# 盘中盯盘：开盘冲高回落 + 急跌幅度 检测器设计

## 背景（为什么做）

用户回放 2026-07-13 中国巨石(600176) 盘中数据，现有规则只在 09:42（大单净额变小）、09:45（价格反转绿转红）、10:21（双重绿+放量）、13:31（放量突破）触发，但**漏掉了两个关键信号**：

1. **开盘时分的最高点** —— 股价开盘冲高至日内高点后回落，现有规则未在高点形成/回落起点给出提示。
2. **立马下杀的风险** —— 开盘高点后的急跌段（09:35–09:45 连续绿柱）未被及时预警；`price_reversal` 只在反转 flip 时（09:45，跌完之后）才触发，`green_streak_sell` 又到 10:21 才触发，均滞后。

根因：`src/monitor.py` 现有 5 条默认规则（`volume_spike_ratio`/`large_flow_surge`/`price_reversal`/`flow_reversal`/`green_streak_sell`）全部基于**方向符号/连击**或**量额突增倍率**，**没有"价格高点"或"价格跌幅幅度/速度"检测**；遗留的 `flow_drop_ratio` 是大单净额跌幅，与价格无关。且每笔 `quote` 只有 `price`/`pre_close`/`change_pct`/`volume`/`large_net_inflow`/`main_net_inflow`，历史只记 `volume_history`/`flow_history`/`large_flow_history`（均 `deque(maxlen=12)`），**没有 price 历史与日内高点追踪** —— 两个新检测器都需要新增状态。

用户已确认语义（经 brainstorming）：
- 开盘高点 → **冲高回落**（开盘 N 分钟内急升创局部高点后反转下跌 → 一条告警同时标记高点价位 + 下杀起点）。理由：实时无法确知某点就是"最高"，唯有"冲高 + 回落"形成后才可判定，且一条复合告警正好覆盖"高点 + 下杀起点"两个诉求。
- 下杀风险 → **急跌幅度**（N 笔内价格跌幅 ≥ X%）。用近期价格窗口而非 `change_pct`（后者基于 `pre_close`，盘中仍涨时的下杀会漏报）。

## 范围

纯后端，仅 `src/monitor.py` + `src/config.py` + `.env.example` + `tests/test_monitor.py`。前端零改动。两个检测器作为**新规则类型**接入既有规则引擎，与现有 5 条默认规则同构（默认注入、config 可调、15 分钟冷却、回放+实时双通道）。

## 既有代码事实（实现依据，已核对）

