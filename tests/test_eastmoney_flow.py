# -*- coding: utf-8 -*-
"""
资金流数据源加固的回归测试。

覆盖：
1. data_provider/eastmoney_flow.py —— 节流 / 指数退避重试 / 熔断降级 / 空klines不误判
2. AkshareFetcher._inject_capital_flow —— 末根字段索引映射
3. TushareFetcher.get_individual_moneyflow —— net_amount 映射 + 5/10日累加 + 失败返回 None
4. AkshareFundamentalAdapter.get_capital_flow —— akshare 失败回落 Tushare
"""

import sys
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.append("f:/codeRepo/ai/agent/daily_stock_analysis")

# 导入 TushareFetcher / AkshareFetcher 前，与既有测试一致地垫好 litellm stub
try:
    from tests.litellm_stub import ensure_litellm_stub
    ensure_litellm_stub()
except Exception:
    pass

from data_provider import eastmoney_flow
from data_provider.eastmoney_flow import fetch_fflow_klines, _get_breaker, _FLOW_SOURCE
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.tushare_fetcher import TushareFetcher
from data_provider.fundamental_adapter import AkshareFundamentalAdapter


def _cfg(min_interval=1.0, retry=2, threshold=3, cooldown=300):
    """构造一个只含 eastmoney_flow 相关字段的 stub config。"""
    return types.SimpleNamespace(
        eastmoney_flow_min_interval_seconds=min_interval,
        eastmoney_flow_retry_count=retry,
        eastmoney_flow_failure_threshold=threshold,
        eastmoney_flow_circuit_cooldown_seconds=cooldown,
    )


def _resp(klines=None, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"data": {"klines": klines or []}}
    return r


class TestEastmoneyFlowFetch(unittest.TestCase):
    """fetch_fflow_klines: 节流 / 重试 / 熔断 / 空数据。"""

    def setUp(self):
        eastmoney_flow._reset_for_test()

    def tearDown(self):
        eastmoney_flow._reset_for_test()

    def _patches(self, cfg=None):
        cfg = cfg or _cfg()
        return (
            patch.object(eastmoney_flow, "get_config", return_value=cfg),
            patch.object(eastmoney_flow, "requests"),  # 整体替换 requests 模块引用
            patch.object(eastmoney_flow.time, "sleep", lambda s: None),
            patch.object(eastmoney_flow.random, "uniform", lambda a, b: 0.0),
        )

    def test_throttle_enforces_min_interval_between_requests(self):
        sleeps = []
        with patch.object(eastmoney_flow, "get_config", return_value=_cfg(min_interval=2.0, retry=1)), \
             patch.object(eastmoney_flow, "requests") as req_mod, \
             patch.object(eastmoney_flow.time, "sleep", lambda s: sleeps.append(s)), \
             patch.object(eastmoney_flow.random, "uniform", lambda a, b: 0.0):
            req_mod.get.return_value = _resp(klines=["09:31,1,2,3,4,5,6"])
            fetch_fflow_klines("1.600519")
            count_after_first = len(sleeps)
            fetch_fflow_klines("1.600519")  # 第二次：距上次极近，应补足 2.0s
        second_call_sleeps = sleeps[count_after_first:]
        self.assertTrue(
            any(s >= 1.9 for s in second_call_sleeps),
            f"第二次调用应补足 min_interval 的节流 sleep(>=1.9s)，实际 {second_call_sleeps}",
        )

    def test_retry_then_success_records_success(self):
        good = _resp(klines=["09:31,100,0,0,50,50,0", "09:32,200,0,0,80,80,0"])
        with patch.object(eastmoney_flow, "get_config", return_value=_cfg(retry=3)), \
             patch.object(eastmoney_flow, "requests") as req_mod, \
             patch.object(eastmoney_flow.time, "sleep", lambda s: None), \
             patch.object(eastmoney_flow.random, "uniform", lambda a, b: 0.0):
            req_mod.get.side_effect = [TimeoutError("boom"), good]
            klines = fetch_fflow_klines("1.600519")
        self.assertEqual(len(klines), 2)
        self.assertEqual(klines[0], ["09:31", "100", "0", "0", "50", "50", "0"])
        # 成功后熔断器恢复(CLOSED)，失败计数归零
        self.assertTrue(_get_breaker().is_available(_FLOW_SOURCE))
        self.assertEqual(_get_breaker()._states[_FLOW_SOURCE]["failures"], 0)

    def test_circuit_breaker_trips_and_skips_further_requests(self):
        calls = {"n": 0}

        def fake_get(*a, **k):
            calls["n"] += 1
            return _resp(status=500)

        with patch.object(eastmoney_flow, "get_config", return_value=_cfg(retry=1, threshold=2)), \
             patch.object(eastmoney_flow, "requests") as req_mod, \
             patch.object(eastmoney_flow.time, "sleep", lambda s: None), \
             patch.object(eastmoney_flow.random, "uniform", lambda a, b: 0.0):
            req_mod.get.side_effect = fake_get
            self.assertIsNone(fetch_fflow_klines("1.600519"))  # 失败1：failures=1，未熔断
            self.assertEqual(calls["n"], 1)
            self.assertIsNone(fetch_fflow_klines("1.600519"))  # 失败2：failures=2 → OPEN
            self.assertEqual(calls["n"], 2)
            self.assertFalse(_get_breaker().is_available(_FLOW_SOURCE))
            # 第三次：熔断中，直接返回 None，不应再发请求
            self.assertIsNone(fetch_fflow_klines("1.600519"))
        self.assertEqual(calls["n"], 2, "熔断期间不应再调用 requests.get")

    def test_empty_klines_is_benign_and_does_not_trip_breaker(self):
        with patch.object(eastmoney_flow, "get_config", return_value=_cfg(retry=1)), \
             patch.object(eastmoney_flow, "requests") as req_mod, \
             patch.object(eastmoney_flow.time, "sleep", lambda s: None), \
             patch.object(eastmoney_flow.random, "uniform", lambda a, b: 0.0):
            req_mod.get.return_value = _resp(klines=[])  # 200 但空(盘前/冷门股)
            self.assertIsNone(fetch_fflow_klines("1.600519"))
        state = _get_breaker()._states.get(_FLOW_SOURCE, {})
        self.assertEqual(state.get("failures", 0), 0, "空 klines 视为良性，不应计失败")
        self.assertTrue(_get_breaker().is_available(_FLOW_SOURCE))


