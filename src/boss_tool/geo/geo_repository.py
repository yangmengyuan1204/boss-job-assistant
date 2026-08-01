"""P5 地理缓存 Repository。

职责：
- get_cached_coordinate(address): 查询缓存
- save_coordinate(...): 写入缓存（UPSERT）
- resolve_coordinate(address): 解析坐标（先缓存后 API）
- calculate_distance(coord1, coord2): 计算距离与 3km 判断

约束：
- 命中缓存时不得访问网络
- 未命中时调用 AmapGeocoder（如已注入）
- API 成功 → 写缓存（status=success）
- API 失败 → 写缓存（status=failed），避免重复请求
- 一次运行同一地址最多请求一次（由 AmapGeocoder 内部 Set 保证）
- 不破坏 JobListRepository / JobDetailRepository

缓存表 geo_cache 字段：
- address (PRIMARY KEY): 原始地址
- normalized_address: 标准化地址（建索引，提高命中率）
- longitude / latitude: 坐标
- provider: 地图服务（如 "amap"）
- status: "success" / "failed"
- created_at / updated_at: 时间戳

缓存查询策略：
- 输入地址先标准化
- 按 normalized_address 查询缓存（同一地址不同写法可命中）
- 未命中再请求 API
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from boss_tool.geo.address_normalizer import normalize_address
from boss_tool.geo.distance import Coordinate, DistanceResult, calculate_distance
from boss_tool.logging_config import get_logger

if TYPE_CHECKING:
    from boss_tool.geo.geocoder import AmapGeocoder

logger = get_logger(__name__)

# ==================== 缓存状态常量 ====================
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# 默认 provider
DEFAULT_PROVIDER = "amap"


@dataclass(frozen=True)
class GeoCacheRecord:
    """geo_cache 表记录。

    Attributes:
        address: 原始地址（主键）
        normalized_address: 标准化地址
        longitude: 经度（失败时为 None）
        latitude: 纬度（失败时为 None）
        provider: 地图服务
        status: success / failed
        created_at: 创建时间
        updated_at: 更新时间
    """

    address: str
    normalized_address: str | None
    longitude: float | None
    latitude: float | None
    provider: str
    status: str
    created_at: datetime
    updated_at: datetime


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _from_iso(s: str | None) -> datetime | None:
    if s is None or s == "":
        return None
    return datetime.fromisoformat(s)


class GeoRepository:
    """地理缓存与坐标解析 Repository。

    不破坏 JobListRepository / JobDetailRepository。
    独立操作 geo_cache 表。
    """

    UPSERT_SQL = """
    INSERT INTO geo_cache (
        address, normalized_address, longitude, latitude,
        provider, status, created_at, updated_at
    ) VALUES (
        :address, :normalized_address, :longitude, :latitude,
        :provider, :status, :created_at, :updated_at
    )
    ON CONFLICT(address) DO UPDATE SET
        normalized_address = excluded.normalized_address,
        longitude          = excluded.longitude,
        latitude           = excluded.latitude,
        provider           = excluded.provider,
        status             = excluded.status,
        updated_at         = excluded.updated_at
    """

    SELECT_BY_NORMALIZED_SQL = """
    SELECT address, normalized_address, longitude, latitude,
           provider, status, created_at, updated_at
    FROM geo_cache
    WHERE normalized_address = ?
    """

    SELECT_BY_ADDRESS_SQL = """
    SELECT address, normalized_address, longitude, latitude,
           provider, status, created_at, updated_at
    FROM geo_cache
    WHERE address = ?
    """

    COUNT_SQL = "SELECT COUNT(*) AS cnt FROM geo_cache"

    def __init__(
        self,
        conn: sqlite3.Connection,
        geocoder: AmapGeocoder | None = None,
    ):
        """初始化 GeoRepository。

        Args:
            conn: SQLite 连接
            geocoder: 可选的地理编码客户端；传入后 resolve_coordinate 在缓存未命中时调用 API
        """
        self.conn = conn
        self._geocoder = geocoder

    def get_cached_coordinate(self, address: str) -> GeoCacheRecord | None:
        """查询缓存中的坐标。

        查询策略：
        1. 先标准化地址
        2. 按 normalized_address 查询（提高命中率）
        3. 未命中则按原始 address 查询

        命中缓存时不访问网络。

        Args:
            address: 原始地址

        Returns:
            GeoCacheRecord；未命中返回 None
        """
        if not address or not address.strip():
            return None

        normalized = normalize_address(address)
        if normalized:
            row = self.conn.execute(self.SELECT_BY_NORMALIZED_SQL, (normalized,)).fetchone()
            if row is not None:
                logger.info("地址命中缓存(normalized): addr=%s", address)
                return self._row_to_record(row)

        row = self.conn.execute(self.SELECT_BY_ADDRESS_SQL, (address.strip(),)).fetchone()
        if row is not None:
            logger.info("地址命中缓存(raw): addr=%s", address)
            return self._row_to_record(row)

        logger.info("地址未命中缓存: addr=%s", address)
        return None

    def save_coordinate(
        self,
        address: str,
        normalized_address: str | None,
        longitude: float | None,
        latitude: float | None,
        provider: str,
        status: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """写入缓存（UPSERT）。

        address 为主键，冲突时更新（保留 created_at，更新 updated_at）。

        Args:
            address: 原始地址（主键）
            normalized_address: 标准化地址
            longitude: 经度（失败时为 None）
            latitude: 纬度（失败时为 None）
            provider: 地图服务
            status: success / failed
            now: 当前时间（测试可注入；默认 datetime.now()）
        """
        current = now or datetime.now()
        # created_at：新记录用 current；已存在记录保留原值
        existing = self.conn.execute(self.SELECT_BY_ADDRESS_SQL, (address,)).fetchone()
        created_at = _from_iso(existing["created_at"]) if existing is not None else current

        self.conn.execute(
            self.UPSERT_SQL,
            {
                "address": address,
                "normalized_address": normalized_address,
                "longitude": longitude,
                "latitude": latitude,
                "provider": provider,
                "status": status,
                "created_at": _to_iso(created_at),
                "updated_at": _to_iso(current),
            },
        )

    def resolve_coordinate(self, address: str) -> Coordinate | None:
        """解析地址坐标（先缓存后 API）。

        流程：
        1. 查缓存
        2. 命中且 status=success → 返回 Coordinate（不访问网络）
        3. 命中且 status=failed → 返回 None（避免重复请求）
        4. 未命中 → 调用 geocoder.geocode()（如已注入）
           - 成功 → 写缓存(status=success) → 返回 Coordinate
           - 失败 → 写缓存(status=failed) → 返回 None
        5. 未注入 geocoder → 返回 None（仅查缓存模式）

        Args:
            address: 原始地址

        Returns:
            Coordinate；无法解析时返回 None
        """
        if not address or not address.strip():
            return None

        # 1. 查缓存
        cached = self.get_cached_coordinate(address)
        if cached is not None:
            if (
                cached.status == STATUS_SUCCESS
                and cached.longitude is not None
                and cached.latitude is not None
            ):
                logger.info(
                    "缓存命中(success): addr=%s lon=%s lat=%s",
                    address,
                    cached.longitude,
                    cached.latitude,
                )
                return Coordinate(longitude=cached.longitude, latitude=cached.latitude)
            # status=failed，返回 None，避免重复请求
            logger.info("缓存命中(failed)，跳过 API 请求: addr=%s", address)
            return None

        # 2. 未命中，调用 API（如已注入 geocoder）
        if self._geocoder is None:
            logger.info("未注入 geocoder，无法解析: addr=%s", address)
            return None

        normalized = normalize_address(address)
        result = self._geocoder.geocode(normalized or address)

        if result is not None:
            # 成功 → 写缓存
            self.save_coordinate(
                address=address.strip(),
                normalized_address=normalized,
                longitude=result.longitude,
                latitude=result.latitude,
                provider=DEFAULT_PROVIDER,
                status=STATUS_SUCCESS,
            )
            logger.info(
                "API 解析成功并写入缓存: addr=%s lon=%s lat=%s",
                address,
                result.longitude,
                result.latitude,
            )
            return Coordinate(longitude=result.longitude, latitude=result.latitude)

        # 失败 → 写缓存(status=failed)
        self.save_coordinate(
            address=address.strip(),
            normalized_address=normalized,
            longitude=None,
            latitude=None,
            provider=DEFAULT_PROVIDER,
            status=STATUS_FAILED,
        )
        logger.info("API 解析失败，写入失败缓存: addr=%s", address)
        return None

    def calculate_distance(
        self,
        coord1: Coordinate | None,
        coord2: Coordinate | None,
    ) -> DistanceResult | None:
        """计算两个坐标之间的距离与 3km 判断。

        复用 boss_tool.geo.distance.calculate_distance。
        任一坐标为 None 时返回 None。

        Args:
            coord1: 坐标 1
            coord2: 坐标 2

        Returns:
            DistanceResult 含 distance_meter 与 is_within_3km；
            任一坐标为 None 时返回 None
        """
        result = calculate_distance(coord1, coord2)
        if result is not None:
            logger.info(
                "距离计算: distance=%.2fm within_3km=%s",
                result.distance_meter,
                result.is_within_3km,
            )
        return result

    def count(self) -> int:
        """返回 geo_cache 表总记录数。"""
        row = self.conn.execute(self.COUNT_SQL).fetchone()
        return int(row["cnt"])

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GeoCacheRecord:
        """从数据库行恢复 GeoCacheRecord。"""
        return GeoCacheRecord(
            address=row["address"],
            normalized_address=row["normalized_address"],
            longitude=row["longitude"],
            latitude=row["latitude"],
            provider=row["provider"],
            status=row["status"],
            created_at=_from_iso(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_from_iso(row["updated_at"]),  # type: ignore[arg-type]
        )


__all__ = [
    "GeoRepository",
    "GeoCacheRecord",
    "STATUS_SUCCESS",
    "STATUS_FAILED",
    "DEFAULT_PROVIDER",
]
