# -*- coding: utf-8 -*-
"""
===================================
盘中盯盘控制接口
===================================

职责：
1. /api/v1/monitor/status  查询后台盘中盯盘运行状态
2. /api/v1/monitor/start   运行时启动盯盘（用最新配置重建实例）
3. /api/v1/monitor/stop    运行时停止盯盘

用于让 WebUI 无需重启 serve 即可启停盘中盯盘线程。
鉴权复用全局 AuthMiddleware（启用鉴权时所有路由自动校验会话）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from src.config import get_config
from src.monitor import (
    get_monitor_status,
    start_monitor_thread,
    stop_monitor_thread,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/status",
    summary="Get intraday monitor status",
    description="返回后台盘中盯盘线程的运行状态、股票池、轮询间隔与规则数。",
)
def get_status() -> dict:
    """返回当前盯盘运行状态。"""
    return get_monitor_status()


@router.post(
    "/start",
    summary="Start intraday monitor",
    description="用最新配置启动盘中盯盘后台线程；若已在运行则先停止再重建。",
)
def start_monitor() -> dict:
    """启动（或重启）后台盯盘，返回启动后的状态。"""
    config = get_config()
    thread = start_monitor_thread(config)
    if thread is None:
        logger.error("[盯盘控制] /monitor/start 启动失败")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "monitor_start_failed",
                "message": "盯盘线程启动失败，详见服务端日志。",
            },
        )
    return get_monitor_status()


@router.post(
    "/stop",
    summary="Stop intraday monitor",
    description="停止当前后台盘中盯盘线程（当前轮询结束后退出）。",
)
def stop_monitor() -> dict:
    """停止当前盯盘，返回停止后的状态。"""
    stopped = stop_monitor_thread()
    status = get_monitor_status()
    status["stopped_existing"] = stopped
    return status