- `evaluate_quote(self, quote, is_replay=False, replay_time=None)` @ `src/monitor.py:283`。时间基准 `current_time_val`：回放由 `replay_time`（`"%Y-%m-%d %H:%M"`）`strptime` 而来（292-297），实时为 `time.time()`（299）。
- 首笔无 `last_quotes[code]` → 跳过 `if last_quote:` 块（302+）。故**开盘窗口状态须在该块之前捕获**，否则首笔丢失 `open_price`。
- 规则派发 `for rule in rules: if r_type == ...` 位于 `if last_quote:` 块内（389+），现有分支：`volume_spike_ratio`(389)/`flow_drop_ratio`(402)/`large_flow_surge`(409)/`price_reversal`(422)/`flow_reversal`(432)/`green_streak_sell`(442)。
- 默认规则注入 `_inject_default_rules` @ 192，追加 5 条（214-223），由 `_default_rules_enabled` @ 183（读 `AGENT_EVENT_MONITOR_DEFAULT_RULES_ENABLED`）总闸门控制；阈值存入规则 dict（如 `{"type":"volume_spike_ratio","threshold":surge_ratio,"_default":True}`），派发时 `rule.get("threshold")` 读取。`surge_ratio` 来自 config（~213）。
- 冷却：`self.cooldowns[cooldown_key] = current_time_val + self._cooldown_seconds(r_type)` @ 469；`_cooldown_seconds` 统一返回 `900.0`（251-253）。
- 告警发出：回放 → `msg = f"[回放预警触发] {replay_time} {quote.name}({code}): {rule_desc}"` + `logger.info` + `self._replay_triggers.append(msg)`（472-476）；实时 → 构建 `alert_msg` + `notifier.send(content=alert_msg, route_type="alert", severity="high")`（478-487）。
- 回放清理两处：pre-replay 652-661、post-replay 688-697，清 `volume_history`/`flow_history`/`large_flow_history`/`last_quotes`/`prev_price_direction`/`prev_large_sign`/`consecutive_price_green`/`consecutive_flow_green`/`price_dir_run`/`flow_sign_run`。
- `run_replay_simulation` @ 517：逐分钟构造 `UnifiedRealtimeQuote`（668-680），调 `evaluate_quote(sim_quote, is_replay=True, replay_time=time_str)`（683）。
- config 字段模式：`getattr(self.config, "agent_event_monitor_<name>", <default>)`（如 189、258）；`src/config.py` 有 `parse_env_float`/`parse_env_int`/`parse_env_bool`（见 217-269 区段），`.env.example` 以 `AGENT_EVENT_MONITOR_*` 注释变量文档化（含调参建议）。
- 常量：`DEFAULT_GREEN_STREAK_BARS=2`、`DEFAULT_GREEN_STREAK_VOLUME_RATIO=1.5` @ 106-107；`DEFAULT_REVERSAL_BARS` 用于 `_reversal_bars` @ 255-261。

## 设计

### 方案选型（已定）

**原生规则类型**：新增 `open_surge_revert`、`sharp_drop` 两个规则类型，由 `_inject_default_rules` 注入、在 `evaluate_quote` 派发、自带状态 + 15 分钟冷却 + 回放/实时双通道发出。与现有 5 条同构。

否决：(a) 引擎外独立检测函数 —— 破坏规则驱动设计与按规则禁用能力；(b) 重载 `price_reversal`/`flow_drop_ratio` —— 语义混淆，用户要的是清晰独立信号。

### 检测器 1：`open_surge_revert`（开盘冲高回落）

**新增状态**（`RealtimeMonitor.__init__` 内，仿 157-160 的 `defaultdict`/`deque` 模式）：
- `self.open_price: dict[str, float]` —— 开盘窗口首笔价。
- `self.open_high: dict[str, float]` —— 开盘窗口运行最高价。
- `self.open_surge_fired: dict[str, bool]` —— 当日是否已触发（一次性）。
- `self.open_day: dict[str, str]` —— 状态所属日期（`"%Y-%m-%d"`，取自 `current_time_val`），用于跨日重置。

**状态捕获**（`evaluate_quote` 顶部，`if last_quote:` 之前；仅当 `open_surge_revert` 全局启用时执行，避免无谓内存）：
1. 由 `current_time_val` 解析 `HH:MM` 与 `"%Y-%m-%d"`。
2. 跨日重置：若 `open_day[code]` 缺失或 != 今日 → `open_price[code]=quote.price`、`open_high[code]=quote.price`、`open_surge_fired[code]=False`、`open_day[code]=今日`。
3. 仅当 `HH:MM` 落在 `[09:30, 09:30+WINDOW]` 内（窗口内）：`open_high[code] = max(open_high[code], quote.price)`。（`open_price` 仅首笔设定，不再更新。）

**触发**（规则派发 `elif r_type == "open_surge_revert":` 分支，位于 `if last_quote:` 内）：
- 读 `surge_pct`/`revert_pct`/`window`：优先 `rule.get(...)`，回退 config 默认。
- `spike = open_high[code] >= open_price[code] * (1 + surge_pct/100)`
- `revert = (open_high[code] - quote.price) / open_high[code] * 100 >= revert_pct`
- 当 `spike and revert and not open_surge_fired[code]` → 触发：
  - `rule_desc = f"开盘冲高至 {open_high[code]:.2f} 元后回落（跌幅 {(open_high[code]-quote.price)/open_high[code]*100:.2f}%），⚠️ 注意下杀风险"`
  - 置 `open_surge_fired[code]=True`（一次性，当日不再发）。
  - 走既有冷却 + 发出逻辑（468-487）：`cooldown_key` 含 `r_type`，回放走 `[回放预警触发]`，实时走 `notifier.send`。

