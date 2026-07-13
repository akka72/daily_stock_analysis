# -*- coding: utf-8 -*-
"""
东财个股资金流(分钟K)加固获取 — 节流 + 指数退避重试 + 熔断降级。

动机：`push2.eastmoney.com/api/qt/stock/fflow/kline/get` 在高频拉取后会对源 IP
做软封禁。原先 `_inject_capital_flow` / 回放路径各写一套裸 `requests.get`，零节流、
零退避、零熔断，几下就把 IP 撞进黑名单。

本模块把拉取逻辑集中一处，三重防护：
1. 节流：两次 fflow 请求间至少 `min_interval` 秒 + jitter，**避免**触发封禁。
2. 退避重试：瞬时失败(超时/连接/非200)指数退避重试 N 次。
3. 熔断：连续失败达阈值 → 冷却 N 分钟，期间直接返回 None（纯价格盯盘），
   不再硬撞，让 IP 自然解封。

注意：多 host 轮换对「源 IP 级」软封禁无效(同供应商、同客户端 IP)，故不采用。

`fetch_fflow_klines` 同时服务于：
- `AkshareFetcher._inject_capital_flow`（盘中实时路径，取末根）
- `RealtimeMonitor.run_replay_simulation`（回放路径，取全部分钟）
"""

import logging
import random
import time
from typing import List, Optional

import requests

from data_provider.realtime_types import CircuitBreaker
from src.config import get_config

logger = logging.getLogger(__name__)

# 东财资金流端点（http 绕开代理，与原实现一致）
_FLOW_URL = "http://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_FLOW_PARAMS_TEMPLATE = {
    "lmt": "0",
    "klt": "1",
    "fields1": "f1,f2,f3,f7",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
    "ut": "b2884a393a59ad64002292a3e90d46a5",
}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_NO_PROXY = {"http": None, "https": None}

# 熔断器按 source 维度独立计费，复用 CircuitBreaker（不与行情/筹码的共享单例混淆）
_FLOW_SOURCE = "eastmoney_fflow"
_BREAKER: Optional[CircuitBreaker] = None

# 节流状态：上次请求时间戳（跨股票/跨轮询全局共享）
_LAST_REQUEST_AT: float = 0.0


def _get_breaker() -> CircuitBreaker:
    """懒建专用熔断器，参数取自 config（首调用定型；冷却/阈值不热更新）。"""
    global _BREAKER
    if _BREAKER is None:
        cfg = get_config()
        _BREAKER = CircuitBreaker(
            failure_threshold=max(1, cfg.eastmoney_flow_failure_threshold),
            cooldown_seconds=max(0, cfg.eastmoney_flow_circuit_cooldown_seconds),
        )
    return _BREAKER


def _enforce_flow_throttle(min_interval: float) -> None:
    """保证两次资金流请求间至少 `min_interval` 秒，加小幅 jitter 抖散并发。"""
    global _LAST_REQUEST_AT
    if min_interval <= 0:
        _LAST_REQUEST_AT = time.time()
        return
    now = time.time()
    if _LAST_REQUEST_AT > 0:
        elapsed = now - _LAST_REQUEST_AT
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
    time.sleep(random.uniform(0.0, 0.3))  # jitter，避免多股轮询形成固定节拍
    _LAST_REQUEST_AT = time.time()


def fetch_fflow_klines(secid: str, *, timeout: float = 5.0) -> Optional[List[List[str]]]:
    """
    拉取东财个股资金流分钟K线。

    Args:
        secid: 东财 secid，如 "1.600519" / "0.000001"。
        timeout: 单次请求超时(秒)。

    Returns:
        拆分后的 kline 列表（每行已 split(',')）；熔断中或重试耗尽返回 None
        （调用方应据此降级为纯价格盯盘，不报错）。
    """
    cfg = get_config()
    breaker = _get_breaker()

    if not breaker.is_available(_FLOW_SOURCE):
        logger.debug("[资金流向] 东财 fflow 熔断中，跳过（纯价格盯盘）")
        return None

    _enforce_flow_throttle(cfg.eastmoney_flow_min_interval_seconds)

    params = dict(_FLOW_PARAMS_TEMPLATE)
    params["secid"] = secid
    retries = max(1, cfg.eastmoney_flow_retry_count)

    for attempt in range(retries):
        try:
            resp = requests.get(
                _FLOW_URL, params=params, headers=_UA, proxies=_NO_PROXY, timeout=timeout
            )
        except Exception as exc:  # 超时 / 连接错误 → 计失败 + 指数退避重试
            breaker.record_failure(_FLOW_SOURCE, type(exc).__name__)
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 5))
            continue

        if resp.status_code == 200:
            try:
                data = resp.json() or {}
            except ValueError:
                breaker.record_failure(_FLOW_SOURCE, "invalid_json")
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 5))
                continue
            klines = (data.get("data") or {}).get("klines") or []
            if klines:
                breaker.record_success(_FLOW_SOURCE)
                return [k.split(",") for k in klines]
            # 200 但空 klines（盘前/冷门股暂无数据）→ 视为良性，不计失败，不触发熔断
            return None

        # 非 200 → 封禁征兆，计失败 + 退避重试
        breaker.record_failure(_FLOW_SOURCE, f"http_{resp.status_code}")
        if attempt < retries - 1:
            time.sleep(min(2 ** attempt, 5))

    logger.warning("[资金流向] fflow 拉取失败 (%s)，已用尽重试", secid)
    return None


def _reset_for_test() -> None:
    """测试专用：重置模块级熔断器与节流状态，保证用例间隔离。"""
    global _BREAKER, _LAST_REQUEST_AT
    _BREAKER = None
    _LAST_REQUEST_AT = 0.0
