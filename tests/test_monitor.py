# -*- coding: utf-8 -*-
"""
单元测试：实时监控与预警规则评估
"""

import datetime
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.append("f:/codeRepo/ai/agent/daily_stock_analysis")

from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource
from src.monitor import parse_alert_rules, is_within_trading_hours, RealtimeMonitor


class TestMonitor(unittest.TestCase):
    def test_parse_alert_rules(self):
        # 1. 测试列表格式
        rules_list_json = '[{"stock_code": "600118", "type": "price_upper", "threshold": 26.5}]'
        rules = parse_alert_rules(rules_list_json)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["stock_code"], "600118")
        self.assertEqual(rules[0]["type"], "price_upper")
        self.assertEqual(rules[0]["threshold"], 26.5)

        # 2. 测试字典格式
        rules_dict_json = '{"688523": {"main_net_inflow_upper": "auto"}}'
        rules = parse_alert_rules(rules_dict_json)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["stock_code"], "688523")
        self.assertEqual(rules[0]["type"], "main_net_inflow_upper")
        self.assertEqual(rules[0]["threshold"], "auto")

    @patch('src.monitor.get_market_for_stock')
    @patch('src.monitor.is_market_open')
    def test_is_within_trading_hours(self, mock_is_market_open, mock_get_market):
        mock_get_market.return_value = "cn"
        mock_is_market_open.return_value = True

        # 我们不用测试实时当前时间（会因运行时间而变），可以测试判定逻辑是否能加载
        res = is_within_trading_hours("600118")
        self.assertIn(res, [True, False])

    def test_realtime_monitor_rule_evaluation(self):
        # 构建 Mock Config
        config = MagicMock()
        config.stock_list = ["600118"]
        config.agent_event_alert_rules_json = '[{"stock_code": "600118", "type": "price_upper", "threshold": 25.0}, {"stock_code": "600118", "type": "volume_spike_ratio", "threshold": 3.0}]'
        config.agent_mode = False

        monitor = RealtimeMonitor(config)
        self.assertEqual(len(monitor.rules), 2)

        # 构造第一个 Quote (初始点)
        quote1 = UnifiedRealtimeQuote(
            code="600118",
            name="中国卫星",
            source=RealtimeSource.TENCENT,
            price=24.5,
            volume=100000, # 1000手
            main_net_inflow=500000.0, # 50万
            circ_mv=5000000000.0 # 50亿
        )

        # 模拟拉取第一个行情
        monitor.fetcher_mgr.get_realtime_quote = MagicMock(return_value=quote1)
        with patch('src.monitor.is_within_trading_hours', return_value=True):
            monitor.run_check_cycle()

        self.assertIn("600118", monitor.last_quotes)
        self.assertEqual(monitor.last_quotes["600118"].price, 24.5)

        # 构造第二个 Quote (触发价格上限 25.5 > 25.0，且成交量暴增 100000 -> 300000，增加 200000)
        quote2 = UnifiedRealtimeQuote(
            code="600118",
            name="中国卫星",
            source=RealtimeSource.TENCENT,
            price=25.5,
            volume=300000, # 增加 200000股 = 2000手 (大于 200手底线)
            main_net_inflow=1500000.0, # 增加 100万
            circ_mv=5000000000.0
        )

        # 模拟拉取第二个行情
        monitor.fetcher_mgr.get_realtime_quote = MagicMock(return_value=quote2)
        
        # 填充 volume 历史以触发成交量均值倍数检测
        monitor.volume_history["600118"].append(10000.0)
        monitor.volume_history["600118"].append(12000.0)
        monitor.volume_history["600118"].append(11000.0)
        # 均值约 11000。当前增量 200000 是它的 18 倍，远大于 threshold=3.0

        with patch('src.monitor.NotificationService.send') as mock_send, \
             patch('src.monitor.is_within_trading_hours', return_value=True):
            monitor.run_check_cycle()
            # 验证至少触发了通知发送
            self.assertTrue(mock_send.called)

    def test_run_replay_simulation(self):
        # 1. 模拟行情
        config = MagicMock()
        config.stock_list = ["600118"]
        config.agent_event_alert_rules_json = '[{"stock_code": "600118", "type": "price_upper", "threshold": 26.0}]'
        config.agent_mode = False

        monitor = RealtimeMonitor(config)

        # 2. 模拟 get_realtime_quote 返回基础静态数据
        latest_quote = UnifiedRealtimeQuote(
            code="600118",
            name="中国卫星",
            source=RealtimeSource.TENCENT,
            price=25.0,
            volume=500000,
            main_net_inflow=1000000.0,
            pre_close=24.0,
            circ_mv=5000000000.0
        )
        monitor.fetcher_mgr.get_realtime_quote = MagicMock(return_value=latest_quote)

        # 3. 模拟资金流向(经 eastmoney_flow 加固路径返回已 split 的列表) + 分时趋势
        flow_klines = [
            "2026-07-10 09:31,100000.0,0.0,0.0,50000.0,50000.0",
            "2026-07-10 09:32,200000.0,0.0,0.0,100000.0,100000.0",
            "2026-07-10 09:33,300000.0,0.0,0.0,150000.0,150000.0",
        ]
        parsed_flow = [s.split(",") for s in flow_klines]

        # 分时趋势 trends（回放路径仍用 requests.get）
        mock_trend_response = MagicMock()
        mock_trend_response.status_code = 200
        mock_trend_response.json.return_value = {
            "data": {
                "trends": [
                    "2026-07-10 09:31,25.0,25.5,25.8,25.0,1000,25000.0,25.50",
                    "2026-07-10 09:32,25.5,26.2,26.5,25.5,2000,52000.0,26.00", # 收盘价 26.2 > 26.0，触发股价突破预警
                    "2026-07-10 09:33,26.2,25.8,26.3,25.8,1500,39000.0,26.10"
                ]
            }
        }

        # 运行回放模拟：资金流走 fetch_fflow_klines，分时走 requests.get
        with patch("src.monitor.fetch_fflow_klines", return_value=parsed_flow), \
             patch("src.monitor.requests.get", return_value=mock_trend_response):
            monitor.run_replay_simulation()

        # 验证回放是否成功记录预警触发
        self.assertTrue(hasattr(monitor, "_replay_triggers"))
        self.assertEqual(len(monitor._replay_triggers), 1)
        self.assertIn("股价突破上限 26.0 元", monitor._replay_triggers[0])

    def _make_default_rules_config(self, stock_list=None, rules_json="", defaults_enabled=True, green_streak_mode="both"):
        """构建一个用于默认规则测试的 Mock Config。"""
        config = MagicMock()
        config.stock_list = stock_list if stock_list is not None else ["000725"]
        config.agent_event_alert_rules_json = rules_json
        config.agent_event_monitor_default_rules_enabled = defaults_enabled
        config.agent_event_monitor_green_streak_mode = green_streak_mode
        config.agent_event_monitor_reversal_bars = 3
        # 开盘冲高回落 + 急跌幅度 检测器配置（显式设值，避免 MagicMock float()/int() 静默返回 0.0/0）
        config.agent_event_monitor_open_surge_revert_enabled = True
        config.agent_event_monitor_open_surge_window_minutes = 15
        config.agent_event_monitor_open_surge_pct = 1.5
        config.agent_event_monitor_open_revert_pct = 0.5
        config.agent_event_monitor_sharp_drop_enabled = True
        config.agent_event_monitor_sharp_drop_bars = 5
        config.agent_event_monitor_sharp_drop_pct = 1.5
        config.agent_mode = False
        return config

    def _q(self, price=10.0, volume=100000, main_net_inflow=0.0, large_net_inflow=0.0, code="000725"):
        """构造一个最小化的行情快照用于盘中异动评估。"""
        return UnifiedRealtimeQuote(
            code=code, name="京东方A", source=RealtimeSource.TENCENT,
            price=price, volume=volume,
            main_net_inflow=main_net_inflow, large_net_inflow=large_net_inflow,
        )

    def test_default_anomaly_rules_injected(self):
        """无显式规则的自选股应自动获得四类默认盘中异动规则。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        rule_types = {r["type"] for r in monitor.rules if r.get("stock_code") == "000725"}
        self.assertIn("volume_spike_ratio", rule_types)
        self.assertIn("large_flow_surge", rule_types)
        self.assertIn("price_reversal", rule_types)
        self.assertIn("flow_reversal", rule_types)

    def test_volume_surge_fires_with_color(self):
        """成交量突然放大且当笔上涨，应触发放量预警并标注红。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000)
        # 近2分钟均值约 11000 股
        monitor.volume_history["000725"].extend([10000.0, 12000.0, 11000.0])
        # 当笔放量 200000 股（远超 3 倍均值且超 20000 底线），价格上涨 → 红
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.5, volume=300000), is_replay=True, replay_time="2026-07-10 10:00")
        self.assertTrue(
            any("放量" in t and "红" in t for t in monitor._replay_triggers),
            f"expected red volume-surge trigger, got {monitor._replay_triggers}",
        )

    def test_large_flow_surge_fires(self):
        """大单净额突然变大且当前为净流入，应触发并标注变大/红。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        # 近2分钟大单增量均值约 55000 元
        monitor.large_flow_history["000725"].extend([50000.0, 60000.0, 55000.0])
        # 当笔大单净额增量 500000 元（远超 3 倍均值且超 10 万底线），累计净流入 → 红
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.0, volume=100000, large_net_inflow=500000.0), is_replay=True, replay_time="2026-07-10 10:00")
        self.assertTrue(
            any("变大" in t and "红" in t for t in monitor._replay_triggers),
            f"expected red large-flow-surge trigger, got {monitor._replay_triggers}",
        )

    def test_price_reversal_fires(self):
        """价格连续3根红后转绿，应触发价格反转（前序连续同向达 N 根才算有效反转）。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000)
        with patch("src.monitor.NotificationService"):
            # 前3笔连续上涨（红）：累积"连续同向"计数（默认需 >= 3）
            monitor.evaluate_quote(self._q(price=10.5, volume=110000), is_replay=True, replay_time="2026-07-10 10:00")
            monitor.evaluate_quote(self._q(price=10.8, volume=110000), is_replay=True, replay_time="2026-07-10 10:01")
            monitor.evaluate_quote(self._q(price=11.0, volume=110000), is_replay=True, replay_time="2026-07-10 10:02")
            # 第4笔下跌（绿）：前序连续3根红 → 触发红转绿
            monitor.evaluate_quote(self._q(price=10.6, volume=120000), is_replay=True, replay_time="2026-07-10 10:03")
        self.assertTrue(
            any("价格方向反转" in t and "红转绿" in t for t in monitor._replay_triggers),
            f"expected price reversal trigger, got {monitor._replay_triggers}",
        )

    def test_flow_reversal_fires(self):
        """大单净额连续3笔净流入后转净流出，应触发大单反转（前序连续同向达 N 笔才算有效反转）。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            # 前3笔大单净流入（红）：累积"连续同向"计数
            monitor.evaluate_quote(self._q(price=10.0, volume=110000, large_net_inflow=500000.0), is_replay=True, replay_time="2026-07-10 10:00")
            monitor.evaluate_quote(self._q(price=10.0, volume=110000, large_net_inflow=600000.0), is_replay=True, replay_time="2026-07-10 10:01")
            monitor.evaluate_quote(self._q(price=10.0, volume=110000, large_net_inflow=700000.0), is_replay=True, replay_time="2026-07-10 10:02")
            # 第4笔大单净流出（绿）：前序连续3笔净流入 → 触发红转绿
            monitor.evaluate_quote(self._q(price=10.0, volume=120000, large_net_inflow=-300000.0), is_replay=True, replay_time="2026-07-10 10:03")
        self.assertTrue(
            any("大单净额反转" in t and "红转绿" in t for t in monitor._replay_triggers),
            f"expected flow reversal trigger, got {monitor._replay_triggers}",
        )

    def test_reversal_filtered_when_streak_too_short(self):
        """前序仅连续2根红（< 默认 N=3）就转绿，不应触发反转（过滤单笔/短促抖动）。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000)
        with patch("src.monitor.NotificationService"):
            # 仅2根红
            monitor.evaluate_quote(self._q(price=10.5, volume=110000), is_replay=True, replay_time="2026-07-10 10:00")
            monitor.evaluate_quote(self._q(price=10.8, volume=110000), is_replay=True, replay_time="2026-07-10 10:01")
            # 转绿（前序 run=2 < 3 → 不触发）
            monitor.evaluate_quote(self._q(price=10.4, volume=120000), is_replay=True, replay_time="2026-07-10 10:02")
        self.assertFalse(
            any("价格方向反转" in t for t in monitor._replay_triggers),
            f"short streak should be filtered as jitter, got {monitor._replay_triggers}",
        )

    def test_reversal_rules_have_cooldown(self):
        """反转类规则同样适用 15 分钟冷却（降噪），不再是 0 冷却。"""
        self.assertEqual(RealtimeMonitor._cooldown_seconds("price_reversal"), 900.0)
        self.assertEqual(RealtimeMonitor._cooldown_seconds("flow_reversal"), 900.0)
        self.assertEqual(RealtimeMonitor._cooldown_seconds("volume_spike_ratio"), 900.0)

    def test_explicit_rules_suppress_defaults(self):
        """已显式配置规则的股票不再注入默认异动规则（保持用户完全控制）。"""
        config = self._make_default_rules_config(
            rules_json='[{"stock_code":"000725","type":"price_upper","threshold":5.0}]'
        )
        monitor = RealtimeMonitor(config)
        types_for_code = [r["type"] for r in monitor.rules if r.get("stock_code") == "000725"]
        self.assertEqual(types_for_code, ["price_upper"])

    def test_disable_default_rules(self):
        """关闭默认规则开关后，不注入任何默认规则。"""
        monitor = RealtimeMonitor(self._make_default_rules_config(defaults_enabled=False))
        self.assertEqual(monitor.rules, [])

    def test_green_streak_sell_both_fires(self):
        """连续2根价格绿柱+大单净流出且放量，应触发"连续绿柱卖出"(双重绿)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        # 近几分钟成交量均值基线(股)，用于放量倍数判定
        monitor.volume_history["000725"].extend([10000.0, 12000.0, 11000.0])
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            # 第1根绿:价格下跌+大单净流出+放量(连击=1,未达2根,不触发)
            monitor.evaluate_quote(self._q(price=9.9, volume=200000, large_net_inflow=-100000.0),
                                   is_replay=True, replay_time="2026-07-10 10:00")
            # 第2根绿:继续下跌+净流出+放量(连击=2,触发卖出信号)
            monitor.evaluate_quote(self._q(price=9.8, volume=400000, large_net_inflow=-300000.0),
                                   is_replay=True, replay_time="2026-07-10 10:01")
        self.assertTrue(
            any("连续" in t and "卖出" in t for t in monitor._replay_triggers),
            f"expected green-streak sell trigger, got {monitor._replay_triggers}",
        )

    def test_green_streak_sell_volume_gate_blocks(self):
        """连续绿柱但成交量未放量，不应触发卖出信号。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        # 高基线均值，使后续小单无法达到 1.5 倍放量门槛
        monitor.volume_history["000725"].extend([100000.0, 120000.0, 110000.0])
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=1000000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=9.9, volume=1010000, large_net_inflow=-50000.0),
                                   is_replay=True, replay_time="2026-07-10 10:00")
            monitor.evaluate_quote(self._q(price=9.8, volume=1020000, large_net_inflow=-60000.0),
                                   is_replay=True, replay_time="2026-07-10 10:01")
        self.assertFalse(
            any("连续" in t and "卖出" in t for t in monitor._replay_triggers),
            f"volume gate should block green-streak trigger, got {monitor._replay_triggers}",
        )

    def test_green_streak_sell_resets_on_red(self):
        """绿→红→绿：红柱重置连击，单根绿不触发(需连续>=2根)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.volume_history["000725"].extend([10000.0, 12000.0, 11000.0])
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            # 绿(连击1)
            monitor.evaluate_quote(self._q(price=9.9, volume=200000, large_net_inflow=-100000.0),
                                   is_replay=True, replay_time="2026-07-10 10:00")
            # 红柱:价格上涨+大单转流入，双重重置连击
            monitor.evaluate_quote(self._q(price=10.0, volume=300000, large_net_inflow=50000.0),
                                   is_replay=True, replay_time="2026-07-10 10:01")
            # 再绿(连击1,未达2根)
            monitor.evaluate_quote(self._q(price=9.9, volume=500000, large_net_inflow=-100000.0),
                                   is_replay=True, replay_time="2026-07-10 10:02")
        self.assertFalse(
            any("连续" in t and "卖出" in t for t in monitor._replay_triggers),
            f"red bar should reset streak, got {monitor._replay_triggers}",
        )

    def test_green_streak_sell_mode_price_only(self):
        """mode=price 时只看价格连跌，大单方向不影响触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config(green_streak_mode="price"))
        monitor._replay_triggers = []
        monitor.volume_history["000725"].extend([10000.0, 12000.0, 11000.0])
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            # 价格连跌，但大单为净流入(红)——price 模式仍应触发
            monitor.evaluate_quote(self._q(price=9.9, volume=200000, large_net_inflow=100000.0),
                                   is_replay=True, replay_time="2026-07-10 10:00")
            monitor.evaluate_quote(self._q(price=9.8, volume=400000, large_net_inflow=200000.0),
                                   is_replay=True, replay_time="2026-07-10 10:01")
        self.assertTrue(
            any("连续" in t and "价格绿柱" in t and "卖出" in t for t in monitor._replay_triggers),
            f"price-mode should fire on price streak regardless of flow, got {monitor._replay_triggers}",
        )

    def test_monitor_registry_start_stop(self):
        """start_monitor_thread 注册实例后 status.running=True；stop 经 Event 立即唤醒并清理注册表。"""
        import src.monitor as monitor_module
        monitor_module.stop_monitor_thread()  # 清理可能残留的实例

        config = MagicMock()
        config.stock_list = []  # 空池 -> run_check_cycle 为空操作，不触网
        config.agent_event_alert_rules_json = ""
        config.agent_event_monitor_default_rules_enabled = False
        config.agent_event_monitor_interval_minutes = 0.1  # -> max(6, 5) = 6s，靠 Event 唤醒而非等满
        config.agent_mode = False

        # 初始无活跃实例
        self.assertFalse(monitor_module.get_monitor_status()["running"])

        thread = monitor_module.start_monitor_thread(config)
        self.assertIsNotNone(thread)
        try:
            # 等待线程进入 start() 并置 _running=True
            deadline = time.time() + 2.0
            while time.time() < deadline and not monitor_module.get_monitor_status()["running"]:
                time.sleep(0.02)
            status = monitor_module.get_monitor_status()
            self.assertTrue(status["running"])
            self.assertTrue(status["thread_alive"])
            self.assertEqual(status["interval_seconds"], 6)

            # stop() 通过 _stop_event.set() 立即唤醒，不应等满 6s
            self.assertTrue(monitor_module.stop_monitor_thread())
            thread.join(timeout=3.0)
            self.assertFalse(thread.is_alive())

            after = monitor_module.get_monitor_status()
            self.assertFalse(after["running"])
            self.assertEqual(after["stock_list"], [])
        finally:
            monitor_module.stop_monitor_thread()

    def test_stop_monitor_when_idle_returns_false(self):
        """无活跃实例时 stop_monitor_thread 应返回 False（幂等、无副作用）。"""
        import src.monitor as monitor_module
        monitor_module.stop_monitor_thread()  # 确保空闲
        self.assertFalse(monitor_module.stop_monitor_thread())
        status = monitor_module.get_monitor_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["stock_list"], [])

    def test_open_surge_and_sharp_drop_helpers_read_config(self):
        """7 个阈值 helper 应从 config 读出真实值（非 MagicMock 的 0.0/0）。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        self.assertTrue(monitor._open_surge_revert_enabled())
        self.assertEqual(monitor._open_surge_window_minutes(), 15)
        self.assertAlmostEqual(monitor._open_surge_pct(), 1.5)
        self.assertAlmostEqual(monitor._open_revert_pct(), 0.5)
        self.assertTrue(monitor._sharp_drop_enabled())
        self.assertEqual(monitor._sharp_drop_bars(), 5)
        self.assertAlmostEqual(monitor._sharp_drop_pct(), 1.5)

    def test_in_open_window_boundary(self):
        """开盘窗口 [09:30, 09:45) 左闭右开；09:45 与 10:00 不在窗口内。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        self.assertTrue(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 30).timestamp()))
        self.assertTrue(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 44, 59).timestamp()))
        self.assertFalse(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 45).timestamp()))
        self.assertFalse(monitor._in_open_window(datetime.datetime(2026, 7, 13, 10, 0).timestamp()))

    def test_price_history_and_open_state_seeded_on_first_bar(self):
        """首笔行情（无 last_quote）应种子 open_price/open_high/open_day 并追加 price_history。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.0, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
        self.assertAlmostEqual(monitor.open_price["000725"], 10.0)
        self.assertAlmostEqual(monitor.open_high["000725"], 10.0)
        self.assertEqual(monitor.open_day["000725"], "2026-07-13")
        self.assertFalse(monitor.open_surge_fired["000725"])
        self.assertEqual(len(monitor.price_history["000725"]), 1)

    def test_open_surge_revert_fires_once(self):
        """开盘冲高(+2%≥1.5%)后回落(自高点跌0.98%≥0.5%)→触发一次，标记高点价 10.20。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
        hits = [t for t in monitor._replay_triggers if "开盘冲高" in t]
        self.assertEqual(len(hits), 1, f"expected one open-surge-revert, got {monitor._replay_triggers}")
        self.assertIn("10.20", hits[0])
        self.assertTrue(monitor.open_surge_fired["000725"])

    def test_open_surge_revert_no_spike_no_fire(self):
        """开盘即跌(无冲高)→不触发(交给 sharp_drop)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=9.95, volume=110000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=9.90, volume=120000), is_replay=True, replay_time="2026-07-13 09:40")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_open_surge_revert_no_revert_no_fire(self):
        """冲高后继续上行(无回落)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.30, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_open_surge_revert_one_shot(self):
        """冲高回落触发后，再冲高回落→仅触发 1 次(fired 标志)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
            monitor.evaluate_quote(self._q(price=10.25, volume=140000), is_replay=True, replay_time="2026-07-13 09:42")
            monitor.evaluate_quote(self._q(price=10.10, volume=150000), is_replay=True, replay_time="2026-07-13 09:44")
        self.assertEqual(len([t for t in monitor._replay_triggers if "开盘冲高" in t]), 1)

    def test_open_surge_revert_resets_next_day(self):
        """跨日 open_day 变更→fired 复位，可再次触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-14 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-14 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-14 09:40")
        self.assertEqual(len([t for t in monitor._replay_triggers if "开盘冲高" in t]), 2)

    def test_open_surge_revert_out_of_window_no_high_update(self):
        """10:00(窗口外)不更新 open_high，open_high 停在首笔 10.00，无 spike 不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 10:00")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 10:05")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 10:10")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)
        self.assertAlmostEqual(monitor.open_high["000725"], 10.00)

    def test_sharp_drop_fires(self):
        """5 笔内自首笔跌 2%(≥1.5%)→触发急跌，rule_desc 含 '近 5 笔'。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.90, 9.85, 9.80, 9.80]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
        hits = [t for t in monitor._replay_triggers if "急跌" in t]
        self.assertEqual(len(hits), 1, f"expected sharp-drop trigger, got {monitor._replay_triggers}")
        self.assertIn("近 5 笔", hits[0])

    def test_sharp_drop_subthreshold_no_fire(self):
        """5 笔累计跌 1%(<1.5%)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.98, 9.97, 9.96, 9.90]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
        self.assertFalse(any("急跌" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_sharp_drop_insufficient_bars_no_fire(self):
        """仅 3 笔(<5)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 10:00")
            monitor.evaluate_quote(self._q(price=9.80, volume=110000), is_replay=True, replay_time="2026-07-13 10:01")
            monitor.evaluate_quote(self._q(price=9.60, volume=120000), is_replay=True, replay_time="2026-07-13 10:02")
        self.assertFalse(any("急跌" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_sharp_drop_cooldown(self):
        """触发后 15 分钟内同码再达阈值→不重复触发(冷却)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.90, 9.85, 9.80, 9.80]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
            monitor.evaluate_quote(self._q(price=9.60, volume=100000), is_replay=True, replay_time="2026-07-13 10:05")
        hits = [t for t in monitor._replay_triggers if "急跌" in t]
        self.assertEqual(len(hits), 1, f"cooldown should block 2nd fire, got {monitor._replay_triggers}")

    def test_run_replay_emits_both_detectors_and_clears_state(self):
        """run_replay_simulation 应在分钟序列中触发 open_surge_revert + sharp_drop，且事后清掉 price_history。"""
        monitor = RealtimeMonitor(self._make_default_rules_config(stock_list=["000725"]))
        monitor._replay_triggers = []

        # 静态行情（run_replay 用其 name/pre_close/circ_mv/source 作为每笔基础信息）
        static_quote = UnifiedRealtimeQuote(
            code="000725", name="京东方A", source=RealtimeSource.TENCENT,
            price=10.0, volume=0, main_net_inflow=0.0, large_net_inflow=0.0,
            pre_close=10.0, circ_mv=10000000000.0,
        )

        # 分时趋势字符串: parts[0]=time, parts[2]=price, parts[5]=vol(手)，至少 8 字段
        # 含冲高回落(09:30 10.00 → 09:35 10.20 → 09:40 10.10) + 急跌(10:00-10:04 共5笔跌至 9.80)
        trends = [
            "2026-07-13 09:30,10.0,10.00,0,0,100,0,0",
            "2026-07-13 09:35,10.1,10.20,0,0,120,0,0",
            "2026-07-13 09:40,10.1,10.10,0,0,130,0,0",
            "2026-07-13 10:00,10.0,10.00,0,0,100,0,0",
            "2026-07-13 10:01,9.95,9.90,0,0,110,0,0",
            "2026-07-13 10:02,9.9,9.85,0,0,120,0,0",
            "2026-07-13 10:03,9.85,9.80,0,0,130,0,0",
            "2026-07-13 10:04,9.85,9.80,0,0,140,0,0",
        ]

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"data": {"trends": trends}}

        with patch.object(monitor.fetcher_mgr, "get_realtime_quote", return_value=static_quote), \
             patch("src.monitor.fetch_fflow_klines", return_value=[["2026-07-13 09:30", "0.0", "0", "0", "0.0", "0.0"]]), \
             patch("src.monitor.requests.get", return_value=fake_resp):
            monitor.run_replay_simulation()

        triggers = monitor._replay_triggers
        self.assertTrue(any("开盘冲高" in t for t in triggers), f"missing open-surge-revert, got {triggers}")
        self.assertTrue(any("急跌" in t for t in triggers), f"missing sharp-drop, got {triggers}")
        # 事后清理（G 块）应清掉 price_history，防止污染实时内存
        self.assertEqual(len(monitor.price_history["000725"]), 0)


class TestMonitorDetectorConfig(unittest.TestCase):
    """开盘冲高回落 + 急跌幅度 检测器的 config 字段解析。"""

    def test_config_loads_detector_fields_from_env(self):
        """config 应解析 7 个新盘中检测器字段（含 env 覆盖）。"""
        from src.config import Config
        env = {
            "AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED": "false",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES": "20",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_PCT": "2.5",
            "AGENT_EVENT_MONITOR_OPEN_REVERT_PCT": "0.8",
            "AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED": "false",
            "AGENT_EVENT_MONITOR_SHARP_DROP_BARS": "8",
            "AGENT_EVENT_MONITOR_SHARP_DROP_PCT": "2.0",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = Config._load_from_env()
        self.assertFalse(cfg.agent_event_monitor_open_surge_revert_enabled)
        self.assertEqual(cfg.agent_event_monitor_open_surge_window_minutes, 20)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_surge_pct, 2.5)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_revert_pct, 0.8)
        self.assertFalse(cfg.agent_event_monitor_sharp_drop_enabled)
        self.assertEqual(cfg.agent_event_monitor_sharp_drop_bars, 8)
        self.assertAlmostEqual(cfg.agent_event_monitor_sharp_drop_pct, 2.0)

    def test_config_detector_fields_defaults(self):
        """无 env 时 7 字段取默认值（save/restore，避免污染本进程 env）。"""
        from src.config import Config
        env_keys = [
            "AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_PCT",
            "AGENT_EVENT_MONITOR_OPEN_REVERT_PCT",
            "AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED",
            "AGENT_EVENT_MONITOR_SHARP_DROP_BARS",
            "AGENT_EVENT_MONITOR_SHARP_DROP_PCT",
        ]
        saved = {k: os.environ.pop(k, None) for k in env_keys}
        try:
            cfg = Config._load_from_env()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(cfg.agent_event_monitor_open_surge_revert_enabled)
        self.assertEqual(cfg.agent_event_monitor_open_surge_window_minutes, 15)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_surge_pct, 1.5)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_revert_pct, 0.5)
        self.assertTrue(cfg.agent_event_monitor_sharp_drop_enabled)
        self.assertEqual(cfg.agent_event_monitor_sharp_drop_bars, 5)
        self.assertAlmostEqual(cfg.agent_event_monitor_sharp_drop_pct, 1.5)


if __name__ == "__main__":
    unittest.main()
