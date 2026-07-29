"""P2 详情页解析器（纯本地，基于 BeautifulSoup）。

解析规则：
- 详情页允许字段缺失
- 岗位描述保留换行语义，清除连续多余空行
- 不做关键词判断、年龄规则判断、劳动强度判断
- 不做 HTML 原样存储
- 非岗位详情页拒绝使用详情解析器（调用方应先做页面类型识别）

本模块不依赖 Playwright，不访问网络。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from boss_tool.models.observed_page import ObservedJobDetail
from boss_tool.parsers.sanitization import sanitize_url
from boss_tool.parsers.selectors import (
    DETAIL_FIELD_SELECTORS,
    PAGE_LEVEL_SELECTORS,
    SELECTOR_VERSION,
    SelectorCandidate,
)


def _normalize_text(text: str | None) -> str | None:
    """规范化文本：去除首尾空白，合并行内连续空白。"""
    if not text:
        return None
    result = re.sub(r"[ \t\r\f\v]+", " ", text).strip()
    return result or None


def _normalize_description(text: str | None) -> str | None:
    """规范化岗位描述：保留换行语义，清除连续多余空行，去除首尾空白。"""
    if not text:
        return None
    # 统一换行符
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    # 合并行内连续空白（保留换行）
    lines = result.split("\n")
    cleaned_lines: list[str] = []
    for line in lines:
        cleaned = re.sub(r"[ \t\f\v]+", " ", line).strip()
        cleaned_lines.append(cleaned)
    # 合并连续空行为单个空行
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines))
    result = result.strip()
    return result or None


def _extract_text(tag: Tag | None) -> str | None:
    """从单个标签提取规范化文本。"""
    if tag is None:
        return None
    text = tag.get_text(separator=" ", strip=True)
    return _normalize_text(text)


def _extract_description(tag: Tag | None) -> str | None:
    """提取岗位描述（保留换行语义）。"""
    if tag is None:
        return None
    # 使用 get_text 但保留换行
    text = tag.get_text(separator="\n", strip=False)
    return _normalize_description(text)


def _extract_single_field(container: Tag, candidate: SelectorCandidate) -> tuple[str | None, int]:
    """提取单值字段。"""
    for sel in candidate.selectors:
        try:
            found = container.select_one(sel)
        except Exception:
            continue
        if found is not None:
            if candidate.name == "description":
                text = _extract_description(found)
            else:
                text = _extract_text(found)
            if text:
                return text, 1
    return None, 0


def _extract_multi_field(container: Tag, candidate: SelectorCandidate) -> tuple[list[str], int]:
    """提取多值字段。"""
    all_values: list[str] = []
    for sel in candidate.selectors:
        try:
            found_list = container.select(sel)
        except Exception:
            continue
        for el in found_list:
            text = _extract_text(el)
            if text:
                all_values.append(text)
    seen: set[str] = set()
    unique: list[str] = []
    for v in all_values:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique, len(all_values)


def _extract_url_field(
    container: Tag, candidate: SelectorCandidate, base_url: str | None = None
) -> tuple[str | None, int]:
    """提取 URL 字段。"""
    for sel in candidate.selectors:
        try:
            found = container.select_one(sel)
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


def find_detail_root(soup: BeautifulSoup) -> Tag | None:
    """查找详情页主容器（多候选选择器优先级尝试）。"""
    for sel in PAGE_LEVEL_SELECTORS["detail_root"]:
        try:
            found = soup.select_one(sel)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def parse_detail_page(
    html: str,
    base_url: str | None = None,
) -> tuple[ObservedJobDetail, dict[str, int]]:
    """解析详情页 HTML。

    非岗位详情页（找不到 detail_root）会返回空 detail 并在 warnings 标记。

    Args:
        html: 详情页 HTML 字符串
        base_url: 基础 URL

    Returns:
        (detail, field_hit_counts): 详情对象与各字段命中数
    """
    soup = BeautifulSoup(html, "lxml")
    root = find_detail_root(soup)

    warnings: list[str] = []
    field_hits: dict[str, int] = {}

    if root is None:
        warnings.append("未找到详情页主容器候选，可能不是岗位详情页")
        # 回退到整个 soup 解析（仍尝试提取字段，便于诊断）
        root = soup  # type: ignore[assignment]

    field_values: dict[str, str | None | list[str]] = {}
    for candidate in DETAIL_FIELD_SELECTORS:
        name = candidate.name
        # 详情页没有 company_url 字段，但 company_name 是链接
        if name == "company_name":
            # company_name 通常是 <a>，优先取 href 的 URL，文本为 company_name
            value, hits = _extract_single_field(root, candidate)
            field_values[name] = value
        elif candidate.multiple:
            value_list, hits = _extract_multi_field(root, candidate)
            field_values[name] = value_list
        else:
            value, hits = _extract_single_field(root, candidate)
            field_values[name] = value
        field_hits[name] = hits

    detail = ObservedJobDetail(
        job_name=field_values.get("job_name"),  # type: ignore[arg-type]
        salary_text=field_values.get("salary_text"),  # type: ignore[arg-type]
        location_text=field_values.get("location_text"),  # type: ignore[arg-type]
        experience_text=field_values.get("experience_text"),  # type: ignore[arg-type]
        education_text=field_values.get("education_text"),  # type: ignore[arg-type]
        description=field_values.get("description"),  # type: ignore[arg-type]
        address_text=field_values.get("address_text"),  # type: ignore[arg-type]
        company_name=field_values.get("company_name"),  # type: ignore[arg-type]
        company_industry=field_values.get("company_industry"),  # type: ignore[arg-type]
        company_size=field_values.get("company_size"),  # type: ignore[arg-type]
        recruiter_name=field_values.get("recruiter_name"),  # type: ignore[arg-type]
        recruiter_title=field_values.get("recruiter_title"),  # type: ignore[arg-type]
        recruiter_active_text=field_values.get("recruiter_active_text"),  # type: ignore[arg-type]
        publish_or_active_text=field_values.get("publish_or_active_text"),  # type: ignore[arg-type]
        benefits=field_values.get("benefits") or [],  # type: ignore[arg-type]
        tags=field_values.get("tags") or [],  # type: ignore[arg-type]
        warnings=warnings,
    )
    return detail, field_hits


__all__ = [
    "parse_detail_page",
    "find_detail_root",
    "SELECTOR_VERSION",
]
