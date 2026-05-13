#!/usr/bin/env python3
"""Generate the notification env mapping table for docs from workflow metadata."""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Dict, Iterable, List

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/daily_analysis.yml"
DOCS_PATH = ROOT_DIR / "docs/notifications.md"

START_MARKER = "<!-- GENERATED: notifications-actions-env-table -->"
END_MARKER = "<!-- END GENERATED: notifications-actions-env-table -->"

NOTIFICATION_ENV_PREFIXES = (
    "WECHAT_",
    "FEISHU_",
    "TELEGRAM_",
    "EMAIL_",
    "PUSHOVER_",
    "NTFY_",
    "GOTIFY_",
    "PUSHPLUS_",
    "CUSTOM_WEBHOOK_",
    "DISCORD_",
    "SLACK_",
    "SERVERCHAN3_",
    "ASTRBOT_",
    "NOTIFICATION_",
)


def _load_workflow_env(path: Path = WORKFLOW_PATH) -> Dict[str, str]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["analyze"]["steps"]
    analyze_step = next(
        (step for step in steps if step.get("name") == "执行股票分析"),
        None,
    )
    assert analyze_step is not None, (
        "Expected daily_analysis.yml job analyze to include a step named "
        "'执行股票分析'."
    )
    return dict(analyze_step["env"])


def _is_notification_env_key(key: str) -> bool:
    return key.startswith(NOTIFICATION_ENV_PREFIXES)


def extract_notification_env(env: Dict[str, str]) -> List[tuple[str, str]]:
    return [(key, str(value)) for key, value in env.items() if _is_notification_env_key(key)]


def _clean_mapping_cell(value: str) -> str:
    escaped = value.replace("|", "\\|")
    return f"`{escaped}`"


def build_actions_env_table(
    notification_items: Iterable[tuple[str, str]],
) -> str:
    rows = [
        "| 通知环境变量 | workflow 映射表达式 |",
        "| --- | --- |",
    ]

    for key, value in notification_items:
        rows.append(f"| `{key}` | {_clean_mapping_cell(value)} |")

    return "\n".join(rows)


def build_marked_table_block(notification_items: Iterable[tuple[str, str]]) -> str:
    return "\n".join(
        [
            START_MARKER,
            build_actions_env_table(notification_items),
            END_MARKER,
        ]
    )


def _extract_marked_block(text: str) -> str:
    start = text.index(START_MARKER)
    end = text.index(END_MARKER)
    if end < start:
        raise ValueError("Invalid marker order in docs.")
    return text[start + len(START_MARKER) : end].strip("\n")


def sync_docs(
    *,
    docs_path: Path = DOCS_PATH,
    workflow_path: Path = WORKFLOW_PATH,
    check_only: bool = False,
) -> int:
    workflow_env = _load_workflow_env(workflow_path)
    notification_items = extract_notification_env(workflow_env)
    expected_block = build_marked_table_block(notification_items)

    docs_text = docs_path.read_text(encoding="utf-8")
    if START_MARKER not in docs_text or END_MARKER not in docs_text:
        raise ValueError(
            f"Docs file {docs_path} is missing table markers:"
            f"\n{START_MARKER}\n{END_MARKER}"
        )

    old_block_with_markers = f"{START_MARKER}\n{_extract_marked_block(docs_text)}\n{END_MARKER}"
    if old_block_with_markers == expected_block:
        return 0

    if check_only:
        return 1

    new_text = docs_text.replace(old_block_with_markers, expected_block)
    docs_path.write_text(new_text, encoding="utf-8")
    return 0


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Render/同步每日分析 workflow 的通知环境变量对照表到 docs。"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查 docs 表与 workflow 提取结果是否一致，不写文件。")
    parser.add_argument(
        "--workflow", default=str(WORKFLOW_PATH), help="daily_analysis workflow 路径。"
    )
    parser.add_argument(
        "--docs", default=str(DOCS_PATH), help="通知文档路径。"
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return sync_docs(
        docs_path=Path(args.docs),
        workflow_path=Path(args.workflow),
        check_only=args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
