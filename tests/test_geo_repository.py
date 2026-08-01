"""P5 GeoRepository 测试。

全部使用 Mock Geocoder，不真实联网。

覆盖：
- 缓存命中（success）→ 不访问网络
- 缓存命中（failed）→ 不访问网络，返回 None
- 缓存未命中 → 请求 API
- API 成功 → 写缓存（status=success）
- API 失败 → 写缓存（status=failed）
- 同地址重复解析 → 第二次走缓存
- SQLite 持久化
- 事务（回滚）
- 距离计算
- get_cached_coordinate 按 normalized_address 命中
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from boss_tool.geo.distance import Coordinate, DistanceResult
from boss_tool.geo.geo_repository import (
    DEFAULT_PROVIDER,
    STATUS_FAILED,
    STATUS_SUCCESS,
    GeoCacheRecord,
    GeoRepository,
)
from boss_tool.geo.geocoder import GeocodeResult
from boss_tool.storage.database import Database


# ==================== Mock Geocoder ====================
class MockGeocoder:
    """Mock 地理编码客户端，不访问网络。

    可配置返回结果或抛异常。
    记录调用次数以便测试验证"同一地址最多请求一次"。
    """

    def __init__(self, results: dict[str, GeocodeResult | None] | None = None):
        self._results = results or {}
        self.call_count = 0
        self.called_addresses: list[str] = []

    def geocode(self, address: str) -> GeocodeResult | None:
        self.call_count += 1
        self.called_addresses.append(address)
        return self._results.get(address)


def _make_geocode_result(lon: float = 120.1769, lat: float = 30.2761) -> GeocodeResult:
    return GeocodeResult(
        formatted_address="浙江省杭州市拱墅区建国北路88号",
        longitude=lon,
        latitude=lat,
    )


@pytest.fixture
def db(tmp_db_path: Path) -> Database:
    """初始化数据库并返回。"""
    d = Database(tmp_db_path)
    d.initialize()
    return d


# ==================== 缓存命中 ====================
class TestCacheHit:
    def test_cache_hit_success_no_network(self, db: Database) -> None:
        """缓存命中 success 时不访问网络。"""
        mock_geocoder = MockGeocoder()
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        # 预写缓存
        repo.save_coordinate(
            address="建国北路88号",
            normalized_address="浙江省杭州市拱墅区建国北路88号",
            longitude=120.1769,
            latitude=30.2761,
            provider="amap",
            status=STATUS_SUCCESS,
        )
        db.commit()

        # 查询应命中缓存，不调用 geocoder
        coord = repo.resolve_coordinate("建国北路88号")
        assert coord is not None
        assert coord.longitude == pytest.approx(120.1769)
        assert coord.latitude == pytest.approx(30.2761)
        assert mock_geocoder.call_count == 0

    def test_cache_hit_by_normalized_address(self, db: Database) -> None:
        """不同写法的同一地址按 normalized_address 命中缓存。"""
        mock_geocoder = MockGeocoder()
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        # 预写缓存（原始地址为 "建国北路88号"）
        repo.save_coordinate(
            address="建国北路88号",
            normalized_address="浙江省杭州市拱墅区建国北路88号",
            longitude=120.1769,
            latitude=30.2761,
            provider="amap",
            status=STATUS_SUCCESS,
        )
        db.commit()

        # 用不同写法查询（标准化后相同）
        coord = repo.resolve_coordinate("建国北路  88号")
        assert coord is not None
        assert mock_geocoder.call_count == 0

    def test_cache_hit_failed_returns_none(self, db: Database) -> None:
        """缓存命中 failed 时返回 None，不请求 API。"""
        mock_geocoder = MockGeocoder()
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        repo.save_coordinate(
            address="失败的地址",
            normalized_address="浙江省杭州市拱墅区失败的地址",
            longitude=None,
            latitude=None,
            provider="amap",
            status=STATUS_FAILED,
        )
        db.commit()

        coord = repo.resolve_coordinate("失败的地址")
        assert coord is None
        assert mock_geocoder.call_count == 0


# ==================== 缓存未命中 ====================
class TestCacheMiss:
    def test_cache_miss_request_api(self, db: Database) -> None:
        """缓存未命中时请求 API。"""
        mock_geocoder = MockGeocoder(
            results={
                "浙江省杭州市拱墅区建国北路88号": _make_geocode_result(),
            }
        )
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        coord = repo.resolve_coordinate("建国北路88号")
        assert coord is not None
        assert coord.longitude == pytest.approx(120.1769)
        assert mock_geocoder.call_count == 1

    def test_api_success_writes_cache(self, db: Database) -> None:
        """API 成功后写入缓存。"""
        mock_geocoder = MockGeocoder(
            results={
                "浙江省杭州市拱墅区建国北路88号": _make_geocode_result(),
            }
        )
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        repo.resolve_coordinate("建国北路88号")
        db.commit()

        # 缓存应存在
        assert repo.count() == 1
        cached = repo.get_cached_coordinate("建国北路88号")
        assert cached is not None
        assert cached.status == STATUS_SUCCESS
        assert cached.longitude == pytest.approx(120.1769)

    def test_api_failure_writes_failed_cache(self, db: Database) -> None:
        """API 失败后写入 failed 缓存。"""
        mock_geocoder = MockGeocoder(
            results={
                "浙江省杭州市拱墅区失败地址": None,
            }
        )
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        coord = repo.resolve_coordinate("失败地址")
        assert coord is None
        db.commit()

        # failed 缓存应存在
        assert repo.count() == 1
        cached = repo.get_cached_coordinate("失败地址")
        assert cached is not None
        assert cached.status == STATUS_FAILED
        assert cached.longitude is None

    def test_no_geocoder_returns_none(self, db: Database) -> None:
        """未注入 geocoder 时缓存未命中返回 None。"""
        repo = GeoRepository(db.connection, geocoder=None)
        coord = repo.resolve_coordinate("某地址")
        assert coord is None


# ==================== 同地址重复解析 ====================
class TestRepeatResolve:
    def test_same_address_second_uses_cache(self, db: Database) -> None:
        """同一地址第二次解析走缓存，不请求 API。"""
        mock_geocoder = MockGeocoder(
            results={
                "浙江省杭州市拱墅区建国北路88号": _make_geocode_result(),
            }
        )
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        # 第一次：缓存未命中，请求 API
        coord1 = repo.resolve_coordinate("建国北路88号")
        assert coord1 is not None
        assert mock_geocoder.call_count == 1

        db.commit()

        # 第二次：缓存命中，不请求 API
        coord2 = repo.resolve_coordinate("建国北路88号")
        assert coord2 is not None
        assert mock_geocoder.call_count == 1  # 仍然只调用 1 次

    def test_failed_cache_prevents_retry(self, db: Database) -> None:
        """failed 缓存阻止重复请求 API。"""
        mock_geocoder = MockGeocoder(
            results={
                "浙江省杭州市拱墅区失败地址": None,
            }
        )
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        # 第一次：API 失败，写 failed 缓存
        coord1 = repo.resolve_coordinate("失败地址")
        assert coord1 is None
        assert mock_geocoder.call_count == 1

        db.commit()

        # 第二次：命中 failed 缓存，不请求 API
        coord2 = repo.resolve_coordinate("失败地址")
        assert coord2 is None
        assert mock_geocoder.call_count == 1


# ==================== SQLite 持久化 ====================
class TestPersistence:
    def test_cache_survives_reopen(self, tmp_db_path: Path) -> None:
        """缓存写入后重新打开数据库仍存在。"""
        db1 = Database(tmp_db_path)
        db1.initialize()
        repo1 = GeoRepository(db1.connection)
        repo1.save_coordinate(
            address="持久化测试",
            normalized_address="浙江省杭州市拱墅区持久化测试",
            longitude=120.0,
            latitude=30.0,
            provider="amap",
            status=STATUS_SUCCESS,
        )
        db1.commit()
        db1.close()

        # 重新打开
        db2 = Database(tmp_db_path)
        db2.initialize()
        repo2 = GeoRepository(db2.connection)
        cached = repo2.get_cached_coordinate("持久化测试")
        assert cached is not None
        assert cached.longitude == pytest.approx(120.0)
        assert cached.status == STATUS_SUCCESS
        db2.close()

    def test_upsert_preserves_created_at(self, db: Database) -> None:
        """UPSERT 时 created_at 保留，updated_at 更新。"""
        repo = GeoRepository(db.connection)
        t1 = datetime(2026, 7, 1, 10, 0, 0)
        t2 = datetime(2026, 8, 1, 10, 0, 0)

        repo.save_coordinate(
            address="UPSERT测试",
            normalized_address="浙江省杭州市拱墅区UPSERT测试",
            longitude=120.0,
            latitude=30.0,
            provider="amap",
            status=STATUS_SUCCESS,
            now=t1,
        )
        db.commit()

        # 再次写入（更新）
        repo.save_coordinate(
            address="UPSERT测试",
            normalized_address="浙江省杭州市拱墅区UPSERT测试",
            longitude=121.0,
            latitude=31.0,
            provider="amap",
            status=STATUS_SUCCESS,
            now=t2,
        )
        db.commit()

        cached = repo.get_cached_coordinate("UPSERT测试")
        assert cached is not None
        assert cached.longitude == pytest.approx(121.0)
        assert cached.created_at == t1
        assert cached.updated_at == t2


# ==================== 事务 ====================
class TestTransaction:
    def test_transaction_rollback(self, tmp_db_path: Path) -> None:
        """事务回滚时不写入缓存。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = GeoRepository(db.connection)

        try:
            with db.transaction():
                repo.save_coordinate(
                    address="回滚测试",
                    normalized_address="浙江省杭州市拱墅区回滚测试",
                    longitude=120.0,
                    latitude=30.0,
                    provider="amap",
                    status=STATUS_SUCCESS,
                )
                raise ValueError("触发回滚")
        except ValueError:
            pass

        # 回滚后缓存不应存在
        assert repo.count() == 0
        db.close()

    def test_transaction_commit(self, tmp_db_path: Path) -> None:
        """事务提交时写入缓存。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = GeoRepository(db.connection)

        with db.transaction():
            repo.save_coordinate(
                address="提交测试",
                normalized_address="浙江省杭州市拱墅区提交测试",
                longitude=120.0,
                latitude=30.0,
                provider="amap",
                status=STATUS_SUCCESS,
            )

        assert repo.count() == 1
        db.close()


# ==================== 距离计算 ====================
class TestCalculateDistance:
    def test_calculate_distance_both_coords(self, db: Database) -> None:
        """两坐标都存在时返回距离。"""
        repo = GeoRepository(db.connection)
        c1 = Coordinate(longitude=120.1769, latitude=30.2761)
        c2 = Coordinate(longitude=120.1769, latitude=30.2761)
        result = repo.calculate_distance(c1, c2)
        assert result is not None
        assert isinstance(result, DistanceResult)
        assert result.distance_meter == pytest.approx(0.0, abs=1e-6)
        assert result.is_within_3km is True

    def test_calculate_distance_none_coord(self, db: Database) -> None:
        """坐标为 None 时返回 None。"""
        repo = GeoRepository(db.connection)
        c1 = Coordinate(longitude=120.0, latitude=30.0)
        assert repo.calculate_distance(None, c1) is None
        assert repo.calculate_distance(c1, None) is None
        assert repo.calculate_distance(None, None) is None


# ==================== 空值处理 ====================
class TestEmptyAddress:
    def test_empty_address_returns_none(self, db: Database) -> None:
        """空地址返回 None。"""
        mock_geocoder = MockGeocoder()
        repo = GeoRepository(db.connection, geocoder=mock_geocoder)

        assert repo.resolve_coordinate("") is None
        assert repo.resolve_coordinate("   ") is None
        assert repo.resolve_coordinate(None) is None
        assert mock_geocoder.call_count == 0

    def test_get_cached_coordinate_empty(self, db: Database) -> None:
        """空地址查询缓存返回 None。"""
        repo = GeoRepository(db.connection)
        assert repo.get_cached_coordinate("") is None
        assert repo.get_cached_coordinate("   ") is None


# ==================== GeoCacheRecord 数据类 ====================
class TestGeoCacheRecord:
    def test_record_fields(self) -> None:
        """GeoCacheRecord 字段完整性。"""
        now = datetime.now()
        record = GeoCacheRecord(
            address="测试地址",
            normalized_address="标准化地址",
            longitude=120.0,
            latitude=30.0,
            provider=DEFAULT_PROVIDER,
            status=STATUS_SUCCESS,
            created_at=now,
            updated_at=now,
        )
        assert record.address == "测试地址"
        assert record.normalized_address == "标准化地址"
        assert record.longitude == 120.0
        assert record.latitude == 30.0
        assert record.provider == DEFAULT_PROVIDER
        assert record.status == STATUS_SUCCESS

    def test_record_frozen(self) -> None:
        """GeoCacheRecord 是不可变的。"""
        record = GeoCacheRecord(
            address="测试",
            normalized_address=None,
            longitude=None,
            latitude=None,
            provider="amap",
            status=STATUS_FAILED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        with pytest.raises(AttributeError):
            record.address = "修改"  # type: ignore[misc]


# ==================== V4 迁移验证 ====================
class TestV4Migration:
    def test_geo_cache_table_exists(self, db: Database) -> None:
        """V4 迁移后 geo_cache 表存在。"""
        tables = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "geo_cache" in tables

    def test_geo_cache_indices_exist(self, db: Database) -> None:
        """geo_cache 表索引存在。"""
        indices = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_geo_%'"
            ).fetchall()
        }
        assert "idx_geo_cache_normalized" in indices
        assert "idx_geo_cache_status" in indices

    def test_job_list_has_geo_columns(self, db: Database) -> None:
        """job_list 表包含 P5 地理字段。"""
        cols = {
            row["name"] for row in db.connection.execute("PRAGMA table_info(job_list)").fetchall()
        }
        assert "normalized_address" in cols
        assert "longitude" in cols
        assert "latitude" in cols
        assert "distance_meter" in cols
        assert "within_3km" in cols

    def test_job_detail_has_geo_columns(self, db: Database) -> None:
        """job_detail 表包含 P5 地理字段。"""
        cols = {
            row["name"] for row in db.connection.execute("PRAGMA table_info(job_detail)").fetchall()
        }
        assert "normalized_address" in cols
        assert "longitude" in cols
        assert "latitude" in cols
        assert "distance_meter" in cols
        assert "within_3km" in cols

    def test_geo_cache_status_check_constraint(self, db: Database) -> None:
        """status 字段 CHECK 约束：只允许 success/failed。"""
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO geo_cache (address, status, created_at, updated_at) "
                "VALUES ('test', 'invalid', '2026-01-01', '2026-01-01')"
            )

    def test_schema_version_is_4(self, db: Database) -> None:
        """迁移后 schema_version == 4。"""
        from boss_tool.storage.database import CURRENT_SCHEMA_VERSION

        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
