# -*- coding: utf-8 -*-
"""Tests for Issue #1390 P0 decision action taxonomy helpers."""

import pytest

from src.schemas.decision_action import (
    build_action_fields,
    display_action_fields,
    display_action_fields_for_result,
    display_decision_type_for_result,
    display_operation_advice,
    localize_action_label,
    normalize_decision_action,
)
from src.schemas.decision_scale import (
    action_for_score,
    decision_type_for_score,
    extract_decision_guardrail_reason,
    signal_key_for_score,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("strong_buy", "buy"),
        ("强烈买入", "buy"),
        ("买入", "buy"),
        ("布局", "buy"),
        ("建仓", "buy"),
        ("매수", "buy"),
        ("추가 매수", "add"),
        ("add", "add"),
        ("加仓", "add"),
        ("增持", "add"),
        ("accumulate", "add"),
        ("hold", "hold"),
        ("持有", "hold"),
        ("持有观察", "hold"),
        ("洗盘观察", "hold"),
        ("보유", "hold"),
        ("관망", "watch"),
        ("watch", "watch"),
        ("观望", "watch"),
        ("等待", "watch"),
        ("wait", "watch"),
        ("reduce", "reduce"),
        ("减仓", "reduce"),
        ("trim", "reduce"),
        ("sell", "sell"),
        ("卖出", "sell"),
        ("清仓", "sell"),
        ("strong_sell", "sell"),
        ("强烈卖出", "sell"),
        ("매도", "sell"),
        ("avoid", "avoid"),
        ("回避", "avoid"),
        ("规避", "avoid"),
        ("不建议买入", "avoid"),
        ("避免买入", "avoid"),
        ("回避买入", "avoid"),
        ("规避买入", "avoid"),
        ("规避卖出", "avoid"),
        ("回避减仓", "avoid"),
        ("do not buy", "avoid"),
        ("회피", "avoid"),
        ("alert", "alert"),
        ("风险预警", "alert"),
        ("警惕", "alert"),
        ("触发告警", "alert"),
        ("risk alert", "alert"),
        ("경고", "alert"),
        ("风险预警，建议卖出", "sell"),
        ("风险预警，建议减仓", "reduce"),
        ("风险预警，持有", "alert"),
        ("风险预警，观望", "alert"),
        ("Alert, sell", "sell"),
        ("alert, hold", "alert"),
        ("alert, watch", "alert"),
        ("경고, 매도", "sell"),
        ("경고, 비중축소", "reduce"),
        ("경고, 보유", "alert"),
        ("경고, 매수", "buy"),
        ("risk alert, avoid buying", "avoid"),
    ],
)
def test_normalize_decision_action_matrix(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "观察",
        "等待突破后买入",
        "waiting to buy",
        "买入或卖出",
        "buy or sell",
        "买盘增强，继续观察",
        "卖压缓解，继续观察",
        "卖方评级分歧",
        "no buyback announced",
        "cannot buyback shares now",
        "share buy-back announced",
        "share buy back announced",
        "no selloff risk",
        "not selloff yet",
        "sell-off risk remains low",
        "sell off risk remains low",
        "no sell-off pressure",
        "普通复盘说明",
    ],
)
def test_normalize_decision_action_unknown_or_ambiguous_returns_none(value: str | None) -> None:
    assert normalize_decision_action(value) is None


