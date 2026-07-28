"""迁移入口（占位）。

迁移注册已移到 boss_tool.storage.database.MIGRATIONS 字典。
本模块保留为入口兼容，方便外部按版本号查询。

未来 v2+ 迁移应：
1. 在 database.py 中新增 migration_vN_xxx(conn) 函数
2. 在 database.MIGRATIONS 字典中追加 {N: migration_vN_xxx}
3. 更新 database.CURRENT_SCHEMA_VERSION 为最高版本号
"""

from __future__ import annotations

from boss_tool.storage.database import CURRENT_SCHEMA_VERSION, MIGRATIONS


def list_migrations() -> list[int]:
    """返回所有已注册的迁移版本号（升序）。"""
    return sorted(MIGRATIONS.keys())


def latest_version() -> int:
    """返回最新迁移版本号。"""
    return CURRENT_SCHEMA_VERSION


__all__ = ["list_migrations", "latest_version"]
