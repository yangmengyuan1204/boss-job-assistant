"""P2 parse-fixture CLI 命令测试。

测试 parse-fixture 命令的 JSON 输出、诊断信息、强制页面类型、
文件不存在处理、无效页面类型处理。
所有测试不启动浏览器、不访问网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boss_tool.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestParseFixtureCommand:
    """测试 parse-fixture CLI 命令。"""

    def test_parse_list_basic_json(
        self,
        runner: CliRunner,
        fixtures_dir: Path,
    ) -> None:
        """parse-fixture list_page_basic.html --json，exit_code==0，输出含 JSON。"""
        fixture_path = fixtures_dir / "list_page_basic.html"
        result = runner.invoke(app, ["parse-fixture", str(fixture_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert "page_type" in data

    def test_parse_detail_basic(
        self,
        runner: CliRunner,
        fixtures_dir: Path,
    ) -> None:
        """parse-fixture detail_page_basic.html --diagnostics，exit_code==0。"""
        fixture_path = fixtures_dir / "detail_page_basic.html"
        result = runner.invoke(app, ["parse-fixture", str(fixture_path), "--diagnostics"])
        assert result.exit_code == 0

    def test_parse_nonexistent_file(
        self,
        runner: CliRunner,
        tmp_workspace: Path,
    ) -> None:
        """parse-fixture nonexistent.html，exit_code != 0。"""
        nonexistent = tmp_workspace / "nonexistent.html"
        result = runner.invoke(app, ["parse-fixture", str(nonexistent)])
        assert result.exit_code != 0

    def test_parse_force_page_type(
        self,
        runner: CliRunner,
        fixtures_dir: Path,
    ) -> None:
        """parse-fixture list_page_basic.html --page-type search_list，exit_code==0。"""
        fixture_path = fixtures_dir / "list_page_basic.html"
        result = runner.invoke(
            app,
            ["parse-fixture", str(fixture_path), "--page-type", "search_list"],
        )
        assert result.exit_code == 0

    def test_parse_invalid_page_type(
        self,
        runner: CliRunner,
        fixtures_dir: Path,
    ) -> None:
        """parse-fixture list_page_basic.html --page-type invalid_type，exit_code != 0。"""
        fixture_path = fixtures_dir / "list_page_basic.html"
        result = runner.invoke(
            app,
            ["parse-fixture", str(fixture_path), "--page-type", "invalid_type"],
        )
        assert result.exit_code != 0
