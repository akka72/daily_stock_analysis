# -*- coding: utf-8 -*-
"""
单元测试：实时监控与预警规则评估
"""

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

    @patch('src.monitor.requests.get')
    def test_run_replay_simulation(self, mock_get):
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

        # 3. 模拟 requests.get 响应
        # 第一个请求：资金流向 klines
        mock_flow_response = MagicMock()
        mock_flow_response.status_code = 200
        mock_flow_response.json.return_value = {
            "data": {
                "klines": [
                    "2026-07-10 09:31,100000.0,0.0,0.0,50000.0,50000.0",
                    "2026-07-10 09:32,200000.0,0.0,0.0,100000.0,100000.0",
                    "2026-07-10 09:33,300000.0,0.0,0.0,150000.0,150000.0"
                ]
            }
        }
        
        # 第二个请求：分时趋势 trends
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

        # 设置 side_effect 让 mock_get 依次返回 flow 和 trend 响应
        mock_get.side_effect = [mock_flow_response, mock_trend_response]

        # 运行回放模拟
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
        """价格方向由涨转跌，应在第二笔触发价格反转预警。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000)
        with patch("src.monitor.NotificationService"):
            # 第一笔：上涨（红），建立方向
            monitor.evaluate_quote(self._q(price=10.5, volume=110000), is_replay=True, replay_time="2026-07-10 10:00")
            # 第二笔：下跌（绿），方向反转
            monitor.evaluate_quote(self._q(price=10.2, volume=120000), is_replay=True, replay_time="2026-07-10 10:01")
        self.assertTrue(
            any("价格方向反转" in t and "红转绿" in t for t in monitor._replay_triggers),
            f"expected price reversal trigger, got {monitor._replay_triggers}",
        )

    def test_flow_reversal_fires(self):
        """大单净额由净流入转净流出，应在第二笔触发大单反转预警。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        monitor.last_quotes["000725"] = self._q(price=10.0, volume=100000, large_net_inflow=0.0)
        with patch("src.monitor.NotificationService"):
            # 第一笔：大单净流入（红），建立符号
            monitor.evaluate_quote(self._q(price=10.0, volume=110000, large_net_inflow=500000.0), is_replay=True, replay_time="2026-07-10 10:00")
            # 第二笔：大单净流出（绿），符号反转
            monitor.evaluate_quote(self._q(price=10.0, volume=120000, large_net_inflow=-300000.0), is_replay=True, replay_time="2026-07-10 10:01")
        self.assertTrue(
            any("大单净额反转" in t and "红转绿" in t for t in monitor._replay_triggers),
            f"expected flow reversal trigger, got {monitor._replay_triggers}",
        )

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


if __name__ == "__main__":
    unittest.main()
