# -*- coding: utf-8 -*-
"""Prompt rendering for the shared market-structure context."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, List

from src.report_language import normalize_report_language
from src.schemas.market_structure import MARKET_STRUCTURE_SCHEMA_VERSION


def format_market_structure_prompt_section(
    context: Any,
    report_language: str = "zh",
) -> str:
    """Render a compact, low-sensitive market structure section for LLM prompts."""
    if not isinstance(context, dict):
        return ""
    if context.get("schema_version") != MARKET_STRUCTURE_SCHEMA_VERSION:
        return ""
    if context.get("status") == "not_supported":
        return ""

    market_theme = context.get("market_theme_context")
    stock_position = context.get("stock_market_position")
    if not isinstance(market_theme, dict) or not isinstance(stock_position, dict):
        return ""

    language = normalize_report_language(report_language)
    active_themes = _item_names(market_theme.get("active_themes"), limit=5)
    leading_concepts = _item_names(market_theme.get("leading_concepts"), limit=5)
    leading_industries = _item_names(market_theme.get("leading_industries"), limit=5)
    primary_theme = stock_position.get("primary_theme")
    primary_name = (
        str(primary_theme.get("name")).strip()
        if isinstance(primary_theme, dict) and primary_theme.get("name")
        else ""
    )
    risk_tags = [
        str(item.get("code") or item.get("message") or "").strip()
        for item in stock_position.get("risk_tags") or []
        if isinstance(item, dict) and str(item.get("code") or item.get("message") or "").strip()
    ]
    missing_fields = _string_values(stock_position.get("missing_fields"))
    data_quality = market_theme.get("data_quality")
    if isinstance(data_quality, dict):
        missing_fields.extend(_string_values(data_quality.get("missing_fields")))
    missing_fields = list(dict.fromkeys(missing_fields))

    if language == "en":
        lines = [
            "\n## Market Structure Context",
            f"- Status: {context.get('status', 'unknown')}",
        ]
        if active_themes:
            lines.append(f"- Active themes: {', '.join(active_themes)}")
        if leading_concepts:
            lines.append(f"- Leading concepts: {', '.join(leading_concepts)}")
        if leading_industries:
            lines.append(f"- Leading industries: {', '.join(leading_industries)}")
        if primary_name:
            lines.append(f"- Stock primary theme: {primary_name}")
        lines.append(f"- Theme phase: {stock_position.get('theme_phase', 'unknown')}")
        lines.append(f"- Stock role: {stock_position.get('stock_role', 'unknown')}")
        if risk_tags:
            lines.append(f"- Risk tags: {', '.join(risk_tags)}")
        if missing_fields:
            lines.append(f"- Missing evidence: {', '.join(missing_fields)}")
        lines.append("- Guardrail: do not claim leader-stock status without constituent or leader evidence.")
        return "\n".join(lines) + "\n"

    lines = [
        "\n## 市场结构上下文",
        f"- 状态：{context.get('status', 'unknown')}",
    ]
    if active_themes:
        lines.append(f"- 活跃题材：{'，'.join(active_themes)}")
    if leading_concepts:
        lines.append(f"- 领涨概念：{'，'.join(leading_concepts)}")
    if leading_industries:
        lines.append(f"- 领涨行业：{'，'.join(leading_industries)}")
    if primary_name:
        lines.append(f"- 个股主关联题材：{primary_name}")
    lines.append(f"- 题材阶段：{stock_position.get('theme_phase', 'unknown')}")
    lines.append(f"- 个股位置：{stock_position.get('stock_role', 'unknown')}")
    if risk_tags:
        lines.append(f"- 风险标签：{'，'.join(risk_tags)}")
    if missing_fields:
        lines.append(f"- 缺失证据：{'，'.join(missing_fields)}")
    lines.append("- 约束：没有成分股或 leader_stocks 证据时，不要断言个股是题材龙头。")
    return "\n".join(lines) + "\n"


def _item_names(value: Any, *, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []
    names: List[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        change_pct = item.get("change_pct")
        if isinstance(change_pct, (int, float)):
            names.append(f"{name}({change_pct:+.2f}%)")
        else:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _string_values(value: Any) -> List[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    normalized: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized
