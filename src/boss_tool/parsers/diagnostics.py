"""P2 解析诊断构建器。

解析器不得只返回成功或失败，必须能说明：
- 找到多少岗位卡片
- 每个关键选择器命中多少
- 哪些字段缺失
- 哪些字段出现多个候选（歧义）
- 页面结构是否可能变化
- 是否建议人工复查 fixture

本模块接收 list_page / detail_page 的解析结果，构建 ParseDiagnostics。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from boss_tool.models.observed_page import (
    ObservedJobCard,
    ObservedJobDetail,
    PageType,
    ParseDiagnostics,
)
from boss_tool.parsers.detail_page import find_detail_root
from boss_tool.parsers.list_page import find_job_cards, parse_single_card
from boss_tool.parsers.selectors import (
    DETAIL_FIELD_SELECTORS,
    LIST_CARD_FIELD_SELECTORS,
    PAGE_LEVEL_SELECTORS,
    SELECTOR_VERSION,
)


# ==================== 列表页诊断 ====================
def build_list_diagnostics(
    soup: BeautifulSoup,
    cards: list[ObservedJobCard],
    base_url: str | None = None,
) -> ParseDiagnostics:
    """构建列表页解析诊断。

    Args:
        soup: BeautifulSoup 对象
        cards: parse_list_page 返回的卡片列表
        base_url: 基础 URL

    Returns:
        ParseDiagnostics
    """
    # 页面级根选择器命中数
    root_matches: dict[str, int] = {}
    for sel in PAGE_LEVEL_SELECTORS["search_list_root"]:
        try:
            count = len(soup.select(sel))
        except Exception:
            count = 0
        if count > 0:
            root_matches[sel] = count

    # 卡片级命中数
    card_tags = find_job_cards(soup)
    root_matches["job_card_total"] = len(card_tags)

    # 字段级命中统计（汇总所有卡片）
    field_matches: dict[str, int] = {}
    ambiguous_fields: list[str] = []
    missing_required_fields: list[str] = []

    # 重新解析每个卡片以收集命中数
    total_field_hits: dict[str, int] = {c.name: 0 for c in LIST_CARD_FIELD_SELECTORS}
    for idx, card_tag in enumerate(card_tags):
        _, field_hits = parse_single_card(card_tag, idx, base_url=base_url)
        for name, hits in field_hits.items():
            total_field_hits[name] = total_field_hits.get(name, 0) + hits

    for candidate in LIST_CARD_FIELD_SELECTORS:
        name = candidate.name
        hits = total_field_hits.get(name, 0)
        field_matches[name] = hits
        # 必填字段全部缺失
        if candidate.required and hits == 0:
            missing_required_fields.append(name)

    # 检查有效卡片数
    valid_cards = [c for c in cards if c.job_name is not None or c.job_url is not None]
    skipped_cards = len(cards) - len(valid_cards)

    warnings: list[str] = []
    if skipped_cards > 0:
        warnings.append(f"{skipped_cards} 个卡片缺少岗位名和岗位URL，已标记为可能跳过")
    if not cards:
        warnings.append("未解析到任何岗位卡片，页面结构可能已变化或选择器失效")
    if missing_required_fields:
        warnings.append(f"必填字段全部缺失: {missing_required_fields}")

    # 是否建议人工复查
    suggest_review = (
        not cards or skipped_cards > 0 or bool(missing_required_fields) or len(root_matches) == 0
    )

    return ParseDiagnostics(
        page_type=PageType.SEARCH_LIST,
        selector_version=SELECTOR_VERSION,
        root_matches=root_matches,
        field_matches=field_matches,
        missing_required_fields=missing_required_fields,
        ambiguous_fields=ambiguous_fields,
        warnings=warnings,
        parser_success=len(cards) > 0,
        card_count=len(cards),
        suggest_manual_review=suggest_review,
    )


# ==================== 详情页诊断 ====================
def build_detail_diagnostics(
    soup: BeautifulSoup,
    detail: ObservedJobDetail,
    field_hits: dict[str, int] | None = None,
) -> ParseDiagnostics:
    """构建详情页解析诊断。

    Args:
        soup: BeautifulSoup 对象
        detail: parse_detail_page 返回的详情对象
        field_hits: parse_detail_page 返回的字段命中数

    Returns:
        ParseDiagnostics
    """
    root_matches: dict[str, int] = {}
    for sel in PAGE_LEVEL_SELECTORS["detail_root"]:
        try:
            count = len(soup.select(sel))
        except Exception:
            count = 0
        if count > 0:
            root_matches[sel] = count

    root = find_detail_root(soup)
    root_matches["detail_root_found"] = 1 if root is not None else 0

    field_matches: dict[str, int] = field_hits or {}
    missing_required_fields: list[str] = []
    ambiguous_fields: list[str] = []

    for candidate in DETAIL_FIELD_SELECTORS:
        name = candidate.name
        hits = field_matches.get(name, 0)
        if candidate.required and hits == 0:
            missing_required_fields.append(name)

    warnings: list[str] = list(detail.warnings)
    if root is None:
        warnings.append("未找到详情页主容器，可能不是岗位详情页，拒绝使用详情解析器结果")
    if missing_required_fields:
        warnings.append(f"必填字段缺失: {missing_required_fields}")

    # 非岗位详情页：parser_success=False
    parser_success = root is not None and detail.job_name is not None

    suggest_review = root is None or bool(missing_required_fields) or not parser_success

    return ParseDiagnostics(
        page_type=PageType.JOB_DETAIL if root is not None else PageType.UNKNOWN,
        selector_version=SELECTOR_VERSION,
        root_matches=root_matches,
        field_matches=field_matches,
        missing_required_fields=missing_required_fields,
        ambiguous_fields=ambiguous_fields,
        warnings=warnings,
        parser_success=parser_success,
        card_count=0,
        suggest_manual_review=suggest_review,
    )


__all__ = [
    "build_list_diagnostics",
    "build_detail_diagnostics",
    "SELECTOR_VERSION",
]
