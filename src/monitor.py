# -*- coding: utf-8 -*-
r"""
===================================
盘中高频盯盘与智能诊断守护程序
===================================

职责：
1. 从配置拉取规则并维护滑动窗口状态
2. 在对应证券的市场交易时段内执行高频行情轮询
3. 计算区间成交量 $\Delta V$ 和主力资金流向 $\Delta F$ 增量
4. 支持自适应市值计算与多倍率异动波动判定
5. 预警后发送通知，并异步拉起 AI Agent 进行异动原因诊断
"""

import collections
import json
import logging
import threading
import time
from datetime import datetime, time as datetime_time
from typing import Dict, List, Optional

import requests

from data_provider import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote, safe_float
from data_provider.eastmoney_flow import fetch_fflow_klines
from src.config import get_config
from src.core.trading_calendar import get_market_for_stock, is_market_open
from src.notification import NotificationService

logger = logging.getLogger(__name__)


def is_within_trading_hours(stock_code: str) -> bool:
    """
    判断当前是否在指定股票对应市场的交易时段内（考虑本地时区与交易日历）
    """
    market = get_market_for_stock(stock_code)
    if not market:
        return True  # 识别失败时 fail-open

    # 确定目标时区
    tz_name = "US/Eastern" if market == "us" else "Asia/Shanghai"
    try:
        import pytz
        tz = pytz.timezone(tz_name)
    except Exception:
        # 无 pytz 库时降级为时差时区
        from datetime import timezone, timedelta
        tz = timezone(timedelta(hours=-5)) if market == "us" else timezone(timedelta(hours=8))

    local_now = datetime.now(tz)

    # 1. 校验是否为对应市场的交易日
    if not is_market_open(market, local_now.date()):
        return False

    # 2. 校验是否在对应市场的交易时间内
    local_time = local_now.time()
    if market == "cn":
        # A股交易时间：9:15-11:30, 13:00-15:00
        return (datetime_time(9, 15) <= local_time <= datetime_time(11, 30)) or \
               (datetime_time(13, 0) <= local_time <= datetime_time(15, 0))
    elif market == "hk":
        # 港股交易时间：9:30-12:00, 13:00-16:00
        return (datetime_time(9, 30) <= local_time <= datetime_time(12, 0)) or \
               (datetime_time(13, 0) <= local_time <= datetime_time(16, 0))
    elif market == "us":
        # 美股交易时间：9:30-16:00
        return datetime_time(9, 30) <= local_time <= datetime_time(16, 0)

    return True


def parse_alert_rules(rules_json: str) -> List[dict]:
    """
    解析 AGENT_EVENT_ALERT_RULES_JSON 配置，支持列表格式和字典嵌套格式
    """
    if not rules_json or not rules_json.strip():
        return []
    try:
        rules = json.loads(rules_json)
        if isinstance(rules, list):
            return rules
        elif isinstance(rules, dict):
            # 将字典映射格式转换为扁平规则列表
            # {"600118": {"price_upper": 26.5}} -> [{"stock_code": "600118", "type": "price_upper", "threshold": 26.5}]
            flat_rules = []
            for code, rules_dict in rules.items():
                for r_type, val in rules_dict.items():
                    flat_rules.append({
                        "stock_code": code,
                        "type": r_type,
                        "threshold": val
                    })
            return flat_rules
    except Exception as e:
        logger.error(f"[盯盘监控] 解析预警规则 JSON 失败: {e}, 原始配置: {rules_json}")
    return []


# 默认盘中异动判定的放量倍数阈值（相对近2分钟均值），用于未显式配置规则的自选股
DEFAULT_SURGE_RATIO = 3.0
# 默认"连续绿柱卖出"判定：连续绿柱根数 / 放量倍数（相对近几分钟成交量均值）
DEFAULT_GREEN_STREAK_BARS = 2
DEFAULT_GREEN_STREAK_VOLUME_RATIO = 1.5
# 默认红绿反转降噪：前序须连续 N 根同向（红或绿）才视为"有效反转"，过滤单笔价格/大单抖动
DEFAULT_REVERSAL_BARS = 3