**语义边界**：开盘即跌（无 spike）→ 不发（交给 `sharp_drop`）；冲高不回落（持续上行）→ 不发；冲高回落再冲高 → 仅发一次（`fired` 标志）。

### 检测器 2：`sharp_drop`（急跌幅度）

**新增状态**：`self.price_history: defaultdict(lambda: deque(maxlen=BARS))` —— 近期价格序列（`BARS` 取 config，默认 5）。

**状态捕获**（`evaluate_quote` 顶部，`if last_quote:` 之前）：`self.price_history[code].append(quote.price)`（每笔都追加，含首笔，保证 N 笔后可回看）。

**触发**（规则派发 `elif r_type == "sharp_drop":` 分支，位于 `if last_quote:` 内）：
- 读 `bars`/`drop_pct`：优先 `rule.get(...)`，回退 config 默认。
- 仅当 `len(price_history[code]) >= bars`：取窗口起点价 `p0 = price_history[code][0]`（`deque(maxlen=bars)` 的最旧元素），`drop_pct_observed = (p0 - quote.price) / p0 * 100`。
- 当 `p0 > 0 and drop_pct_observed >= drop_pct` → 触发：
  - `rule_desc = f"近 {bars} 笔跌幅 {drop_pct_observed:.2f}%（{p0:.2f} → {quote.price:.2f}），⚠️ 急跌"`
  - 走既有冷却（15 分钟，`cooldown_key` 含 `r_type`）+ 发出逻辑。

**用 `price_history[0]` 而非 `change_pct`**：`change_pct` 基于 `pre_close`，盘中仍涨时的下杀会被"全日仍涨"掩盖；近期窗口跌幅才是"急跌"语义。`deque(maxlen=bars)` 保证 `[0]` 恒为 `bars` 笔前的价。

### 配置（`src/config.py` + `.env.example`）

仿 `agent_event_monitor_*` 既有模式（`parse_env_float`/`parse_env_int`/`parse_env_bool`）。两个检测器均受总闸门 `AGENT_EVENT_MONITOR_DEFAULT_RULES_ENABLED` 控制，并各带独立 enable（便于单独关闭）：

| 字段 | env | 默认 | 说明 |
|---|---|---|---|
| `agent_event_monitor_open_surge_revert_enabled` | `AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED` | `True` | 是否注入开盘冲高回落规则 |
| `agent_event_monitor_open_surge_window_minutes` | `AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES` | `15` | 开盘窗口长度（分钟），自 09:30 起 |
| `agent_event_monitor_open_surge_pct` | `AGENT_EVENT_MONITOR_OPEN_SURGE_PCT` | `1.5` | 冲高判定：相对开盘价涨幅 % |
| `agent_event_monitor_open_revert_pct` | `AGENT_EVENT_MONITOR_OPEN_REVERT_PCT` | `0.5` | 回落判定：自高点跌幅 % |
| `agent_event_monitor_sharp_drop_enabled` | `AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED` | `True` | 是否注入急跌幅度规则 |
| `agent_event_monitor_sharp_drop_bars` | `AGENT_EVENT_MONITOR_SHARP_DROP_BARS` | `5` | 急跌回看笔数 |
| `agent_event_monitor_sharp_drop_pct` | `AGENT_EVENT_MONITOR_SHARP_DROP_PCT` | `1.5` | 急跌判定：窗口跌幅 % |

