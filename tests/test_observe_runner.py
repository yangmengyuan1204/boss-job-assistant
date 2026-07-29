"""observe_runner 命令循环测试。

P2：直接测试 _run_observe_loop，注入 FakeCommandSource 与 mock BrowserManager。
不启动真实 Playwright，不访问网络。

覆盖：
- quit / confirm / status / inspect / save-fixture 命令分支
- save-fixture 必须精确输入 SAVE
- 登录页 / 验证页 / 未知页禁止保存 fixture
- 空输入与未知命令
- 浏览器关闭与会话终止退出
- save-fixture 异常分支（ValueError / OSError）
- KeyboardInterrupt 安全退出
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from boss_tool.browser import FakeCommandSource
from boss_tool.browser.signals import BrowserSessionState
from boss_tool.cli import app
from boss_tool.enums import StopReason
from boss_tool.models.observed_page import PageType, PageTypeDetection
from boss_tool.observe_runner import _run_observe_loop, run_observe_page


def _make_cmd_source(commands: list[str]) -> FakeCommandSource:
    """构造 FakeCommandSource。"""
    return FakeCommandSource(commands=commands)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_detection(page_type: PageType, confidence: float = 0.9) -> PageTypeDetection:
    """构造一个 PageTypeDetection。"""
    return PageTypeDetection(
        page_type=page_type,
        confidence=confidence,
        evidence=[f"测试证据-{page_type.value}"],
        warnings=[],
    )


def _make_inspect_summary(
    page_type: PageType = PageType.SEARCH_LIST,
    card_count: int = 2,
) -> dict:
    """构造一个 inspect() 返回的摘要 dict。"""
    return {
        "page_type": page_type.value,
        "confidence": 0.85,
        "evidence": ["URL 命中", "DOM 命中"],
        "warnings": [],
        "card_count": card_count,
        "detail_root_found": 0,
        "field_hits": {"job_name": 2, "company_name": 2},
        "missing_fields": [],
        "structure_changed": False,
        "current_url": "https://www.zhipin.com/web/geek/job",
        "title": "BOSS直聘 - 搜索结果",
    }


def _make_mock_manager(
    *,
    is_running: bool = True,
    page_type: PageType = PageType.SEARCH_LIST,
    page_html: str = "<html><body><div class='search-job-result'></div></body></html>",
    page_url: str = "https://www.zhipin.com/web/geek/job",
) -> MagicMock:
    """构造 mock BrowserManager，含 _page / session / PageObserver 行为。"""
    mock_manager = MagicMock()
    mock_manager.is_running = is_running
    mock_manager.session = MagicMock()
    mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER
    mock_manager.session.is_terminal.return_value = False
    mock_manager.session.session_id = "test-observe-123"
    mock_manager.session.user_confirmed = False

    # _page mock
    mock_page = MagicMock()
    mock_page.url = page_url
    mock_page.content.return_value = page_html
    mock_manager._page = mock_page  # noqa: SLF001

    # close 后变 not running
    def _close(**kwargs):
        mock_manager.is_running = False
        mock_manager.session.state = BrowserSessionState.CLOSED
        mock_manager.session.is_terminal.return_value = True

    mock_manager.close.side_effect = _close

    # patch PageObserver 构造（通过 _get_observer）
    # 因为 _get_observer 内部直接构造 PageObserver(page)，
    # 我们在测试中 patch PageObserver 类
    return mock_manager


def _make_session() -> MagicMock:
    """构造一个 mock session，状态非终止。"""
    session = MagicMock()
    session.state = BrowserSessionState.WAITING_FOR_USER
    session.is_terminal.return_value = False
    session.session_id = "test-observe-123"
    session.user_confirmed = False
    return session


# ==================== 命令分支测试 ====================
class TestObserveLoopCommands:
    """observe_loop 各命令分支测试。"""

    @patch("boss_tool.observe_runner.PageObserver")
    def test_quit_closes_session(self, mock_observer_cls):
        """quit 命令触发 close 并退出。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        mock_manager.close.assert_called_once()
        assert mock_manager.close.call_args.kwargs["stop_reason"] == StopReason.USER_ABORTED

    @patch("boss_tool.observe_runner.PageObserver")
    def test_confirm_records_user_confirmation(self, mock_observer_cls):
        """confirm 命令调用 manager.confirm_user。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["confirm", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        mock_manager.confirm_user.assert_called_once()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_confirm_does_not_claim_login_verified(self, mock_observer_cls, capsys):
        """confirm 不声称登录已验证。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["confirm", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "login_verified" not in captured.out
        assert "已验证登录" not in captured.out
        assert "不代表程序判断登录成功" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_status_outputs_session_and_page_info(self, mock_observer_cls, capsys):
        """status 输出 session 和页面信息。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_instance.get_current_url.return_value = "https://www.zhipin.com/web/geek/job"
        mock_observer_instance.get_current_title.return_value = "BOSS直聘"
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["status", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "test-observe-123" in captured.out
        assert "waiting_for_user" in captured.out
        assert "search_list" in captured.out
        assert "允许保存fixture" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_inspect_outputs_diagnostics(self, mock_observer_cls, capsys):
        """inspect 输出诊断摘要。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_instance.inspect.return_value = _make_inspect_summary(
            PageType.SEARCH_LIST, card_count=5
        )
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["inspect", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "search_list" in captured.out
        assert "5" in captured.out  # card_count
        assert "只读页面侦察" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_empty_input_does_not_confirm(self, mock_observer_cls, capsys):
        """空输入提示无效，不触发 confirm。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        mock_manager.confirm_user.assert_not_called()
        captured = capsys.readouterr()
        assert "空输入无效" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_unknown_command_warns(self, mock_observer_cls, capsys):
        """未知命令提示，不退出。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["foobar", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        mock_manager.close.assert_called_once()
        captured = capsys.readouterr()
        assert "未知命令" in captured.out
        assert "foobar" in captured.out


# ==================== save-fixture 测试 ====================
class TestSaveFixture:
    """save-fixture 命令测试。"""

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_requires_exact_SAVE(self, mock_observer_cls, capsys, tmp_path):
        """非 SAVE 输入取消保存。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        # save-fixture 后会 typer.prompt 读取下一行
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        with patch("boss_tool.observe_runner.typer.prompt", return_value="yes"):
            _run_observe_loop(
                mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
            )
        captured = capsys.readouterr()
        assert "输入不是 SAVE" in captured.out
        # 文件不应被创建
        assert not (tmp_path / "t.html").exists()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_saves_on_exact_SAVE(self, mock_observer_cls, capsys, tmp_path):
        """精确输入 SAVE 触发保存。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_cls.return_value = mock_observer_instance

        html = (
            "<html><body><div class='search-job-result'>"
            "<div class='job-card-wrapper'><a class='job-card-left' href='/job_detail/123.html'>"
            "<span class='job-name'>测试岗位</span></a></div>"
            "</div></body></html>"
        )
        mock_manager = _make_mock_manager(page_html=html)
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        with patch("boss_tool.observe_runner.typer.prompt", return_value="SAVE"):
            _run_observe_loop(
                mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
            )
        captured = capsys.readouterr()
        assert "fixture 已保存" in captured.out
        assert (tmp_path / "t.html").exists()
        assert (tmp_path / "t.meta.json").exists()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_rejected_on_login_page(self, mock_observer_cls, capsys, tmp_path):
        """登录页禁止保存 fixture。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.LOGIN)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
        )
        captured = capsys.readouterr()
        assert "禁止保存" in captured.out
        assert not (tmp_path / "t.html").exists()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_rejected_on_verification_page(self, mock_observer_cls, capsys, tmp_path):
        """验证页禁止保存 fixture。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.VERIFICATION)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
        )
        captured = capsys.readouterr()
        assert "禁止保存" in captured.out
        assert not (tmp_path / "t.html").exists()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_rejected_on_unknown_page(self, mock_observer_cls, capsys, tmp_path):
        """未知页（低置信度）禁止保存 fixture。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(
            PageType.UNKNOWN, confidence=0.3
        )
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
        )
        captured = capsys.readouterr()
        assert "禁止保存" in captured.out
        assert not (tmp_path / "t.html").exists()

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_handles_value_error(self, mock_observer_cls, capsys, tmp_path):
        """脱敏二次扫描发现高风险内容（ValueError）应被捕获并提示。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager(page_html="<html></html>")
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        with (
            patch("boss_tool.observe_runner.typer.prompt", return_value="SAVE"),
            patch(
                "boss_tool.observe_runner.save_fixture",
                side_effect=ValueError("二次扫描发现高风险内容"),
            ),
        ):
            _run_observe_loop(
                mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
            )
        captured = capsys.readouterr()
        assert "保存失败" in captured.err or "保存失败" in captured.out
        assert (
            "脱敏二次扫描" in captured.err
            or "脱敏二次扫描" in captured.out
            or "高风险内容" in captured.err
            or "高风险内容" in captured.out
        )

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_handles_oserror(self, mock_observer_cls, capsys, tmp_path):
        """文件写入失败（OSError）应被捕获并提示。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager(page_html="<html></html>")
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        with (
            patch("boss_tool.observe_runner.typer.prompt", return_value="SAVE"),
            patch(
                "boss_tool.observe_runner.save_fixture",
                side_effect=OSError("磁盘已满"),
            ),
        ):
            _run_observe_loop(
                mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
            )
        captured = capsys.readouterr()
        assert (
            "文件写入失败" in captured.err
            or "文件写入失败" in captured.out
            or "磁盘已满" in captured.err
            or "磁盘已满" in captured.out
        )

    @patch("boss_tool.observe_runner.PageObserver")
    def test_save_fixture_cancel_on_keyboard_interrupt(self, mock_observer_cls, capsys, tmp_path):
        """typer.prompt 被 KeyboardInterrupt 取消时，安全返回命令循环。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = _make_detection(PageType.SEARCH_LIST)
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["save-fixture", "quit"])
        with patch("boss_tool.observe_runner.typer.prompt", side_effect=KeyboardInterrupt()):
            _run_observe_loop(
                mock_manager, session, command_source=cmd_source, output_dir=tmp_path, label="t"
            )
        captured = capsys.readouterr()
        assert "已取消保存" in captured.out
        assert not (tmp_path / "t.html").exists()


