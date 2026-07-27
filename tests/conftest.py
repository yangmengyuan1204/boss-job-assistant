"""pytest 全局 fixtures。

确保：
- src/ 在 sys.path 中
- 测试不污染真实 data/logs/output 目录
- 使用临时目录承载 SQLite、日志、配置
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# 把 src/ 加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 标记真实配置目录（用于默认配置测试）
REAL_CONFIG_DIR = PROJECT_ROOT / "config"


@pytest.fixture
def tmp_workspace() -> Iterator[Path]:
    """创建临时工作目录，包含 data/logs/output/user_data 子目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="boss_test_"))
    (tmp / "data").mkdir()
    (tmp / "logs").mkdir()
    (tmp / "output").mkdir()
    (tmp / "user_data").mkdir()
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def tmp_db_path(tmp_workspace: Path) -> Path:
    return tmp_workspace / "data" / "test.db"


@pytest.fixture
def real_config_dir() -> Path:
    """返回项目自带的真实配置目录。"""
    return REAL_CONFIG_DIR


@pytest.fixture
def copied_config_dir(tmp_workspace: Path, real_config_dir: Path) -> Path:
    """将真实配置拷贝到临时目录，允许测试中修改。"""
    dst = tmp_workspace / "config"
    shutil.copytree(real_config_dir, dst)
    return dst


@pytest.fixture(autouse=True)
def _reset_logger():
    """每个测试前后重置 boss_tool logger 的 handlers，避免污染。"""
    import logging

    logger = logging.getLogger("boss_tool")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    logger.setLevel(saved_level)


# 防止任何测试意外写入真实 data/logs 目录
os.environ.setdefault("BOSS_TOOL_TEST_MODE", "1")
