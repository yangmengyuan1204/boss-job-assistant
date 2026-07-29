"""P2 解析器模块（纯本地，不依赖 Playwright）。

模块职责：
- page_types: 页面类型识别（基于 DOM 与 URL）
- selectors: 集中式 CSS 选择器候选定义
- sanitization: HTML 最小化与脱敏
- list_page: 列表页解析
- detail_page: 详情页解析
- diagnostics: 解析诊断构建

本模块全部基于 BeautifulSoup，不访问网络、不执行 JavaScript。
"""

from __future__ import annotations

from boss_tool.parsers.detail_page import find_detail_root, parse_detail_page
from boss_tool.parsers.diagnostics import build_detail_diagnostics, build_list_diagnostics
from boss_tool.parsers.list_page import find_job_cards, parse_list_page, parse_single_card
from boss_tool.parsers.page_types import detect_page_type, is_save_fixture_allowed
from boss_tool.parsers.sanitization import (
    compute_sha256,
    sanitize_html,
    sanitize_text,
    sanitize_url,
    scan_high_risk_content,
)
from boss_tool.parsers.selectors import (
    SELECTOR_VERSION,
    SelectorCandidate,
)

__all__ = [
    "SELECTOR_VERSION",
    "SelectorCandidate",
    "detect_page_type",
    "is_save_fixture_allowed",
    "sanitize_html",
    "sanitize_url",
    "sanitize_text",
    "scan_high_risk_content",
    "compute_sha256",
    "parse_list_page",
    "parse_single_card",
    "find_job_cards",
    "parse_detail_page",
    "find_detail_root",
    "build_list_diagnostics",
    "build_detail_diagnostics",
]