class RealtimeMonitor:
    """
    高频盯盘与异动报警服务类
    """
    def __init__(self, config):
        self.config = config
        self.fetcher_mgr = DataFetcherManager()
        
        # 对输入股票池代码进行规范化 (去除首尾空格，对5位数字代码自动补零)
        def clean_and_pad(code: str) -> str:
            c = code.strip()
            # 如果是 5 位全数字代码，且以 '0' 开头，自动补零为 6 位 A 股代码（如 00725 -> 000725）
            if len(c) == 5 and c.startswith("0") and c.isdigit():
                return "0" + c
            return c

        # 加载盯盘列表（去除重复代码）
        raw_list = getattr(config, "stock_list", []) or []
        self.stock_list = []
        for c in raw_list:
            cleaned = clean_and_pad(c)
            if cleaned and cleaned not in self.stock_list:
                self.stock_list.append(cleaned)
        
        # 加载与解析规则
        rules_json = getattr(config, "agent_event_alert_rules_json", "") or ""
        self.rules = parse_alert_rules(rules_json)
        
        # 规范化规则中的股票代码并合并到监控池中
        for rule in self.rules:
            raw_code = rule.get("stock_code")
            if raw_code:
                padded_code = clean_and_pad(raw_code)
                rule["stock_code"] = padded_code
                if padded_code not in self.stock_list:
                    self.stock_list.append(padded_code)

        # 为"未配置任何显式规则"的自选股补充内置默认盘中异动规则（放量/大单异动/红绿反转），
        # 使显著盘中异动可开箱即触；已显式配置规则的股票保持完全由用户规则控制。
        explicit_rule_count = len(self.rules)
        self.rules = self._inject_default_rules(self.rules)

        # 内存运行状态变量
        self.last_quotes: Dict[str, UnifiedRealtimeQuote] = {}
        # 维护最近 12 次区间增量（如果轮询是 10s，约保存 2 分钟的历史波动）
        self.volume_history = collections.defaultdict(lambda: collections.deque(maxlen=12))
        self.flow_history = collections.defaultdict(lambda: collections.deque(maxlen=12))
        # 大单净额区间增量历史，用于大单异动（突然变大/变小）倍数判定
        self.large_flow_history = collections.defaultdict(lambda: collections.deque(maxlen=12))
        # 上一笔的方向/符号状态，用于红绿反转判定（红=涨/净流入，绿=跌/净流出）
        self.prev_price_direction: Dict[str, Optional[str]] = {}
        self.prev_large_sign: Dict[str, Optional[str]] = {}
        # 连续绿柱（价格下跌 / 大单净流出）连击计数，用于"连续绿柱卖出"规则
        self.consecutive_price_green: Dict[str, int] = {}
        self.consecutive_flow_green: Dict[str, int] = {}
        # 红绿反转降噪：截至上一笔的"连续同向"计数（价格方向 / 大单符号），过滤单笔抖动
        self.price_dir_run: Dict[str, int] = {}
        self.flow_sign_run: Dict[str, int] = {}
        
        # 预警冷却字典：避免短时间频繁发送报警，冷却时间默认 15 分钟
        self.cooldowns: Dict[tuple, float] = {}
        self._running = False
        # 停止信号：让 start() 的等待可被立即唤醒，实现响应式停止（替代阻塞式 time.sleep）
        self._stop_event = threading.Event()
        
        default_rule_count = len(self.rules) - explicit_rule_count
        logger.info(
            f"[盯盘监控] 初始化完成。监控股票池大小: {len(self.stock_list)}, "
            f"激活规则数量: {len(self.rules)} (显式 {explicit_rule_count} + 默认 {default_rule_count})"
        )

    def _default_rules_enabled(self) -> bool:
        """是否启用内置默认盘中异动规则（默认开启）。"""
        return bool(getattr(self.config, "agent_event_monitor_default_rules_enabled", True))

    def _green_streak_mode(self) -> str:
        """连续绿柱卖出规则的判定维度：price(仅价格下跌)/flow(仅大单净流出)/both(双重绿，默认)。"""
        mode = str(getattr(self.config, "agent_event_monitor_green_streak_mode", "both") or "both").strip().lower()
        return mode if mode in ("price", "flow", "both") else "both"

    def _inject_default_rules(self, explicit_rules: List[dict]) -> List[dict]:
        """
        为自选池中"未配置任何显式规则"的股票自动补充默认盘中异动规则：
          - volume_spike_ratio : 成交量突然放大（标注当笔红/绿）
          - large_flow_surge   : 大单净额突然变大/变小（标注红/绿）
          - price_reversal     : 价格方向反转（红转绿/绿转红）
          - flow_reversal      : 大单净额反转（红转绿/绿转红）
          - green_streak_sell  : 连续绿柱卖出（连续放量下跌/大单净流出，维度可配 price/flow/both）
        已显式配置规则的股票保持完全由用户规则控制。
        """
        if not self._default_rules_enabled():
            return explicit_rules

        codes_with_explicit = {
            r.get("stock_code") for r in explicit_rules if r.get("stock_code")
        }
        surge_ratio = DEFAULT_SURGE_RATIO

        enriched = list(explicit_rules)
        for code in self.stock_list:
            if code in codes_with_explicit:
                continue
            enriched.append({"stock_code": code, "type": "volume_spike_ratio", "threshold": surge_ratio, "_default": True})
            enriched.append({"stock_code": code, "type": "large_flow_surge", "threshold": surge_ratio, "_default": True})
            enriched.append({"stock_code": code, "type": "price_reversal", "threshold": None, "_default": True})
            enriched.append({"stock_code": code, "type": "flow_reversal", "threshold": None, "_default": True})
            enriched.append({
                "stock_code": code,
                "type": "green_streak_sell",
                "threshold": DEFAULT_GREEN_STREAK_BARS,
                "mode": self._green_streak_mode(),
                "volume_ratio": DEFAULT_GREEN_STREAK_VOLUME_RATIO,
                "_default": True,
            })
        return enriched

    @staticmethod
    def _price_direction(cur_price, prev_price) -> Optional[str]:
        """当笔价格方向：红(涨)/绿(跌)/None(平盘或缺数据)。"""
        if cur_price is None or prev_price is None:
            return None
        if cur_price > prev_price:
            return "红"
        if cur_price < prev_price:
            return "绿"
        return None

    @staticmethod
    def _large_net_sign(large_net_inflow) -> Optional[str]:
        """大单净额符号：红(净流入)/绿(净流出)/None(为零或缺数据)。"""
        if large_net_inflow is None:
            return None
        if large_net_inflow > 0:
            return "红"
        if large_net_inflow < 0:
            return "绿"
        return None

    @staticmethod
    def _cooldown_seconds(r_type: str) -> float:
        """所有规则统一 15 分钟冷却，避免短时间内重复刷屏（含红绿反转类，配合"连续 N 根同向"过滤单笔抖动）。"""
        return 900.0

    def _reversal_bars(self) -> int:
        """红绿反转降噪所需的前序"连续同向"笔数（过滤单笔抖动）。读 config，最低 1。"""
        try:
            val = int(getattr(self.config, "agent_event_monitor_reversal_bars", DEFAULT_REVERSAL_BARS) or DEFAULT_REVERSAL_BARS)
        except (TypeError, ValueError):
            val = DEFAULT_REVERSAL_BARS
        return val if val >= 1 else DEFAULT_REVERSAL_BARS

    def run_check_cycle(self):
        """执行单次轮询检测周期"""
        for code in self.stock_list:
            # 1. 检测对应市场是否开市
            if not is_within_trading_hours(code):
                continue

            try:
                # 2. 拉取实时行情
                quote = self.fetcher_mgr.get_realtime_quote(code, source="tencent")
                if not quote or quote.price is None:
                    continue

                # 3. 评估针对该股票的规则
                self.evaluate_quote(quote)

            except Exception as e:
                logger.error(f"[盯盘监控] 检测股票 {code} 行情或评估预警出错: {e}")
                logger.exception("盯盘错误明细:")

    def evaluate_quote(self, quote, is_replay=False, replay_time=None):
        """
        评估单个行情节点是否触发规则。
        支持 is_replay 模式用于今日历史分钟级数据回放测试。
        """
        code = quote.code
        notifier = NotificationService()
        
        # 确定时间基准
        if is_replay and replay_time:
            try:
                dt = datetime.datetime.strptime(replay_time, "%Y-%m-%d %H:%M")
                current_time_val = dt.timestamp()
            except Exception:
                current_time_val = time.time()
        else:
            current_time_val = time.time()

        last_quote = self.last_quotes.get(code)
        if last_quote:
            # 1. 计算区间成交量 $\Delta V$（以股为单位）与区间主力资金净流入 $\Delta F$（以元为单位）
            delta_v = 0.0
            if quote.volume is not None and last_quote.volume is not None:
                delta_v = float(quote.volume - last_quote.volume)
                if delta_v < 0:
                    delta_v = 0.0

            delta_f = 0.0
            if quote.main_net_inflow is not None and last_quote.main_net_inflow is not None:
                delta_f = float(quote.main_net_inflow - last_quote.main_net_inflow)

            # 区间大单净额增量 $\Delta L$（以元为单位），用于大单异动判定
            delta_large = 0.0
            if quote.large_net_inflow is not None and last_quote.large_net_inflow is not None:
                delta_large = float(quote.large_net_inflow - last_quote.large_net_inflow)

            # 将有交易的数据加入滑动历史均值计算池
            if delta_v > 0:
                self.volume_history[code].append(delta_v)
            if delta_f != 0:
                self.flow_history[code].append(abs(delta_f))
            if delta_large != 0:
                self.large_flow_history[code].append(abs(delta_large))

            # 连续绿柱连击计数（价格下跌 / 大单净流出 各计一路），供"连续绿柱卖出"规则判定
            if self._price_direction(quote.price, last_quote.price) == "绿":
                self.consecutive_price_green[code] = self.consecutive_price_green.get(code, 0) + 1
            else:
                self.consecutive_price_green[code] = 0
            if self._large_net_sign(quote.large_net_inflow) == "绿":
                self.consecutive_flow_green[code] = self.consecutive_flow_green.get(code, 0) + 1
            else:
                self.consecutive_flow_green[code] = 0

            # 2. 评估针对该股票的所有预警规则
            for rule in self.rules:
                if rule.get("stock_code") != code:
                    continue

                r_type = rule.get("type")
                threshold = rule.get("threshold")

                # 冷却校验
                cooldown_key = (code, r_type)
                if current_time_val < self.cooldowns.get(cooldown_key, 0.0):
                    continue

                triggered = False
                rule_desc = ""

                # A. 价格与涨跌幅绝对值预警
                if r_type == "price_upper" and quote.price >= float(threshold):
                    triggered = True
                    rule_desc = f"股价突破上限 {threshold} 元（当前价 {quote.price}）"
                elif r_type == "price_lower" and quote.price <= float(threshold):
                    triggered = True
                    rule_desc = f"股价跌破下限 {threshold} 元（当前价 {quote.price}）"
                elif r_type == "change_pct_upper" and quote.change_pct is not None and quote.change_pct >= float(threshold):
                    triggered = True
                    rule_desc = f"日内涨幅突破 {threshold}%（当前涨幅 {quote.change_pct}%）"
                elif r_type == "change_pct_lower" and quote.change_pct is not None and quote.change_pct <= float(threshold):
                    triggered = True
                    rule_desc = f"日内跌幅超过 {abs(float(threshold))}%（当前涨幅 {quote.change_pct}%）"

                # B. 主力资金绝对净流入预警（支持自适应市值 auto 计算）
                elif r_type in ("main_net_inflow_upper", "main_net_inflow_lower"):
                    if threshold == "auto":
                        circ_mv_yanyi = (quote.circ_mv or 0) / 100000000.0
                        if circ_mv_yanyi <= 0:
                            actual_threshold = 10000000.0 if r_type == "main_net_inflow_upper" else -8000000.0
                        else:
                            if r_type == "main_net_inflow_upper":
                                actual_threshold = 250.0 * (circ_mv_yanyi ** 0.6) * 10000.0
                            else:
                                actual_threshold = -200.0 * (circ_mv_yanyi ** 0.6) * 10000.0
                    else:
                        actual_threshold = float(threshold)

                    if r_type == "main_net_inflow_upper" and quote.main_net_inflow is not None and quote.main_net_inflow >= actual_threshold:
                        triggered = True
                        rule_desc = f"今日主力资金净流入突破预警线 {actual_threshold / 10000.0:.2f} 万元（当前净流入 {quote.main_net_inflow / 10000.0:.2f} 万元）"
                    elif r_type == "main_net_inflow_lower" and quote.main_net_inflow is not None and quote.main_net_inflow <= actual_threshold:
                        triggered = True
                        rule_desc = f"今日主力资金净流出突破预警线 {abs(actual_threshold) / 10000.0:.2f} 万元（当前净流入 {quote.main_net_inflow / 10000.0:.2f} 万元）"

                # C. 区间倍数异动波动判定（至少积累 3 次区间增量历史以防止启动噪声）
                elif r_type == "volume_spike_ratio" and len(self.volume_history[code]) >= 3:
                    avg_v = sum(self.volume_history[code]) / len(self.volume_history[code])
                    if avg_v > 0 and delta_v >= float(threshold) * avg_v and delta_v >= 20000:
                        triggered = True
                        _color = self._price_direction(quote.price, last_quote.price) or "平"
                        rule_desc = f"区间成交量放量突破 {threshold} 倍（当前成交 {delta_v / 100:.0f} 手，近2分钟均值 {avg_v / 100:.0f} 手），当笔{_color}"

                elif r_type == "flow_spike_ratio" and len(self.flow_history[code]) >= 3:
                    avg_f = sum(self.flow_history[code]) / len(self.flow_history[code])
                    if avg_f > 0 and delta_f >= float(threshold) * avg_f and delta_f >= 100000:
                        triggered = True
                        rule_desc = f"区间主力资金净买入突破 {threshold} 倍（当前净买入 {delta_f / 10000.0:.2f} 万元，近2分钟均值 {avg_f / 10000.0:.2f} 万元）"

                elif r_type == "flow_drop_ratio" and len(self.flow_history[code]) >= 3:
                    avg_f = sum(self.flow_history[code]) / len(self.flow_history[code])
                    if avg_f > 0 and delta_f <= -float(threshold) * avg_f and delta_f <= -100000:
                        triggered = True
                        rule_desc = f"区间主力资金砸盘流出突破 {threshold} 倍（当前净流入 {delta_f / 10000.0:.2f} 万元，近2分钟均值 {avg_f / 10000.0:.2f} 万元）"

                # D. 大单净额突然变大/变小（标注红/绿）
                elif r_type == "large_flow_surge" and len(self.large_flow_history[code]) >= 3:
                    avg_lf = sum(self.large_flow_history[code]) / len(self.large_flow_history[code])
                    if avg_lf > 0 and abs(delta_large) >= float(threshold) * avg_lf and abs(delta_large) >= 100000:
                        triggered = True
                        _dir = "变大" if delta_large > 0 else "变小"
                        _sign = self._large_net_sign(quote.large_net_inflow) or "平"
                        rule_desc = (
                            f"大单净额突然{_dir} {abs(delta_large) / 10000.0:.2f} 万元"
                            f"（{threshold} 倍于近2分钟均值），当前{_sign}"
                            f"（大单净额 {(quote.large_net_inflow or 0) / 10000.0:.2f} 万元）"
                        )

                # E. 价格方向反转（红转绿/绿转红）：仅当前序已连续 N 根同向（过滤单笔抖动）时才视为有效反转
                elif r_type == "price_reversal":
                    cur_dir = self._price_direction(quote.price, last_quote.price)
                    prev_dir = self.prev_price_direction.get(code)
                    _run = self.price_dir_run.get(code, 0)
                    if prev_dir and cur_dir and cur_dir != prev_dir and _run >= self._reversal_bars():
                        triggered = True
                        _flip = "红转绿" if prev_dir == "红" else "绿转红"
                        rule_desc = f"价格方向反转 {_flip}（前 {_run} 笔{prev_dir} → 本笔{cur_dir}）"

                # F. 大单净额反转（红转绿/绿转红）：仅当前序已连续 N 笔同向（过滤单笔抖动）时才视为有效反转
                elif r_type == "flow_reversal":
                    cur_sign = self._large_net_sign(quote.large_net_inflow)
                    prev_sign = self.prev_large_sign.get(code)
                    _run = self.flow_sign_run.get(code, 0)
                    if prev_sign and cur_sign and cur_sign != prev_sign and _run >= self._reversal_bars():
                        triggered = True
                        _flip = "红转绿" if prev_sign == "红" else "绿转红"
                        rule_desc = f"大单净额反转 {_flip}（前 {_run} 笔{prev_sign} → 本笔{cur_sign}）"

                # G. 连续绿柱卖出（连续放量下跌 / 大单净流出；维度 mode=price/flow/both）
                elif r_type == "green_streak_sell":
                    _bars = int(threshold) if threshold else DEFAULT_GREEN_STREAK_BARS
                    _mode = str(rule.get("mode") or "both").strip().lower()
                    if _mode not in ("price", "flow", "both"):
                        _mode = "both"
                    _vol_ratio = float(rule.get("volume_ratio") or DEFAULT_GREEN_STREAK_VOLUME_RATIO)
                    _p_streak = self.consecutive_price_green.get(code, 0)
                    _f_streak = self.consecutive_flow_green.get(code, 0)
                    _hist_len = len(self.volume_history[code])
                    _avg_v = sum(self.volume_history[code]) / _hist_len if _hist_len >= 2 else 0.0
                    _volume_ok = _avg_v > 0 and delta_v >= _vol_ratio * _avg_v
                    _hit, _detail = False, ""
                    if _mode == "price" and _p_streak >= _bars:
                        _hit, _detail = True, f"连续 {_p_streak} 根价格绿柱(每分钟下跌)"
                    elif _mode == "flow" and _f_streak >= _bars:
                        _hit, _detail = True, f"连续 {_f_streak} 根大单净流出(主力出货)"
                    elif _mode == "both" and _p_streak >= _bars and _f_streak >= _bars:
                        _hit, _detail = True, f"连续 {_p_streak} 根价格绿柱 + {_f_streak} 根大单净流出(双重绿)"
                    if _hit and _volume_ok:
                        triggered = True
                        rule_desc = (
                            f"{_detail} 且放量(区间成交 {delta_v / 100:.0f} 手，为近均值的 "
                            f"{delta_v / _avg_v:.1f} 倍) → ⚠️ 注意卖出止盈/止损"
                        )

                # 3. 触发预警逻辑
                if triggered:
                    self.cooldowns[cooldown_key] = current_time_val + self._cooldown_seconds(r_type)

                    if is_replay:
                        msg = f"[回放预警触发] {replay_time} {quote.name}({code}): {rule_desc}"
                        logger.info(msg)
                        if not hasattr(self, "_replay_triggers"):
                            self._replay_triggers = []
                        self._replay_triggers.append(msg)
                    else:
                        alert_msg = (
                            f"🚨【盯盘预警】{quote.name}({code}) 触发实时异动预警！\n"
                            f"────────────────────\n"
                            f"🔔 异动原因: {rule_desc}\n"
                            f"📈 最新股价: {quote.price} 元 (昨收: {quote.pre_close} 元, 涨跌幅: {quote.change_pct}%)\n"
                            f"💰 今日主力净买入: {(quote.main_net_inflow or 0.0) / 10000.0:.2f} 万元 (大单: {(quote.large_net_inflow or 0.0) / 10000.0:.2f} 万元)\n"
                            f"📊 区间波动: 区间成交量 {delta_v / 100:.0f} 手，区间主力流向 {delta_f / 10000.0:.2f} 万元。"
                        )
                        logger.info(f"[盯盘预警] 股票 {code} 触发规则 {r_type}，发送警报。")
                        notifier.send(content=alert_msg, route_type="alert", severity="high")

                        if getattr(self.config, "agent_mode", False):
                            threading.Thread(
                                target=self._run_async_diagnosis,
                                args=(code, quote.name, quote.price, rule_desc, alert_msg),
                                daemon=True
                            ).start()

        # 更新方向/符号状态及"连续同向"计数，供下一笔反转判定（仅在存在前序行情时）
        if last_quote:
            _cur_dir = self._price_direction(quote.price, last_quote.price)
            if _cur_dir:
                _prev_dir = self.prev_price_direction.get(code)
                if _prev_dir and _cur_dir == _prev_dir:
                    self.price_dir_run[code] = self.price_dir_run.get(code, 1) + 1
                else:
                    self.price_dir_run[code] = 1
                self.prev_price_direction[code] = _cur_dir
            _cur_sign = self._large_net_sign(quote.large_net_inflow)
            if _cur_sign:
                _prev_sign = self.prev_large_sign.get(code)
                if _prev_sign and _cur_sign == _prev_sign:
                    self.flow_sign_run[code] = self.flow_sign_run.get(code, 1) + 1
                else:
                    self.flow_sign_run[code] = 1
                self.prev_large_sign[code] = _cur_sign

        self.last_quotes[code] = quote

    def run_replay_simulation(self):
        """
        在非交易时段，从 9:30 开始回放今天的全天分钟级历史数据，
        用于验证和调试预警规则是否会触发。
        """
        logger.info("=" * 60)
        logger.info("进入今日盘中历史分钟级数据回放模拟测试 (09:30 - 15:00)")
        logger.info("=" * 60)

        self._replay_triggers = []

        for code in self.stock_list:
            if not (code.isdigit() and len(code) == 6):
                logger.info(f"[回放跳过] {code} 暂不支持回放模拟")
                continue

            # A. 拉取最新行情作为基础静态信息 (包括 name, pre_close, circ_mv)
            try:
                latest_quote = self.fetcher_mgr.get_realtime_quote(code)
            except Exception as e:
                logger.error(f"[回放] 拉取 {code} 静态行情失败: {e}")
                continue

            if not latest_quote:
                logger.warning(f"[回放] 未获取到 {code} 行情数据，跳过")
                continue

            name = latest_quote.name
            pre_close = latest_quote.pre_close
            circ_mv = latest_quote.circ_mv

            if not pre_close or pre_close <= 0:
                logger.warning(f"[回放] {code} 昨收价无效: {pre_close}，跳过")
                continue

            logger.info(f"正在拉取 {name}({code}) 今日分钟级历史行情与资金流向...")

            secid = f"1.{code}" if code.startswith(("6", "5", "9")) else f"0.{code}"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            # B. 拉取资金流向分钟数据（http 绕过代理；节流/退避/熔断集中在 eastmoney_flow）
            flow_map = {}
            raw_klines = fetch_fflow_klines(secid, timeout=10.0)
            if raw_klines:
                logger.info(f"[东财资金流向数据样例] 样本长度: {len(raw_klines)}, 前3条: {[','.join(p) for p in raw_klines[:3]]}")
                if getattr(self.config, "agent_event_monitor_replay_debug", False):
                    logger.info(f"[回放-原始] 资金流向 klines 全量({len(raw_klines)}条): {[','.join(p) for p in raw_klines]}")
                for parts in raw_klines:
                    if len(parts) >= 6:
                        flow_map[parts[0]] = {
                            "main_net_inflow": float(parts[1]),
                            "large_net_inflow": float(parts[4]),
                            "super_large_net_inflow": float(parts[5]),
                        }
            else:
                logger.error(f"[回放] 拉取 {code} 资金流向失败（东财拒绝或熔断）")
                logger.error("[提示] 访问东财接口被拒。若开启了 VPN 或代理软件（如 Clash 开启系统代理/TUN模式），请尝试切换为\"直连 (Direct)\"模式或关闭代理后再试。")
                continue

            # C. 拉取分时趋势分钟数据 (使用 http 协议绕过代理)
            trends_list = []
            try:
                trend_url = "http://push2his.eastmoney.com/api/qt/stock/trends2/get"
                trend_params = {
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                    "ut": "b2884a393a59ad64002292a3e90d46a5"
                }
                r = requests.get(trend_url, params=trend_params, headers=headers, proxies={"http": None, "https": None}, timeout=10)
                if r.status_code == 200:
                    json_data = r.json()
                    logger.info(f"[东财分时趋势接口返回] 状态码: {r.status_code}, 键值: {list(json_data.keys())}")
                    trends_list = json_data.get("data", {}).get("trends", []) if json_data.get("data") else []
                    logger.info(f"[东财分时趋势数据样例] 样本长度: {len(trends_list)}, 前3条数据: {trends_list[:3]}")
                    if getattr(self.config, "agent_event_monitor_replay_debug", False):
                        logger.info(f"[回放-原始] 分时 trends 全量({len(trends_list)}条): {trends_list}")
            except Exception as e:
                logger.error(f"[回放] 拉取 {code} 分时趋势失败: {e}")
                logger.error("[提示] 访问东财接口被拒。若开启了 VPN 或代理软件（如 Clash 开启系统代理/TUN模式），请尝试切换为“直连 (Direct)”模式或关闭代理后再试。")
                continue

            if not trends_list:
                logger.warning(f"[回放] {code} 今日暂无分钟级趋势数据")
                continue

            # D. 对齐匹配分钟数据
            matched_quotes = []
            cumulative_vol = 0.0

            for trend in trends_list:
                parts = trend.split(",")
                if len(parts) < 8:
                    continue
                time_str = parts[0] # "YYYY-MM-DD HH:MM"

                time_part = time_str.split(" ")[1] if " " in time_str else ""
                if not time_part:
                    continue
                if not (("09:30" <= time_part <= "11:30") or ("13:00" <= time_part <= "15:00")):
                    continue

                price = float(parts[2]) # 当前收盘价
                vol_of_minute = float(parts[5]) * 100.0 # volume in 手 * 100
                cumulative_vol += vol_of_minute

                flow_data = flow_map.get(time_str, {
                    "main_net_inflow": 0.0,
                    "large_net_inflow": 0.0,
                    "super_large_net_inflow": 0.0,
                })

                matched_quotes.append({
                    "time": time_str,
                    "price": price,
                    "volume": cumulative_vol,
                    "main_net_inflow": flow_data["main_net_inflow"],
                    "large_net_inflow": flow_data["large_net_inflow"],
                    "super_large_net_inflow": flow_data["super_large_net_inflow"],
                })

            logger.info(f"[回放] {name}({code}) 数据匹配完成，匹配到 {len(matched_quotes)} 条分钟级行情。开始回放比对...")

            if getattr(self.config, "agent_event_monitor_replay_debug", False):
                logger.info(f"[回放-原始] flow_map({len(flow_map)}条): {flow_map}")
                for _q in matched_quotes:
                    logger.info(
                        f"[回放-明细] {_q['time']} 价={_q['price']} 累计量={_q['volume'] / 100:.0f}手 "
                        f"主力净额={_q['main_net_inflow'] / 10000.0:.2f}万 大单净额={_q['large_net_inflow'] / 10000.0:.2f}万"
                    )

            # E. 初始化该股票的回放队列环境，清除缓存
            self.volume_history[code].clear()
            self.flow_history[code].clear()
            self.large_flow_history[code].clear()
            self.last_quotes.pop(code, None)
            self.prev_price_direction.pop(code, None)
            self.prev_large_sign.pop(code, None)
            self.consecutive_price_green.pop(code, None)
            self.consecutive_flow_green.pop(code, None)
            self.price_dir_run.pop(code, None)
            self.flow_sign_run.pop(code, None)

            # F. 开始回放计算
            stock_triggers = 0
            for item in matched_quotes:
                time_str = item["time"]

                sim_quote = UnifiedRealtimeQuote(
                    code=code,
                    name=name,
                    source=latest_quote.source,
                    price=item["price"],
                    volume=item["volume"],
                    main_net_inflow=item["main_net_inflow"],
                    large_net_inflow=item["large_net_inflow"],
                    super_large_net_inflow=item["super_large_net_inflow"],
                    change_pct=round((item["price"] - pre_close) / pre_close * 100, 2) if pre_close else 0.0,
                    pre_close=pre_close,
                    circ_mv=circ_mv
                )

                prev_triggers = len(self._replay_triggers)
                self.evaluate_quote(sim_quote, is_replay=True, replay_time=time_str)
                if len(self._replay_triggers) > prev_triggers:
                    stock_triggers += 1

            # G. 清理该股票的历史缓存，防止污染实时内存
            self.volume_history[code].clear()
            self.flow_history[code].clear()
            self.large_flow_history[code].clear()
            self.last_quotes.pop(code, None)
            self.prev_price_direction.pop(code, None)
            self.prev_large_sign.pop(code, None)
            self.consecutive_price_green.pop(code, None)
            self.consecutive_flow_green.pop(code, None)
            self.price_dir_run.pop(code, None)
            self.flow_sign_run.pop(code, None)

            logger.info(f"[回放] {name}({code}) 回放测试完成！触发预警次数: {stock_triggers}")

        logger.info("=" * 60)
        logger.info("今日分钟数据回放测试总结：")
        if self._replay_triggers:
            for trigger_log in self._replay_triggers:
                logger.info(f"  {trigger_log}")
        else:
            logger.info("  今日分钟级回放中没有触发任何监控规则。")
        logger.info("=" * 60)

    def _run_async_diagnosis(self, code: str, name: str, price: float, rule_desc: str, alert_detail: str):
        """
        后台异步调用 ReAct Agent 开展异动原因分析并追更推送
        """
        logger.info(f"[异步诊断] 开始对异动股 {name}({code}) 开展智能研判分析...")
        try:
            from src.agent.factory import build_agent_executor
            import uuid
            
            # 1. 组装 AI 诊断提示词
            prompt = (
                f"个股 {name}({code}) 在盘中发生异动并触发了实时预警。异动原因为：{rule_desc}。\n"
                f"当前最新价为 {price} 元。\n"
                f"详细的异动情况如下：\n{alert_detail}\n\n"
                f"请使用 search_stock_news 检索该股票最近 24 小时内的所有重大媒体新闻、公告或异动信息。\n"
                f"请严格用三句话分析主力异动的根源原因（如果是资金流向/放量原因请结合消息面和板块效应解析），并给出一个明确的防守/应对盘中操作建议。"
            )

            # 2. 解析配置和默认问股技能
            from bot.commands.ask import AskCommand
            cmd = AskCommand()
            default_skill = cmd._get_default_skill_id()

            # 3. 创建单次独立的 Agent 执行会话
            executor = build_agent_executor(self.config, skills=[default_skill] if default_skill else None)
            session_id = f"monitor_diag_{code}_{uuid.uuid4()}"
            
            # 4. 执行 AI 分析
            result = executor.chat(message=prompt, session_id=session_id)
            if result.success:
                diag_msg = (
                    f"🤖【AI 异动研判 - {name}({code})】\n"
                    f"────────────────────\n"
                    f"{result.content}"
                )
                # 5. 回复推送至通知通道
                notifier = NotificationService()
                notifier.send(content=diag_msg, route_type="alert", severity="info")
                logger.info(f"[异步诊断] {name}({code}) 诊断完成并已推送。")
            else:
                logger.error(f"[异步诊断] {name}({code}) Agent 诊断执行未成功: {result.error}")
        except Exception as e:
            logger.error(f"[异步诊断] 分析股票 {code} 出现异常: {e}")
            logger.exception("诊断分析错误明细:")

    def start(self):
        """启动监控守护循环"""
        self._running = True
        self._stop_event.clear()
        # 默认 5 分钟，如果用户配置为小数（如 0.16 则是 10 秒），读取并转换
        interval_min = getattr(self.config, "agent_event_monitor_interval_minutes", 5.0) or 5.0
        interval_seconds = max(int(interval_min * 60), 5) # 最小间隔 5 秒以防过载东财接口

        logger.info(f"[盯盘监控] 后台盯盘守护进程已启动，轮询间隔: {interval_seconds} 秒")

        while self._running:
            self.run_check_cycle()
            # 用 Event.wait 替代 time.sleep：stop() 触发 set() 后立即唤醒退出，无需等到下个轮询
            if self._stop_event.wait(timeout=interval_seconds):
                break
        logger.info("[盯盘监控] 后台盯盘守护循环已退出。")

    def stop(self):
        """关闭盯盘服务"""
        self._running = False
        self._stop_event.set()  # 立即唤醒可能在 wait 的 start() 循环
        logger.info("[盯盘监控] 盯盘守护服务已请求停止。")


