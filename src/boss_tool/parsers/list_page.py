"""P2 列表页解析器（纯本地，基于 BeautifulSoup）。

解析规则：
- 每个岗位卡片独立解析
- 一个卡片字段缺失不能导致整页失败
- 保留卡片原始顺序，记录 source_index
- 去除重复空白
- 标签去重但保持顺序
- URL 标准化（绝对化、去 query/fragment、只允许 BOSS 官方域名）
- 不对薪资做数值解析
- 不做年龄判断、距离判断、劳动强度判断
- 连岗位名和岗位URL都没有的卡片：记录警告，可跳过，诊断中统计

本模块不依赖 Playwright，不访问网络。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from boss_tool.models.observed_page import ObservedJobCard, ParseDiagnostics
from boss_tool.parsers.sanitization import sanitize_url
from boss_tool.parsers.selectors import (
    LIST_CARD_FIELD_SELECTORS,
    PAGE_LEVEL_SELECTORS,
    SELECTOR_VERSION,
    SelectorCandidate,
)


def _normalize_text(text: str | None) -> str | None:
    """规范化文本：去除首尾空白，合并行内连续空白。"""
    if not text:
        return None
    result = re.sub(r"\s+", " ", text).strip()
    return result or None


def _extract_text(tag: Tag | None) -> str | None:
    """从单个标签提取规范化文本。"""
    if tag is None:
        return None
    text = tag.get_text(separator=" ", strip=True)
    return _normalize_text(text)


def _extract_single_field(card: Tag, candidate: SelectorCandidate) -> tuple[str | None, int]:
    """提取单值字段。返回 (值, 命中候选选择器数)。"""
    for sel in candidate.selectors:
        try:
            found = card.select_one(sel)
        except Exception:
            continue
        if found is not None:
            text = _extract_text(found)
            if text:
                return text, 1
    return None, 0


def _extract_multi_field(card: Tag, candidate: SelectorCandidate) -> tuple[list[str], int]:
    """提取多值字段（benefits/tags）。返回 (去重保持顺序的列表, 总命中数)。"""
    all_values: list[str] = []
    for sel in candidate.selectors:
        try:
            found_list = card.select(sel)
        except Exception:
            continue
        for el in found_list:
            text = _extract_text(el)
            if text:
                all_values.append(text)
    # 去重但保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for v in all_values:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique, len(all_values)


def _extract_url_field(
    card: Tag, candidate: SelectorCandidate, base_url: str | None = None
) -> tuple[str | None, int]:
    """提取 URL 字段（job_url / company_url）。"""
    for sel in candidate.selectors:
        try:
            found = card.select_one(sel)
        except Exception:
            continue
        if found is None:
            continue
        href = found.get("href")
        if href:
            sanitized = sanitize_url(href, base=base_url)
            if sanitized:
                return sanitized, 1
    return None, 0


def find_job_cards(soup: BeautifulSoup) -> list[Tag]:
    """查找所有岗位卡片元素（多候选选择器去重合并）。"""
    seen_ids: set[int] = set()
    cards: list[Tag] = []
    for sel in PAGE_LEVEL_SELECTORS["job_card"]:
        try:
            found = soup.select(sel)
        except Exception:
            continue
        for el in found:
            if id(el) not in seen_ids:
                seen_ids.add(id(el))
                cards.append(el)
    return cards


def parse_list_page(
    html: str,
    base_url: str | None = None,
) -> list[ObservedJobCard]:
    """解析列表页 HTML。

    保留卡片原始顺序（source_index）。
    连岗位名和岗位URL都没有的卡片也会被返回（warnings 记录），
    调用方可按需过滤。

    Args:
        html: 列表页 HTML 字符串
        base_url: 基础 URL（用于相对 URL 转换）

    Returns:
        所有岗位卡片列表（含无效卡片，由 warnings 标记）
    """
    soup = BeautifulSoup(html, "lxml")
    card_tags = find_job_cards(soup)

    cards: list[ObservedJobCard] = []
    for index, card_tag in enumerate(card_tags):
        card, _ = _parse_single_card(card_tag, index, base_url=base_url)
        cards.append(card)
    return cards


def parse_list_page_with_diagnostics(
    html: str,
    base_url: str | None = None,
) -> tuple[list[ObservedJobCard], ParseDiagnostics]:
    """解析列表页 HTML 并返回诊断信息。

    复用 P2 已有的 parse_list_page 与 build_list_diagnostics，不重写解析逻辑。
    使用延迟导入避免与 diagnostics 模块的循环导入。

    Args:
        html: 列表页 HTML 字符串
        base_url: 基础 URL（用于相对 URL 转换）

    Returns:
        (cards, diagnostics): 卡片列表与解析诊断
    """
    # 延迟导入：diagnostics.py 依赖 list_page.py，避免循环导入
    from boss_tool.parsers.diagnostics import build_list_diagnostics

    soup = BeautifulSoup(html, "lxml")
    card_tags = find_job_cards(soup)

    cards: list[ObservedJobCard] = []
    for index, card_tag in enumerate(card_tags):
        card, _ = _parse_single_card(card_tag, index, base_url=base_url)
        cards.append(card)

    diagnostics = build_list_diagnostics(soup, cards, base_url=base_url)
    return cards, diagnostics


def parse_single_card(
    card_tag: Tag,
    source_index: int,
    base_url: str | None = None,
) -> tuple[ObservedJobCard, dict[str, int]]:
    """解析单个岗位卡片（公开，供 diagnostics 复用）。"""
    return _parse_single_card(card_tag, source_index, base_url=base_url)


def _parse_single_card(
    card_tag: Tag,
    source_index: int,
    base_url: str | None = None,
) -> tuple[ObservedJobCard, dict[str, int]]:
    """解析单个岗位卡片。

    Returns:
        (card, field_hit_counts): 卡片对象与各字段命中数
    """
    warnings: list[str] = []
    field_hits: dict[str, int] = {}
    field_values: dict[str, str | None | list[str]] = {}

    for candidate in LIST_CARD_FIELD_SELECTORS:
        name = candidate.name
        if name in ("job_url", "company_url"):
            value, hits = _extract_url_field(card_tag, candidate, base_url=base_url)
            field_values[name] = value
        elif candidate.multiple:
            value_list, hits = _extract_multi_field(card_tag, candidate)
            field_values[name] = value_list
        else:
            value, hits = _extract_single_field(card_tag, candidate)
            field_values[name] = value
        field_hits[name] = hits

    # 必填字段缺失检查：job_name 和 job_url 都缺失才标记为无效卡片
    if field_values.get("job_name") is None and field_values.get("job_url") is None:
        warnings.append("卡片缺少岗位名和岗位URL，可能为非岗位卡片，建议跳过")

    card = ObservedJobCard(
        source_index=source_index,
        job_name=field_values.get("job_name"),  # type: ignore[arg-type]
        job_url=field_values.get("job_url"),  # type: ignore[arg-type]
        salary_text=field_values.get("salary_text"),  # type: ignore[arg-type]
        area_text=field_values.get("area_text"),  # type: ignore[arg-type]
        experience_text=field_values.get("experience_text"),  # type: ignore[arg-type]
        education_text=field_values.get("education_text"),  # type: ignore[arg-type]
        company_name=field_values.get("company_name"),  # type: ignore[arg-type]
        company_url=field_values.get("company_url"),  # type: ignore[arg-type]
        company_industry=field_values.get("company_industry"),  # type: ignore[arg-type]
        company_size=field_values.get("company_size"),  # type: ignore[arg-type]
        recruiter_name=field_values.get("recruiter_name"),  # type: ignore[arg-type]
        recruiter_title=field_values.get("recruiter_title"),  # type: ignore[arg-type]
        recruiter_active_text=field_values.get("recruiter_active_text"),  # type: ignore[arg-type]
        benefits=field_values.get("benefits") or [],  # type: ignore[arg-type]
        tags=field_values.get("tags") or [],  # type: ignore[arg-type]
        warnings=warnings,
    )
    return card, field_hits


__all__ = [
    "parse_list_page",
    "parse_list_page_with_diagnostics",
    "parse_single_card",
    "find_job_cards",
    "SELECTOR_VERSION",
]