# ==================== 退出条件测试 ====================
class TestObserveLoopExitConditions:
    """退出条件测试。"""

    @patch("boss_tool.observe_runner.PageObserver")
    def test_browser_closed_exits_loop(self, mock_observer_cls, capsys):
        """浏览器关闭时无输入也退出。"""
        mock_manager = _make_mock_manager()
        mock_manager.is_running = False  # 启动即已关闭
        session = _make_session()
        cmd_source = _make_cmd_source([])  # 无命令
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "浏览器已关闭" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_session_terminal_exits_loop(self, mock_observer_cls, capsys):
        """会话终止时退出。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        session.state = BrowserSessionState.CLOSED  # 终态
        cmd_source = _make_cmd_source([])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "会话已结束" in captured.out

    @patch("boss_tool.observe_runner.PageObserver")
    def test_no_page_shows_error_and_continues(self, mock_observer_cls, capsys):
        """manager._page 为 None 时提示错误，循环继续。"""
        mock_manager = _make_mock_manager()
        mock_manager._page = None  # noqa: SLF001
        session = _make_session()
        cmd_source = _make_cmd_source(["status", "quit"])
        _run_observe_loop(
            mock_manager, session, command_source=cmd_source, output_dir=Path("."), label="t"
        )
        captured = capsys.readouterr()
        assert "没有活动页面" in captured.out


# ==================== run_observe_page 入口测试 ====================
class TestRunObservePageEntry:
    """run_observe_page 入口函数测试。

    使用 mock cfg 对象绕过 Pydantic 强制校验，
    以测试 run_observe_page 自身的防御性校验逻辑。
    """

    @staticmethod
    def _make_mock_cfg(
        *,
        require_user_confirm: bool = True,
        allow_unattended: bool = False,
        allow_background: bool = False,
        home_url: str = "https://www.zhipin.com/",
    ) -> MagicMock:
        """构造 mock RuntimeConfig，不经过 Pydantic 校验。"""
        cfg = MagicMock()
        cfg.run_control.require_user_confirm = require_user_confirm
        cfg.run_control.allow_unattended = allow_unattended
        cfg.run_control.allow_background = allow_background
        cfg.browser.home_url = home_url
        cfg.browser.user_data_dir = "/tmp/test_user_data"
        return cfg

    def test_rejects_require_user_confirm_false(self, runner: CliRunner, tmp_workspace: Path):
        """require_user_confirm=false 时拒绝启动。"""
        cfg = self._make_mock_cfg(require_user_confirm=False)
        with pytest.raises((SystemExit, typer.Exit)):
            run_observe_page(
                cfg,
                home_url=None,
                output_dir=tmp_workspace / "fixtures",
                label="t",
                project_root=tmp_workspace,
                command_source=_make_cmd_source([]),
            )

    def test_rejects_allow_unattended_true(self, runner: CliRunner, tmp_workspace: Path):
        """allow_unattended=true 时拒绝启动。"""
        cfg = self._make_mock_cfg(allow_unattended=True)
        with pytest.raises((SystemExit, typer.Exit)):
            run_observe_page(
                cfg,
                home_url=None,
                output_dir=tmp_workspace / "fixtures",
                label="t",
                project_root=tmp_workspace,
                command_source=_make_cmd_source([]),
            )

    def test_rejects_allow_background_true(self, runner: CliRunner, tmp_workspace: Path):
        """allow_background=true 时拒绝启动。"""
        cfg = self._make_mock_cfg(allow_background=True)
        with pytest.raises((SystemExit, typer.Exit)):
            run_observe_page(
                cfg,
                home_url=None,
                output_dir=tmp_workspace / "fixtures",
                label="t",
                project_root=tmp_workspace,
                command_source=_make_cmd_source([]),
            )

    def test_rejects_invalid_home_url(self, runner: CliRunner, tmp_workspace: Path):
        """非法 home_url 拒绝启动。"""
        cfg = self._make_mock_cfg()
        with pytest.raises((SystemExit, typer.Exit)):
            run_observe_page(
                cfg,
                home_url="https://evil.example.com/",  # 非白名单
                output_dir=tmp_workspace / "fixtures",
                label="t",
                project_root=tmp_workspace,
                command_source=_make_cmd_source([]),
            )


# ==================== CLI 命令测试 ====================
class TestObservePageCli:
    """observe-page CLI 命令测试。"""

    def test_observe_page_help(self, runner: CliRunner):
        result = runner.invoke(app, ["observe-page", "--help"])
        assert result.exit_code == 0
        assert "observe-page" in result.output or "侦察" in result.output

    def test_observe_page_registered(self, runner: CliRunner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "observe-page" in result.output