# ── 进程内盯盘实例注册表 ──────────────────────────────────────────────
# 用于 WebUI/API 运行时启停：保存当前后台盯盘的 monitor 实例与线程句柄。
# main.py 的自动启动与 stop_monitor_thread()/get_monitor_status() 共享同一份注册表，
# 确保全局只有一个活跃盯盘实例。
_monitor_state: Dict[str, object] = {"monitor": None, "thread": None}
_monitor_state_lock = threading.Lock()


def start_monitor_thread(config) -> Optional[threading.Thread]:
    """
    外部启动入口：拉起后台监控守护线程。
    若已有运行中的盯盘实例，先停止旧的再用最新 config 重建，保证全局唯一活跃实例。
    """
    stop_monitor_thread()  # 先停旧的，避免重复实例
    try:
        monitor = RealtimeMonitor(config)
        t = threading.Thread(target=monitor.start, name="RealtimeMonitorThread", daemon=True)
        with _monitor_state_lock:
            _monitor_state["monitor"] = monitor
            _monitor_state["thread"] = t
        t.start()
        logger.info("[盯盘监控] 已通过注册表拉起后台盯盘线程。")
        return t
    except Exception as e:
        logger.error(f"[盯盘监控] 启动盘中监控守护线程失败: {e}")
        logger.exception("监控启动错误明细:")
        return None