def test_normalize_decision_action_keeps_no_separator_avoid_buyings_as_avoid() -> None:
    assert normalize_decision_action("回避买入") == "avoid"
    assert normalize_decision_action("规避买入") == "avoid"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("暂不买入", "avoid"),
        ("不要买入", "avoid"),
        ("不宜买入", "avoid"),
        ("先不买入", "avoid"),
        ("无需买入", "avoid"),
        ("无须买入", "avoid"),
        ("不建议建仓", "avoid"),
        ("暂不建仓", "avoid"),
        ("无需建仓", "avoid"),
        ("无须建仓", "avoid"),
        ("不建议布局", "avoid"),
        ("先不布局", "avoid"),
        ("无需布局", "avoid"),
        ("无须布局", "avoid"),
        ("no buy", "avoid"),
        ("no need to buy", "avoid"),
        ("need not buy", "avoid"),
        ("cannot buy", "avoid"),
        ("can't buy", "avoid"),
        ("not a buy yet", "avoid"),
        ("not to buy", "avoid"),
        ("avoid buying", "avoid"),
        ("avoid buying into weakness", "avoid"),
        ("不建议加仓", "hold"),
        ("无须加仓", "hold"),
        ("no add", "hold"),
        ("no need to add", "hold"),
        ("need not add", "hold"),
        ("cannot add", "hold"),
        ("not to add", "hold"),
        ("no accumulate", "hold"),
        ("can't accumulate", "hold"),
        ("not to accumulate", "hold"),
        ("不建议卖出", "hold"),
        ("无需卖出", "hold"),
        ("无须卖出", "hold"),
        ("不要卖出", "hold"),
        ("暂不卖出", "hold"),
        ("no sell", "hold"),
        ("no need to sell", "hold"),
        ("cannot sell", "hold"),
        ("can't sell", "hold"),
        ("not a sell yet", "hold"),
        ("not to sell", "hold"),
        ("无需减仓", "hold"),
        ("无须减仓", "hold"),
        ("no reduce", "hold"),
        ("no need to reduce", "hold"),
        ("cannot reduce", "hold"),
        ("not to reduce", "hold"),
        ("no trim", "hold"),
        ("can't trim", "hold"),
        ("not a trim yet", "hold"),
        ("not to trim", "hold"),
        ("avoid selling into weakness", "hold"),
        ("avoid trimming before earnings", "hold"),
        ("avoid reducing exposure before earnings", "hold"),
        ("不建议清仓", "hold"),
    ],
)
def test_normalize_decision_action_handles_negated_trade_actions(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize(
    "advice",
    [
        "无需买入，等待确认",
        "无须建仓，继续观察",
        "无需布局，等待突破",
        "no buy until breakout",
        "no need to buy before confirmation",
        "cannot buy before confirmation",
        "can't buy before confirmation",
        "not a buy yet",
        "not to buy",
    ],
)
def test_build_action_fields_prioritizes_negated_buy_advice_over_embedded_buy_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "avoid",
        "action_label": "回避",
    }


def test_build_action_fields_keeps_compound_no_separator_avoid_buy_with_score_alignment() -> None:
    assert build_action_fields(
        operation_advice="回避买入",
        sentiment_score=90,
        align_with_score=True,
    ) == {
        "action": "avoid",
        "action_label": "回避",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "无须加仓，维持仓位",
        "无需卖出，继续持有",
        "无须减仓，等待确认",
        "no add before confirmation",
        "cannot add before confirmation",
        "no need to accumulate here",
        "can't accumulate here",
        "no sell before earnings",
        "cannot sell before earnings",
        "no need to reduce exposure",
        "can't reduce exposure",
        "no trim while trend holds",
        "cannot trim while trend holds",
        "not a sell yet",
        "not a trim yet",
        "not to sell",
        "not to trim",
        "avoid selling into weakness",
        "avoid trimming before earnings",
        "avoid reducing exposure before earnings",
    ],
)
def test_build_action_fields_prioritizes_negated_hold_advice_over_embedded_trade_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "hold",
        "action_label": "持有",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "预警，不建议买入",
        "alert, do not buy",
        "风险预警，避免买入",
        "risk alert, do not buy",
        "risk alert, avoid buying",
        "风险预警，不建议买入",
    ],
)
def test_build_action_fields_preserves_negated_warning_advice_before_score_fallback(advice: str) -> None:
    assert build_action_fields(
        operation_advice=advice,
        sentiment_score=90,
        align_with_score=True,
    ) == {
        "action": "avoid",
        "action_label": "回避",
    }
    assert display_operation_advice(
        operation_advice=advice,
        sentiment_score=90,
    ) == "回避"


def test_build_action_fields_prefers_trade_term_over_alert_prefix() -> None:
    assert build_action_fields(
        operation_advice="风险预警，建议卖出",
        align_with_score=True,
    ) == {
        "action": "sell",
        "action_label": "卖出",
    }

    assert build_action_fields(
        operation_advice="Alert, sell",
        report_language="en",
        align_with_score=True,
    ) == {
        "action": "sell",
        "action_label": "Sell",
    }

    assert build_action_fields(
        operation_advice="경고, 매도",
        report_language="ko",
        align_with_score=True,
    ) == {
        "action": "sell",
        "action_label": "매도",
    }

    assert build_action_fields(
        operation_advice="경고, 비중축소",
        report_language="ko",
        align_with_score=True,
    ) == {
        "action": "reduce",
        "action_label": "비중축소",
    }



