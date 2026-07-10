# -*- coding: utf-8 -*-
"""Tests for history comparison action normalization compatibility."""

from datetime import datetime
from types import SimpleNamespace

from src.services.history_comparison_service import _record_to_signal


def _history_record(**kwargs):
    return SimpleNamespace(
        raw_result=kwargs.get("raw_result", {}),
        operation_advice=kwargs.get("operation_advice"),
        sentiment_score=kwargs.get("sentiment_score", 72),
        report_language=kwargs.get("report_language"),
        created_at=kwargs.get("created_at", datetime(2026, 1, 1, 9, 0)),
        query_id=kwargs.get("query_id", "history-record"),
        trend_prediction=kwargs.get("trend_prediction", "看多"),
        report_type=kwargs.get("report_type", "simple"),
    )


def test_record_to_signal_uses_raw_action_field_before_action_label() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "action": "sell",
                "action_label": "매수",
                "operation_advice": "观望",
                "report_language": "ko",
                "guardrail_reason": None,
            },
            report_language="ko",
            sentiment_score=60,
        )
    )

    assert signal is not None
    assert signal["action"] == "sell"
    assert signal["action_label"] == "매도"


def test_record_to_signal_prefers_action_label_when_action_invalid() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "action": "unknown",
                "action_label": "回避",
                "operation_advice": "持有",
                "report_language": "zh",
                "guardrail_reason": None,
            },
            report_language="zh",
            sentiment_score=84,
        )
    )

    assert signal is not None
    assert signal["action"] == "avoid"
    assert signal["action_label"] == "回避"


def test_record_to_signal_falls_back_to_action_label_when_action_absent() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "action_label": "경고",
                "operation_advice": "持有",
                "report_language": "ko",
            },
            report_language="ko",
            sentiment_score=72,
        )
    )

    assert signal is not None
    assert signal["action"] == "alert"
    assert signal["action_label"] == "경고"


def test_record_to_signal_localizes_action_with_override_language() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "operation_advice": "持有",
                "report_language": "ko",
                "guardrail_reason": None,
            },
            report_language="ko",
            sentiment_score=72,
        ),
        report_language="en",
    )

    assert signal is not None
    assert signal["action"] == "buy"
    assert signal["action_label"] == "Buy"


def test_record_to_signal_localizes_action_label_with_language_override() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "action": None,
                "action_label": "回避",
                "operation_advice": "持有",
                "report_language": "zh",
            },
            report_language="zh",
        ),
        report_language="en",
    )

    assert signal is not None
    assert signal["action"] == "avoid"
    assert signal["action_label"] == "Avoid"


def test_record_to_signal_preserves_hold_with_decision_score_guardrail_reason() -> None:
    signal = _record_to_signal(
        _history_record(
            raw_result={
                "action": "hold",
                "operation_advice": "持有",
                "report_language": "zh",
                "decision_score_guardrail_reason": "评分偏高但风险仍存",
            },
            report_language="zh",
            sentiment_score=80,
        )
    )

    assert signal is not None
    assert signal["action"] == "hold"
    assert signal["action_label"] == "持有"
