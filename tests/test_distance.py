"""P5 距离计算测试。

覆盖：
- Haversine 距离计算
- 3km 判断边界（2999m / 3000m / 3001m）
- 同点距离为 0
- 坐标为 None 时返回 None
- 已知坐标对距离验证
"""

from __future__ import annotations

import math

import pytest

from boss_tool.geo.distance import (
    DISTANCE_3KM_M,
    EARTH_RADIUS_M,
    Coordinate,
    DistanceResult,
    calculate_distance,
    haversine_distance_m,
    is_within_3km,
)


class TestHaversineDistance:
    """Haversine 距离计算测试。"""

    def test_same_point_zero_distance(self) -> None:
        """同点距离为 0。"""
        c = Coordinate(longitude=120.1769, latitude=30.2761)
        distance = haversine_distance_m(c, c)
        assert distance == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_short(self) -> None:
        """已知短距离验证（约 1km）。

        杭州建国北路附近两点，纬度差约 0.01 度 ≈ 1.1km。
        """
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        c2 = Coordinate(longitude=120.1769, latitude=30.2861)
        distance = haversine_distance_m(c1, c2)
        # 纬度差 0.01 度 ≈ 1111m
        assert 1000 < distance < 1200

    def test_known_distance_long(self) -> None:
        """已知长距离验证（杭州到上海约 170km）。"""
        hangzhou = Coordinate(longitude=120.1769, latitude=30.2761)
        shanghai = Coordinate(longitude=121.4737, latitude=31.2304)
        distance = haversine_distance_m(hangzhou, shanghai)
        # 杭州-上海直线距离约 170km
        assert 160_000 < distance < 180_000

    def test_distance_non_negative(self) -> None:
        """距离非负。"""
        c1 = Coordinate(longitude=120.0, latitude=30.0)
        c2 = Coordinate(longitude=121.0, latitude=31.0)
        distance = haversine_distance_m(c1, c2)
        assert distance >= 0

    def test_symmetric(self) -> None:
        """距离对称性：d(a,b) == d(b,a)。"""
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        c2 = Coordinate(longitude=120.1869, latitude=30.2861)
        d1 = haversine_distance_m(c1, c2)
        d2 = haversine_distance_m(c2, c1)
        assert d1 == pytest.approx(d2, abs=1e-6)


class TestIsWithin3km:
    """3km 判断测试。"""

    def test_within_boundary_2999(self) -> None:
        """2999m 在 3km 内。"""
        assert is_within_3km(2999.0) is True

    def test_boundary_3000(self) -> None:
        """3000m 恰好在 3km 内（<=）。"""
        assert is_within_3km(3000.0) is True

    def test_outside_boundary_3001(self) -> None:
        """3001m 在 3km 外。"""
        assert is_within_3km(3001.0) is False

    def test_zero_distance_within(self) -> None:
        """0m 在 3km 内。"""
        assert is_within_3km(0.0) is True

    def test_negative_distance_within(self) -> None:
        """负距离（异常）也视为 <= 3000，返回 True（防御性）。"""
        assert is_within_3km(-1.0) is True

    def test_large_distance_outside(self) -> None:
        """大距离在 3km 外。"""
        assert is_within_3km(10_000.0) is False


class TestCalculateDistance:
    """calculate_distance 集成测试。"""

    def test_both_coords_present(self) -> None:
        """两坐标都存在时返回 DistanceResult。"""
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        c2 = Coordinate(longitude=120.1769, latitude=30.2761)
        result = calculate_distance(c1, c2)
        assert result is not None
        assert isinstance(result, DistanceResult)
        assert result.distance_meter == pytest.approx(0.0, abs=1e-6)
        assert result.is_within_3km is True

    def test_first_coord_none(self) -> None:
        """第一个坐标为 None 时返回 None。"""
        c2 = Coordinate(longitude=120.0, latitude=30.0)
        assert calculate_distance(None, c2) is None

    def test_second_coord_none(self) -> None:
        """第二个坐标为 None 时返回 None。"""
        c1 = Coordinate(longitude=120.0, latitude=30.0)
        assert calculate_distance(c1, None) is None

    def test_both_coords_none(self) -> None:
        """两坐标都为 None 时返回 None。"""
        assert calculate_distance(None, None) is None

    def test_within_3km_result(self) -> None:
        """3km 内的结果。"""
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        # 纬度差 0.001 度 ≈ 111m
        c2 = Coordinate(longitude=120.1769, latitude=30.2771)
        result = calculate_distance(c1, c2)
        assert result is not None
        assert result.is_within_3km is True
        assert result.distance_meter < 3000

    def test_outside_3km_result(self) -> None:
        """3km 外的结果。"""
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        # 纬度差 0.1 度 ≈ 11km
        c2 = Coordinate(longitude=120.1769, latitude=30.3761)
        result = calculate_distance(c1, c2)
        assert result is not None
        assert result.is_within_3km is False
        assert result.distance_meter > 3000


class TestDistance3kmBoundaryIntegration:
    """距离 2999/3000/3001 边界集成测试。

    通过构造特定距离的坐标对，验证 calculate_distance 在边界附近的行为。
    """

    def _make_coord_at_distance(self, center: Coordinate, target_distance_m: float) -> Coordinate:
        """构造距离 center 指定米数的坐标（沿经度方向）。

        使用与 Haversine 相同的 EARTH_RADIUS_M 计算每度米数：
        meters_per_degree_lon = EARTH_RADIUS_M * cos(lat) * π / 180
        """
        lat_rad = math.radians(center.latitude)
        meters_per_degree_lon = EARTH_RADIUS_M * math.cos(lat_rad) * math.pi / 180.0
        delta_lon = target_distance_m / meters_per_degree_lon
        return Coordinate(longitude=center.longitude + delta_lon, latitude=center.latitude)

    def test_2999m_within_3km(self) -> None:
        """2999m 在 3km 内。"""
        center = Coordinate(longitude=120.1769, latitude=30.2761)
        target = self._make_coord_at_distance(center, 2999.0)
        result = calculate_distance(center, target)
        assert result is not None
        assert result.distance_meter == pytest.approx(2999.0, rel=1e-3)
        assert result.is_within_3km is True

    def test_3000m_boundary_within_3km(self) -> None:
        """3000m 恰好在边界（<=）。"""
        center = Coordinate(longitude=120.1769, latitude=30.2761)
        target = self._make_coord_at_distance(center, 3000.0)
        result = calculate_distance(center, target)
        assert result is not None
        assert result.distance_meter == pytest.approx(3000.0, rel=1e-3)
        assert result.is_within_3km is True

    def test_3001m_outside_3km(self) -> None:
        """3001m 在 3km 外。"""
        center = Coordinate(longitude=120.1769, latitude=30.2761)
        target = self._make_coord_at_distance(center, 3001.0)
        result = calculate_distance(center, target)
        assert result is not None
        assert result.distance_meter == pytest.approx(3001.0, rel=1e-3)
        assert result.is_within_3km is False


class TestConstants:
    """常量一致性测试。"""

    def test_earth_radius_positive(self) -> None:
        assert EARTH_RADIUS_M > 0

    def test_distance_3km_constant(self) -> None:
        assert DISTANCE_3KM_M == 3000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