class TestInjectCapitalFlow(unittest.TestCase):
    """_inject_capital_flow: 末根字段索引映射(主力=idx1 / 大单=idx4 / 超大单=idx5)。"""

    def test_maps_last_kline_indices(self):
        klines = [["09:31", "1000.0", "0", "0", "500.0", "50.0", "0"]]
        quote = UnifiedRealtimeQuote(
            code="600519", name="茅台", source=RealtimeSource.TENCENT, price=1800.0
        )
        fetcher = AkshareFetcher.__new__(AkshareFetcher)  # 跳过 __init__，方法不依赖实例属性
        with patch("data_provider.akshare_fetcher.fetch_fflow_klines", return_value=klines):
            fetcher._inject_capital_flow(quote)
        self.assertEqual(quote.main_net_inflow, 1000.0)
        self.assertEqual(quote.large_net_inflow, 500.0)
        self.assertEqual(quote.super_large_net_inflow, 50.0)

    def test_no_injection_when_fetch_returns_none(self):
        quote = UnifiedRealtimeQuote(
            code="600519", name="茅台", source=RealtimeSource.TENCENT, price=1800.0
        )
        fetcher = AkshareFetcher.__new__(AkshareFetcher)
        with patch("data_provider.akshare_fetcher.fetch_fflow_klines", return_value=None):
            fetcher._inject_capital_flow(quote)  # 不应抛错，保持原值
        self.assertIsNone(quote.main_net_inflow)


class TestTushareMoneyflowAndFallback(unittest.TestCase):
    """Tushare 个股资金流 + adapter 回落。"""

    @staticmethod
    def _make_tushare():
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_get_individual_moneyflow_maps_and_sums(self):
        fetcher = self._make_tushare()
        df = type(fetcher._api)  # 占位，立即覆盖
        df = None
        import pandas as pd
        df = pd.DataFrame({
            "trade_date": ["20260710", "20260709", "20260708", "20260707", "20260706", "20260703"],
            "net_amount": [100.0, 200.0, -50.0, 300.0, 50.0, 25.0],
        })
        fetcher._api.moneyflow.return_value = df
        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 7, 10, 20, 0)), \
             patch.object(fetcher, "_check_rate_limit"):
            mf = fetcher.get_individual_moneyflow("600519")
        self.assertIsNotNone(mf)
        self.assertEqual(mf["main_net_inflow"], 100.0)            # 最新交易日
        # 倒序前5个交易日累计：100+200-50+300+50 = 600.0
        self.assertEqual(mf["inflow_5d"], 600.0)
        # 仅6行，前10个累计 = 600 + 25 = 625.0
        self.assertEqual(mf["inflow_10d"], 625.0)

    def test_get_individual_moneyflow_none_when_api_unavailable(self):
        fetcher = self._make_tushare()
        fetcher._api = None
        self.assertIsNone(fetcher.get_individual_moneyflow("600519"))

    def test_get_individual_moneyflow_none_on_api_error(self):
        fetcher = self._make_tushare()
        fetcher._api.moneyflow.side_effect = RuntimeError("权限不足")
        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 7, 10, 20, 0)), \
             patch.object(fetcher, "_check_rate_limit"):
            self.assertIsNone(fetcher.get_individual_moneyflow("600519"))

    def test_capital_flow_falls_back_to_tushare_when_akshare_empty(self):
        adapter = AkshareFundamentalAdapter()
        ts_mock = MagicMock()
        ts_mock.get_individual_moneyflow.return_value = {
            "main_net_inflow": 123.0, "inflow_5d": None, "inflow_10d": None,
        }
        with patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, [])), \
             patch.object(AkshareFundamentalAdapter, "_get_tushare_fetcher", return_value=ts_mock):
            result = adapter.get_capital_flow("600519")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], 123.0)
        self.assertIn("capital_stock:tushare_moneyflow", result["source_chain"])


if __name__ == "__main__":
    unittest.main()
