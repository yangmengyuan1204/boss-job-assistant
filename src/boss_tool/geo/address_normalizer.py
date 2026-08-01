"""P5 地址标准化模块。

规则：
1. 统一前缀：浙江省 / 杭州市 / 拱墅区
   - 允许 "建国北路88号" → "浙江省杭州市拱墅区建国北路88号"
   - 已含前缀的不重复添加
2. 全角/半角统一：
   - 全角数字字母 → 半角
   - 全角空格 → 半角空格
3. 空白规范化：
   - 删除所有空白字符（中文地址不使用空格分隔）
   - "建国北路  88号" 与 "建国北路88号" 标准化结果相同
4. 多余标点删除：
   - 删除连续重复的标点（如 "，，" → "，"）
   - 删除地址段末尾多余标点
   - 不删除门牌号中的连字符（如 "1-3号"）
5. 保留：
   - 门牌号（如 "88号"）
   - 楼栋号（如 "3栋"）
   - 园区名称（如 "锦园小区"）
   - 真实地址信息不得删除

输入为空或仅空白时返回 None。
"""

from __future__ import annotations

import re
import unicodedata

# ==================== 固定前缀 ====================
# 参考地点：杭州市拱墅区建国北路锦园小区
# 标准化时统一补全省级/市级/区级前缀
PROVINCE_PREFIX = "浙江省"
CITY_PREFIX = "杭州市"
DISTRICT_PREFIX = "拱墅区"

# 完整前缀（用于拼接）
FULL_PREFIX = f"{PROVINCE_PREFIX}{CITY_PREFIX}{DISTRICT_PREFIX}"

# 已知前缀的正则模式（匹配各种简写形式）
# 例如："浙江杭州拱墅"、"浙江省杭州市拱墅区"、"杭州拱墅区" 等
_PROVINCE_PATTERN = re.compile(r"浙江(省)?")
_CITY_PATTERN = re.compile(r"杭州(市)?")
_DISTRICT_PATTERN = re.compile(r"拱墅(区)?")

# 全角数字字母 → 半角（NFKC 规范化）
# 全角空格 \u3000 也由 NFKC 转为半角空格

# 连续空白（含 \u3000 全角空格、\t \n 等）→ 单个空格
_WHITESPACE_PATTERN = re.compile(r"\s+")

# 标点集合（用于删除连续重复与末尾多余标点）
# 仅处理常见中文标点，不处理门牌号中的连字符
_PUNCTUATIONS = "，。、；：,;:"


def _to_halfwidth(text: str) -> str:
    """全角字符转半角（NFKC 规范化）。

    NFKC 会将：
    - 全角数字（０-９）→ 半角（0-9）
    - 全角字母（Ａ-Ｚ）→ 半角（A-Z）
    - 全角空格（　）→ 半角空格
    - 全角标点（，）→ 保留（中文逗号不在 NFKC 范围）

    注意：中文标点（，。、）不在 NFKC 转换范围，需单独处理。
    """
    return unicodedata.normalize("NFKC", text)


def _normalize_whitespace(text: str) -> str:
    """删除所有空白字符（中文地址不使用空格分隔）。

    "建国北路  88号" → "建国北路88号"
    "  建国北路88号  " → "建国北路88号"
    """
    return _WHITESPACE_PATTERN.sub("", text)


def _collapse_repeated_punctuation(text: str) -> str:
    """折叠连续重复的标点为单个。

    例如："建国北路，，，88号" → "建国北路，88号"
    不处理门牌号中的连字符（-）。
    """
    result = text
    for punct in _PUNCTUATIONS:
        # 连续相同的标点 → 单个
        pattern = re.escape(punct) + r"+"
        result = re.sub(pattern, punct, result)
    return result


def _strip_trailing_punctuation(text: str) -> str:
    """删除地址段末尾多余标点。

    例如："建国北路88号，" → "建国北路88号"
    """
    return text.rstrip(_PUNCTUATIONS + " ")


def _ensure_prefix(text: str) -> str:
    """确保地址包含完整的省/市/区前缀。

    策略：
    1. 检测是否已含省级前缀（浙江/浙江省）
    2. 检测是否已含市级前缀（杭州/杭州市）
    3. 检测是否已含区级前缀（拱墅/拱墅区）
    4. 缺失的前缀按 省级→市级→区级 顺序补全到地址开头

    例如：
    - "建国北路88号" → "浙江省杭州市拱墅区建国北路88号"
    - "杭州市拱墅区建国北路" → "浙江省杭州市拱墅区建国北路"
    - "拱墅区建国北路" → "浙江省杭州市拱墅区建国北路"
    - "浙江省杭州市拱墅区建国北路" → 不变
    """
    # 计算需要补全的前缀
    prefix_parts: list[str] = []

    if not _PROVINCE_PATTERN.search(text):
        prefix_parts.append(PROVINCE_PREFIX)
    if not _CITY_PATTERN.search(text):
        prefix_parts.append(CITY_PREFIX)
    if not _DISTRICT_PATTERN.search(text):
        prefix_parts.append(DISTRICT_PREFIX)

    if not prefix_parts:
        return text

    # 拼接补全的前缀
    prefix = "".join(prefix_parts)
    return prefix + text


def normalize_address(raw: str | None) -> str | None:
    """标准化地址。

    处理步骤：
    1. 空值处理：None / 空字符串 / 纯空白 → None
    2. 全角转半角（NFKC）
    3. 空白规范化（删除所有空白字符，中文地址不使用空格分隔）
    4. 删除多余标点（连续重复标点折叠、末尾标点删除）
    5. 补全省/市/区前缀

    Args:
        raw: 原始地址文本

    Returns:
        标准化后的地址；输入为空时返回 None

    Examples:
        >>> normalize_address("建国北路88号")
        '浙江省杭州市拱墅区建国北路88号'
        >>> normalize_address("杭州市拱墅区 建国北路88号")
        '浙江省杭州市拱墅区建国北路88号'
        >>> normalize_address(None)
    """
    if raw is None:
        return None

    # 1. 全角转半角
    text = _to_halfwidth(raw)

    # 2. 空白规范化
    text = _normalize_whitespace(text)

    if not text:
        return None

    # 3. 删除多余标点
    text = _collapse_repeated_punctuation(text)
    text = _strip_trailing_punctuation(text)
    text = _normalize_whitespace(text)

    if not text:
        return None

    # 4. 补全前缀
    text = _ensure_prefix(text)

    return text


__all__ = [
    "normalize_address",
    "PROVINCE_PREFIX",
    "CITY_PREFIX",
    "DISTRICT_PREFIX",
    "FULL_PREFIX",
]