`.env.example` 在 monitor 段补 7 个注释变量，含调参建议（开盘波动大→调大 surge_pct；想更早捕下杀→调小 revert_pct / drop_pct / bars）。

`_inject_default_rules` 追加（仅当对应 enable 为 True，且总闸门开）：
```
{"stock_code": code, "type": "open_surge_revert",
 "surge_pct": <cfg>, "revert_pct": <cfg>, "window": <cfg>, "_default": True}
{"stock_code": code, "type": "sharp_drop",
 "bars": <cfg>, "drop_pct": <cfg>, "_default": True}
```

### 状态重置

- **回放**：将 `price_history`/`open_price`/`open_high`/`open_surge_fired`/`open_day` 的 `.clear()`/`.pop(code,None)` 加入两处清理块（652-661、688-697）。
- **实时**：`price_history` 自截断（`deque(maxlen)`）；`open_*`/`open_day` 由顶部"跨日重置"逻辑处理（日期变更即重置）；冷却复用既有 `self.cooldowns`。

## 测试（`tests/test_monitor.py`，`.venv/Scripts/python.exe tests/test_monitor.py -v`，无 pytest）

沿用既有测试构造 `UnifiedRealtimeQuote` + `RealtimeMonitor` 的模式。新增：

**`open_surge_revert`：**
1. 冲高+回落触发一次：开盘价 10.00 → 冲高至 10.20（+2%≥1.5% spike）→ 回落至 10.10（自高点跌 0.98%≥0.5% revert）→ 触发，`rule_desc` 含 "开盘冲高至 10.20"，`open_surge_fired=True`。
2. 无冲高不触发：开盘后直接下跌（无 spike）→ 不触发。
3. 冲高不回落不触发：冲高后继续上行 → 不触发。
4. 一次性：冲高回落后再冲高回落 → 仅触发 1 次。
5. 跨日重置：同 code 第二日（`open_day` 变）→ `fired` 复位，可再触发。
6. 窗口外不更新 high：10:00 的笔不更新 `open_high`（窗口已过）。

**`sharp_drop`：**
7. N 笔跌幅触发：10.00→9.90→9.85→9.80→9.80（5 笔，自首笔跌 2%≥1.5%）→ 触发，`rule_desc` 含 "近 5 笔跌幅"。
8. 低于阈值不触发：5 笔累计跌 1%（<1.5%）→ 不触发。
9. 不足笔数不触发：仅 3 笔（<5）→ 不触发。
10. 冷却：触发后 15 分钟内同码再达阈值 → 不重复触发。

**回放通道：**
11. `run_replay_simulation` 喂含冲高回落 + 急跌的分钟序列 → `_replay_triggers` 含对应 `[回放预警触发]` 两条；且清理后状态不残留（`price_history[code]` 空）。

## 改动文件清单

- `src/monitor.py`：`__init__` 新增 5 个状态字段；`evaluate_quote` 顶部加 price_history 追加 + open_* 捕获/跨日重置；规则派发加 `open_surge_revert`/`sharp_drop` 两分支；`_inject_default_rules` 加 2 条注入；两处回放清理块加新状态。
- `src/config.py`：7 个字段 + env 读取（`parse_env_bool`/`parse_env_int`/`parse_env_float`）。
- `.env.example`：7 个注释变量 + 调参建议。
- `tests/test_monitor.py`：11 个新用例。

## 非目标（YAGNI）

- 不加多 host / 节流（与东财资金流加固无关，那批已在 `fe1d200d` 入 main）。
- 不改前端告警类型枚举（`AlertType` 是另一套 portfolio 告警系统，与本盘中盯盘无关）。
- 不加非 A 股开盘时间适配（现有 `is_within_trading_hours` 已假定 A 股 09:30）。
- 不为现有 5 条规则补个别 enable 开关（保持现状，仅新检测器带独立 enable）。
- 反转降噪那批改动仍维持用户既有决定，不在本批内。
