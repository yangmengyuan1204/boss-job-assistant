"""P5 地址标准化测试。

覆盖：
- 基本标准化（补全省/市/区前缀）
- 已含前缀的不重复添加
- 全角/半角统一
- 空白规范化
- 多余标点删除
- 保留门牌号、楼栋号、园区名称
- 空值处理
"""

from __future__ import annotations

import pytest

from boss_tool.geo.address_normalizer import (
    CITY_PREFIX,
    DISTRICT_PREFIX,
    FULL_PREFIX,
    PROVINCE_PREFIX,
    normalize_address,
)


class TestNormalizeAddressBasic:
    """基本标准化测试。"""

    def test_empty_none_returns_none(self) -> None:
        assert normalize_address(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert normalize_address("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert normalize_address("   ") is None
        assert normalize_address("\t\n") is None

    def test_full_width_space_returns_none(self) -> None:
        # 全角空格 \u3000
        assert normalize_address("\u3000\u3000") is None

    def test_add_full_prefix(self) -> None:
        """无前缀的地址补全省/市/区前缀。"""
        result = normalize_address("建国北路88号")
        assert result == f"{FULL_PREFIX}建国北路88号"

    def test_keep_existing_full_prefix(self) -> None:
        """已含完整前缀的不重复添加。"""
        result = normalize_address("浙江省杭州市拱墅区建国北路88号")
        assert result == "浙江省杭州市拱墅区建国北路88号"

    def test_add_province_only(self) -> None:
        """仅缺省级前缀。"""
        result = normalize_address("杭州市拱墅区建国北路88号")
        assert result == "浙江省杭州市拱墅区建国北路88号"

    def test_add_province_and_city(self) -> None:
        """仅缺省级和市级前缀。"""
        result = normalize_address("拱墅区建国北路88号")
        assert result == "浙江省杭州市拱墅区建国北路88号"

    def test_match_short_prefix_forms(self) -> None:
        """简写形式（浙江/杭州/拱墅）也视为已含前缀。"""
        result = normalize_address("浙江杭州拱墅建国北路88号")
        assert result == "浙江杭州拱墅建国北路88号"


class TestNormalizeAddressWidth:
    """全角/半角统一测试。"""

    def test_fullwidth_digits_to_halfwidth(self) -> None:
        """全角数字转半角。"""
        result = normalize_address("建国北路８８号")
        assert "88号" in result
        assert "８８" not in result

    def test_fullwidth_letters_to_halfwidth(self) -> None:
        """全角字母转半角。"""
        result = normalize_address("拱墅区BuildingA")
        # 字母保持半角
        assert "BuildingA" in result

    def test_fullwidth_space_to_halfwidth(self) -> None:
        """全角空格转半角空格。"""
        result = normalize_address("建国北路\u300088号")
        # 不应包含全角空格
        assert "\u3000" not in result


class TestNormalizeAddressWhitespace:
    """空白规范化测试。"""

    def test_collapse_consecutive_spaces(self) -> None:
        """连续空白折叠为单个空格。"""
        result = normalize_address("建国北路   88号")
        assert "  " not in result

    def test_strip_leading_trailing(self) -> None:
        """strip 首尾空白。"""
        result = normalize_address("  建国北路88号  ")
        assert result is not None
        assert not result.startswith(" ")
        assert not result.endswith(" ")


class TestNormalizeAddressPunctuation:
    """多余标点删除测试。"""

    def test_collapse_repeated_punctuation(self) -> None:
        """连续重复标点折叠为单个。"""
        result = normalize_address("建国北路，，，88号")
        assert "，，" not in result

    def test_strip_trailing_punctuation(self) -> None:
        """删除末尾多余标点。"""
        result = normalize_address("建国北路88号，")
        assert result is not None
        assert not result.endswith("，")

    def test_keep_hyphen_in_house_number(self) -> None:
        """门牌号中的连字符保留。"""
        result = normalize_address("建国北路1-3号")
        assert "1-3号" in result


class TestNormalizeAddressPreserveInfo:
    """保留真实地址信息测试。"""

    def test_keep_house_number(self) -> None:
        """保留门牌号。"""
        result = normalize_address("建国北路88号")
        assert "88号" in result

    def test_keep_building_number(self) -> None:
        """保留楼栋号。"""
        result = normalize_address("建国北路88号3栋")
        assert "3栋" in result

    def test_keep_community_name(self) -> None:
        """保留园区/小区名称。"""
        result = normalize_address("锦园小区")
        assert "锦园小区" in result

    def test_keep_full_detail_address(self) -> None:
        """保留完整详细地址。"""
        raw = "锦园小区3栋2单元501室"
        result = normalize_address(raw)
        assert result is not None
        assert "锦园小区3栋2单元501室" in result


class TestNormalizeAddressPrefixConstants:
    """前缀常量一致性测试。"""

    def test_prefix_constants(self) -> None:
        assert PROVINCE_PREFIX == "浙江省"
        assert CITY_PREFIX == "杭州市"
        assert DISTRICT_PREFIX == "拱墅区"
        assert FULL_PREFIX == "浙江省杭州市拱墅区"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