def test_build_action_fields_keeps_guard_with_neutral_advice_before_score_fallback() -> None:
    assert build_action_fields(
        operation_advice="风险预警，持有",
        sentiment_score=90,
        align_with_score=True,
    ) == {
        "action": "alert",
        "action_label": "预警",
    }

    assert build_action_fields(
        operation_advice="alert, watch",
        sentiment_score=85,
        report_language="en",
        align_with_score=True,
    ) == {
        "action": "alert",
        "action_label": "Alert",
    }

    assert build_action_fields(
        operation_advice="경고, 보유",
        sentiment_score=88,
        report_language="ko",
        align_with_score=True,
    ) == {
        "action": "alert",
        "action_label": "경고",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "买盘增强，继续观察",
        "卖压缓解，继续观察",
        "卖方评级分歧",
    ],
)
def test_build_action_fields_keeps_chinese_financial_context_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    "advice",
    [
        "no buyback announced",
        "cannot buyback shares now",
        "no selloff risk",
        "not selloff yet",
    ],
)
def test_build_action_fields_keeps_financial_compound_terms_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    "advice",
    [
        "share buy-back announced",
        "share buy back announced",
        "sell-off risk remains low",
        "sell off risk remains low",
        "no sell-off pressure",
    ],
)
def test_build_action_fields_keeps_hyphenated_financial_compound_terms_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    ("advice", "expected_action", "expected_label"),
    [
        ("buy after sell-off", "buy", "买入"),
        ("sell after buy-back rumor", "sell", "卖出"),
    ],
)
def test_financial_compound_mask_preserves_separate_action_terms(
    advice: str,
    expected_action: str,
    expected_label: str,
) -> None:
    assert normalize_decision_action(advice) == expected_action
    assert build_action_fields(operation_advice=advice) == {
        "action": expected_action,
        "action_label": expected_label,
    }


def test_localize_action_label_uses_report_language() -> None:
    assert localize_action_label("avoid", "zh") == "回避"
    assert localize_action_label("avoid", "en") == "Avoid"


def test_build_action_fields_respects_market_review_exclusion() -> None:
    fields = build_action_fields(
        operation_advice="买入",
        explicit_action="buy",
        report_type="market_review",
    )

    assert fields == {"action": None, "action_label": None}


def test_build_action_fields_prefers_explicit_action_over_advice() -> None:
    fields = build_action_fields(
        operation_advice="买入",
        explicit_action="watch",
        report_language="zh",
    )

    assert fields == {"action": "watch", "action_label": "观望"}


def test_build_action_fields_keeps_empty_action_without_advice_or_explicit_action() -> None:
    fields = build_action_fields(
        operation_advice=None,
        report_language="zh",
    )

    assert fields == {"action": None, "action_label": None}


@pytest.mark.parametrize(
    ("score", "expected_signal", "expected_action", "expected_decision_type"),
    [
        (28, "reduce", "reduce", "sell"),
        (38, "reduce", "reduce", "sell"),
        (42, "watch", "watch", "hold"),
        (55, "watch", "watch", "hold"),
        (60, "buy", "buy", "buy"),
        (66, "buy", "buy", "buy"),
        (72, "buy", "buy", "buy"),
    ],
)
def test_canonical_score_scale_boundaries(
    score: int,
    expected_signal: str,
    expected_action: str,
    expected_decision_type: str,
) -> None:
    assert signal_key_for_score(score) == expected_signal
    assert action_for_score(score) == expected_action
    assert decision_type_for_score(score) == expected_decision_type


def test_build_action_fields_can_align_neutral_action_with_directional_score() -> None:
    assert build_action_fields(
        operation_advice="持有",
        sentiment_score=72,
        align_with_score=True,
    ) == {"action": "buy", "action_label": "买入"}

    assert build_action_fields(
        operation_advice="观望",
        sentiment_score=28,
        align_with_score=True,
    ) == {"action": "reduce", "action_label": "减仓"}


def test_build_action_fields_keeps_neutral_score_conflict_when_guardrail_is_explicit() -> None:
    assert build_action_fields(
        operation_advice="持有/观望待回踩",
        sentiment_score=72,
        guardrail_reason="等待回踩确认",
        align_with_score=True,
    ) == {"action": "watch", "action_label": "观望"}


def test_display_operation_advice_aligns_with_score_without_guardrail() -> None:
    assert display_operation_advice(
        operation_advice="持有",
        sentiment_score=72,
        report_language="zh",
    ) == "买入"


def test_display_operation_advice_keeps_guardrailed_neutral_advice() -> None:
    assert display_operation_advice(
        operation_advice="持有",
        sentiment_score=72,
        guardrail_reason="等待回踩确认",
        report_language="zh",
    ) == "持有"


@pytest.mark.parametrize(
    ("advice", "language", "expected"),
    [
        ("风险预警，观望", "zh", "预警"),
        ("alert, watch", "en", "Alert"),
        ("경고, 보유", "ko", "경고"),
    ],
)
def test_display_operation_advice_keeps_guard_with_neutral_advice(
    advice: str,
    language: str,
    expected: str,
) -> None:
    assert display_operation_advice(
        operation_advice=advice,
        sentiment_score=90,
        report_language=language,
    ) == expected


