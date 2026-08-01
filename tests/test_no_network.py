"""P2 网络隔离测试。

验证 parsers 模块和 parse-fixture 命令不依赖网络：
- parsers 模块不导入 playwright
- parse-fixture 命令不实例化 BrowserManager
- parse_list_page 不创建 socket
- parse_list_page 不调用 requests
"""

from __future__ import annotations

import ast
import inspect
import socket
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boss_tool.cli import app
from boss_tool.parsers.list_page import parse_list_page


class TestNoNetwork:
    """验证解析器与 CLI 不访问网络。"""

    def test_parser_module_not_import_playwright(self) -> None:
        """parsers 模块不导入 playwright。"""
        import boss_tool.parsers
        import boss_tool.parsers.detail_page
        import boss_tool.parsers.diagnostics
        import boss_tool.parsers.list_page
        import boss_tool.parsers.page_types
        import boss_tool.parsers.sanitization
        import boss_tool.parsers.selectors

        modules = [
            boss_tool.parsers,
            boss_tool.parsers.detail_page,
            boss_tool.parsers.diagnostics,
            boss_tool.parsers.list_page,
            boss_tool.parsers.page_types,
            boss_tool.parsers.sanitization,
            boss_tool.parsers.selectors,
        ]

        for mod in modules:
            source = inspect.getsource(mod)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        msg = f"{mod.__name__} 直接导入了 playwright"
                        assert "playwright" not in alias.name.lower(), msg
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and "playwright" in node.module.lower()
                ):
                    raise AssertionError(f"{mod.__name__} 从 playwright 导入")

    def test_parse_fixture_not_start_browser(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fixtures_dir: Path,
    ) -> None:
        """parse-fixture 命令不实例化 BrowserManager。"""
        from boss_tool.browser.manager import BrowserManager

        def _fail_init(self, *args, **kwargs):
            raise AssertionError("BrowserManager 不应被实例化")

        monkeypatch.setattr(BrowserManager, "__init__", _fail_init)

        runner = CliRunner()
        fixture_path = fixtures_dir / "list_page_basic.html"
        result = runner.invoke(app, ["parse-fixture", str(fixture_path), "--json"])
        assert result.exit_code == 0

    def test_socket_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        list_page_basic_html: str,
    ) -> None:
        """parse_list_page 不创建 socket。"""

        def _fail_socket(*args, **kwargs):
            raise AssertionError("socket.socket 不应被调用")

        monkeypatch.setattr(socket, "socket", _fail_socket)

        cards = parse_list_page(list_page_basic_html)
        assert len(cards) == 2

    def test_requests_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        list_page_basic_html: str,
    ) -> None:
        """parse_list_page 不调用 requests.get。"""

        class _FakeRequests:
            @staticmethod
            def get(*args, **kwargs):
                raise AssertionError("requests.get 不应被调用")

            @staticmethod
            def post(*args, **kwargs):
                raise AssertionError("requests.post 不应被调用")

        monkeypatch.setitem(sys.modules, "requests", _FakeRequests)

        cards = parse_list_page(list_page_basic_html)
        assert len(cards) == 2
