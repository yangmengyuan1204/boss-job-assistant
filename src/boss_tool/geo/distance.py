"""P5 距离计算模块。

使用 Haversine 公式计算两点间球面距离。

规则：
- distance <= 3000 米 → is_within_3km = True
- distance > 3000 米 → is_within_3km = False
- 任一坐标为 None → 距离与判断均为 None
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ==================== 常量 ====================
# 地球平均半径（米），WGS84 推荐值
EARTH_RADIUS_M: float = 6_371_000.0

# 3 公里阈值（米）
DISTANCE_3KM_M: float = 3000.0


@dataclass(frozen=True)
class Coordinate:
    """地理坐标（经纬度）。

    Attributes:
        longitude: 经度（东经为正，西经为负）
        latitude: 纬度（北纬为正，南纬为负）
    """

    longitude: float
    latitude: float


@dataclass(frozen=True)
class DistanceResult:
    """距离计算结果。

    Attributes:
        distance_meter: 直线距离（米），>= 0
        is_within_3km: 是否在 3 公里内（distance_meter <= 3000）
    """

    distance_meter: float
    is_within_3km: bool


def haversine_distance_m(coord1: Coordinate, coord2: Coordinate) -> float:
    """计算两个坐标之间的 Haversine 球面距离（米）。

    Haversine 公式：
        a = sin²(Δlat/2) + cos(lat1) * cos(lat2) * sin²(Δlon/2)
        c = 2 * atan2(√a, √(1-a))
        d = R * c

    Args:
        coord1: 坐标 1
        coord2: 坐标 2

    Returns:
        直线距离（米），>= 0

    Examples:
        >>> c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        >>> c2 = Coordinate(longitude=120.1769, latitude=30.2761)
        >>> haversine_distance_m(c1, c2)
        0.0
    """
    lat1_rad = math.radians(coord1.latitude)
    lat2_rad = math.radians(coord2.latitude)
    delta_lat_rad = math.radians(coord2.latitude - coord1.latitude)
    delta_lon_rad = math.radians(coord2.longitude - coord1.longitude)

    a = (
        math.sin(delta_lat_rad / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon_rad / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = EARTH_RADIUS_M * c

    # 浮点误差可能导致极小负值，规范为非负
    if distance < 0:
        distance = 0.0
    return distance


def is_within_3km(distance_meter: float) -> bool:
    """判断距离是否在 3 公里内。

    规则：distance <= 3000 → True，否则 False。

    Args:
        distance_meter: 距离（米）

    Returns:
        True 如果 distance_meter <= 3000
    """
    return distance_meter <= DISTANCE_3KM_M


def calculate_distance(
    coord1: Coordinate | None, coord2: Coordinate | None
) -> DistanceResult | None:
    """计算两个坐标之间的距离与 3km 判断。

    Args:
        coord1: 坐标 1，None 时返回 None
        coord2: 坐标 2，None 时返回 None

    Returns:
        DistanceResult 含 distance_meter 与 is_within_3km；
        任一坐标为 None 时返回 None
    """
    if coord1 is None or coord2 is None:
        return None

    distance = haversine_distance_m(coord1, coord2)
    within = is_within_3km(distance)
    return DistanceResult(distance_meter=distance, is_within_3km=within)


__all__ = [
    "Coordinate",
    "DistanceResult",
    "EARTH_RADIUS_M",
    "DISTANCE_3KM_M",
    "haversine_distance_m",
    "is_within_3km",
    "calculate_distance",
]