def test_display_decision_type_for_result_uses_display_action_bucket() -> None:
    class Result:
        operation_advice = "持有"
        sentiment_score = 72
        report_language = "zh"
        action = None
        action_label = None
        dashboard = None

    assert display_decision_type_for_result(Result()) == "buy"


@pytest.mark.parametrize("decision_type, expected", [("buy", "buy"), ("sell", "sell")])
def test_display_decision_type_for_result_falls_back_to_decision_type(
    decision_type: str,
    expected: str,
) -> None:
    class Result:
        operation_advice = "未知波动信号"
        sentiment_score = 72
        report_language = "zh"
        action = None
        action_label = None
        dashboard = None

    setattr(Result, "decision_type", decision_type)

    assert display_decision_type_for_result(Result()) == expected


def test_display_action_fields_falls_back_to_action_label_when_explicit_action_is_blank() -> None:
    assert display_action_fields(
        operation_advice="持有",
        explicit_action="",
        action_label="回避",
        sentiment_score=72,
        report_language="zh",
    ) == {
        "action": "avoid",
        "action_label": "回避",
    }


@pytest.mark.parametrize("explicit_action", ["unknown", "N/A"])
def test_display_action_fields_falls_back_to_action_label_when_explicit_action_is_invalid(
    explicit_action: str,
) -> None:
    assert display_action_fields(
        operation_advice="持有",
        explicit_action=explicit_action,
        action_label="回避",
        sentiment_score=72,
        report_language="zh",
    ) == {
        "action": "avoid",
        "action_label": "回避",
    }


def test_display_action_fields_preserves_strong_buy_label_for_explicit_advice() -> None:
    assert display_action_fields(
        operation_advice="强烈买入",
        sentiment_score=72,
        report_language="zh",
    ) == {
        "action": "buy",
        "action_label": "强烈买入",
    }

    assert display_action_fields(
        operation_advice="strong buy",
        sentiment_score=72,
        report_language="en",
    ) == {
        "action": "buy",
        "action_label": "Strong Buy",
    }


def test_display_action_fields_prefers_strong_buy_action_label_when_explicit_label_supplied() -> None:
    assert display_action_fields(
        operation_advice="持有",
        action_label="强烈买入",
        sentiment_score=72,
        report_language="zh",
    ) == {
        "action": "buy",
        "action_label": "强烈买入",
    }


def test_display_action_fields_prefers_strong_buy_action_label_when_explicit_action_is_set() -> None:
    assert display_action_fields(
        operation_advice="持有",
        explicit_action="buy",
        action_label="强烈买入",
        sentiment_score=72,
        report_language="zh",
    ) == {
        "action": "buy",
        "action_label": "强烈买入",
    }


@pytest.mark.parametrize("operation_advice", ["不建议卖出", "不建议加仓"])
def test_build_action_fields_keeps_negated_hold_over_legacy_decision_type(
    operation_advice: str,
) -> None:
    assert build_action_fields(
        operation_advice=operation_advice,
        legacy_decision_type="sell",
        sentiment_score=50,
    ) == {
        "action": "hold",
        "action_label": "持有",
    }


def test_display_action_fields_for_result_falls_back_when_result_action_is_blank() -> None:
    class Result:
        operation_advice = "持有"
        action = ""
        action_label = "回避"
        decision_type = "hold"
        report_language = "zh"
        report_type = None
        sentiment_score = 72
        dashboard = None
        guardrail_reason = None

    assert display_action_fields_for_result(Result(), report_language="zh") == {
        "action": "avoid",
        "action_label": "回避",
    }


def test_extract_decision_guardrail_reason_reads_dashboard_sources() -> None:
    assert extract_decision_guardrail_reason(
        {
            "dashboard": {
                "decision_score_calibration": {
                    "guardrail_reason": "wait for support confirmation",
                }
            }
        }
    ) == "wait for support confirmation"

    assert extract_decision_guardrail_reason(
        {
            "dashboard": {
                "decision_stability": {
                    "applied": True,
                    "reason": "capital flow is unavailable",
                }
            }
        }
    ) == "capital flow is unavailable"


def test_extract_decision_guardrail_reason_ignores_unapplied_stability_reason() -> None:
    assert (
        extract_decision_guardrail_reason(
            {
                "dashboard": {
                    "decision_stability": {
                        "applied": False,
                        "reason": "资金流不可用，未使用资金流校准",
                    }
                }
            }
        )
        is None
    )
    assert (
        extract_decision_guardrail_reason(
            {
                "dashboard": {
                    "decision_stability": {
                        "applied": False,
                        "downgrade_reason": "资金流仍偏弱，暂按观望处理",
                    }
                }
            }
        )
        is None
    )
