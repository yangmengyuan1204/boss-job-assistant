"""迁移入口。

未来 v2+ 的迁移函数应注册到 database._MIGRATIONS 中。
P0 阶段仅占位。
"""

from __future__ import annotations


def list_migrations() -> list[int]:
    """返回所有可用的迁移版本号。"""
    # P0 阶段仅 v1
    return [1]


__all__ = ["list_migrations"]