def stop_monitor_thread() -> bool:
    """
    停止当前后台盯盘实例（若存在）。返回是否曾存在并请求了停止。
    不阻塞等待线程退出：守护线程会在当前轮询结束或被 Event 唤醒后自行退出。
    """
    with _monitor_state_lock:
        monitor = _monitor_state.get("monitor")
    if monitor is None:
        return False
    try:
        monitor.stop()
    except Exception as e:
        logger.error(f"[盯盘监控] 停止盯盘实例出错: {e}")
    with _monitor_state_lock:
        # 仅当注册表仍指向同一实例时才清空，避免清掉刚刚启动的新实例
        if _monitor_state.get("monitor") is monitor:
            _monitor_state["monitor"] = None
            _monitor_state["thread"] = None
    logger.info("[盯盘监控] 已请求停止后台盯盘线程并清理注册表。")
    return True


def get_monitor_status() -> dict:
    """返回当前后台盯盘实例的运行状态，供 WebUI/API 展示。"""
    with _monitor_state_lock:
        monitor = _monitor_state.get("monitor")
        thread = _monitor_state.get("thread")
    if monitor is None:
        return {
            "running": False,
            "thread_alive": False,
            "stock_list": [],
            "interval_seconds": 0,
            "rules_count": 0,
        }
    interval_min = getattr(monitor.config, "agent_event_monitor_interval_minutes", 5.0) or 5.0
    interval_seconds = max(int(interval_min * 60), 5)
    return {
        "running": bool(getattr(monitor, "_running", False)),
        "thread_alive": bool(thread is not None and thread.is_alive()),
        "stock_list": list(getattr(monitor, "stock_list", []) or []),
        "interval_seconds": interval_seconds,
        "rules_count": len(getattr(monitor, "rules", []) or []),
    }
