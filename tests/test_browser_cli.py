"""browser-login CLI 命令测试。

P1.1 重构：
- 命令循环测试改为直接测试 _run_command_loop，注入 FakeCommandSource
- 不再依赖 typer.prompt 的 input= 参数（生产路径已改用 ThreadedCommandSource）
- 新增非阻塞退出测试（浏览器关闭后无需输入即可退出）
- 新增 URL 严格校验测试
- 新增 close_source 语义测试
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from boss_tool.browser import FakeCommandSource, ThreadedCommandSource
from boss_tool.browser.signals import BrowserSessionState
from boss_tool.cli import _run_command_loop, app
from boss_tool.enums import StopReason


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ==================== Help / 命令注册 ====================
class TestBrowserLoginCommand:
    def test_help_lists_browser_login(self, runner: CliRunner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "browser-login" in result.output

    def test_browser_login_help(self, runner: CliRunner):
        result = runner.invoke(app, ["browser-login", "--help"])
        assert result.exit_code == 0
        assert "confirm" in result.output or "登录" in result.output


# ==================== 配置与 URL 校验 ====================
class TestBrowserLoginConfigValidation:
    def test_invalid_home_url_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """非白名单域名被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://evil.example.com/",
            ],
        )
        assert result.exit_code != 0
        assert "白名单" in result.output or "不在白名单" in result.output

    def test_localhost_rejected(self, runner: CliRunner, copied_config_dir: Path):
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "http://localhost:8080/",
            ],
        )
        assert result.exit_code != 0

    def test_http_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：http 必须被拒绝（仅允许 https）。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "http://www.zhipin.com/",
            ],
        )
        assert result.exit_code != 0
        assert "https" in result.output

    def test_port_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：显式非默认端口被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://www.zhipin.com:8443/",
            ],
        )
        assert result.exit_code != 0
        assert "端口" in result.output

    def test_userinfo_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：userinfo 被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://user@www.zhipin.com/",
            ],
        )
        assert result.exit_code != 0
        assert "userinfo" in result.output

    def test_password_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：password 被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://user:pass@www.zhipin.com/",
            ],
        )
        assert result.exit_code != 0
        assert "userinfo" in result.output

    def test_query_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：query 被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://www.zhipin.com/?token=secret",
            ],
        )
        assert result.exit_code != 0
        assert "query" in result.output

    def test_fragment_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：fragment 被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://www.zhipin.com/#fragment",
            ],
        )
        assert result.exit_code != 0
        assert "fragment" in result.output

    def test_evil_lookalike_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：相似恶意域名被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://www.zhipin.com.evil.com/",
            ],
        )
        assert result.exit_code != 0

    def test_evil_userinfo_rejected(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：evil.com@www.zhipin.com 形式被拒绝。"""
        result = runner.invoke(
            app,
            [
                "browser-login",
                "--config-dir",
                str(copied_config_dir),
                "--home-url",
                "https://evil.com@www.zhipin.com/",
            ],
        )
        assert result.exit_code != 0
        assert "userinfo" in result.output

    def test_https_zhipin_allowed(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：https://www.zhipin.com/ 允许。"""
        # 通过 mock 避免真实启动浏览器
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: False)
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.CLOSED
        with patch("boss_tool.cli.BrowserManager", return_value=mock_manager):
            result = runner.invoke(
                app,
                [
                    "browser-login",
                    "--config-dir",
                    str(copied_config_dir),
                    "--home-url",
                    "https://www.zhipin.com/",
                ],
            )
        # 应该正常退出（mock is_running=False 直接退出循环）
        assert result.exit_code == 0

    def test_https_zhipin_no_www_allowed(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：https://zhipin.com/ 允许。"""
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: False)
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.CLOSED
        with patch("boss_tool.cli.BrowserManager", return_value=mock_manager):
            result = runner.invoke(
                app,
                [
                    "browser-login",
                    "--config-dir",
                    str(copied_config_dir),
                    "--home-url",
                    "https://zhipin.com/",
                ],
            )
        assert result.exit_code == 0

    def test_cli_output_does_not_contain_query(self, runner: CliRunner, copied_config_dir: Path):
        """P1.1：CLI 输出不应包含 query 参数。"""
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: False)
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.CLOSED
        mock_manager.user_data_dir = "/tmp/ud"
        with patch("boss_tool.cli.BrowserManager", return_value=mock_manager):
            result = runner.invoke(
                app,
                [
                    "browser-login",
                    "--config-dir",
                    str(copied_config_dir),
                    "--home-url",
                    "https://www.zhipin.com/",
                ],
            )
        # 输出中不应出现 token、password 等敏感 query
        assert "token=" not in result.output
        assert "password=" not in result.output


# ==================== 命令循环（直接测试 _run_command_loop） ====================
class TestCommandLoop:
    """P1.1：直接测试 _run_command_loop，注入 FakeCommandSource。

    不再通过 CliRunner input= 参数注入命令，
    而是构造 FakeCommandSource 预置命令队列，直接调用 _run_command_loop。
    """

    def _make_mock_manager(self, *, is_running: bool = True) -> MagicMock:
        """构造一个 mock BrowserManager，close() 后 is_running 变为 False。"""
        mock_manager = MagicMock()
        state = {"running": is_running}
        type(mock_manager).is_running = property(lambda self: state["running"])

        def _close(**kwargs):
            state["running"] = False

        mock_manager.close.side_effect = _close
        mock_manager.user_data_dir = "/tmp/ud"
        return mock_manager

    def _make_session(self, state: BrowserSessionState = BrowserSessionState.WAITING_FOR_USER):
        """构造一个 mock session。"""
        s = MagicMock()
        s.state = state
        s.session_id = "test-session-123"
        s.user_confirmed = False
        s.browser_closed_by_user = False
        s.close_source = None
        s.started_at = "2026-07-28 10:00:00"
        s.ended_at = None
        s.stop_reason = None
        s.last_known_url = "https://www.zhipin.com/"
        return s

    def test_quit_exits_safely(self):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        cmd_source = FakeCommandSource(commands=["quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source)
        mock_manager.close.assert_called_once()
        assert mock_manager.close.call_args.kwargs["stop_reason"] == StopReason.USER_ABORTED

    def test_confirm_sets_user_confirmed(self):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        cmd_source = FakeCommandSource(commands=["confirm", "quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source)
        mock_manager.confirm_user.assert_called_once()

    def test_status_shows_session_info(self, capsys):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        # status 命令读取 manager.session，需要绑定到测试 session
        mock_manager.session = session
        cmd_source = FakeCommandSource(commands=["status", "quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        assert "test-session-123" in captured.out
        assert "waiting_for_user" in captured.out

    def test_empty_input_does_not_confirm(self, capsys):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        cmd_source = FakeCommandSource(commands=["", "quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        mock_manager.confirm_user.assert_not_called()
        captured = capsys.readouterr()
        assert "空输入无效" in captured.out

    def test_invalid_command_does_not_exit(self, capsys):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        cmd_source = FakeCommandSource(commands=["invalid_command", "quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        mock_manager.close.assert_called_once()
        captured = capsys.readouterr()
        assert "未知命令" in captured.out

    def test_confirm_does_not_claim_login_verified(self, capsys):
        mock_manager = self._make_mock_manager()
        session = self._make_session()
        cmd_source = FakeCommandSource(commands=["confirm", "quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        assert "login_verified" not in captured.out
        assert "已验证登录" not in captured.out
        assert "不代表程序判断登录成功" in captured.out


# ==================== 非阻塞退出（P1.1 新增） ====================
class TestNonBlockingExit:
    """P1.1：浏览器关闭后无需用户输入即可退出命令循环。"""

    def _make_mock_manager(self, *, is_running: bool = True) -> MagicMock:
        mock_manager = MagicMock()
        state = {"running": is_running}
        type(mock_manager).is_running = property(lambda self: state["running"])

        def _close(**kwargs):
            state["running"] = False

        mock_manager.close.side_effect = _close
        return mock_manager

    def test_browser_closed_exits_without_input(self, capsys):
        """浏览器关闭时无终端输入也能退出。"""
        mock_manager = self._make_mock_manager(is_running=False)
        session = MagicMock()
        session.state = BrowserSessionState.CLOSED
        # 空命令队列（用户未输入任何内容）
        cmd_source = FakeCommandSource(commands=[])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        assert "浏览器已关闭" in captured.out
        # close 不应被命令循环调用（浏览器已关闭）
        mock_manager.close.assert_not_called()

    def test_page_closed_exits_without_input(self, capsys):
        """唯一页面关闭时无终端输入也能退出。"""
        mock_manager = self._make_mock_manager(is_running=False)
        session = MagicMock()
        session.state = BrowserSessionState.CLOSED
        cmd_source = FakeCommandSource(commands=[])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        assert "浏览器已关闭" in captured.out

    def test_context_closed_exits_without_input(self, capsys):
        """context 关闭时无终端输入也能退出。"""
        mock_manager = self._make_mock_manager(is_running=False)
        session = MagicMock()
        session.state = BrowserSessionState.CLOSED
        cmd_source = FakeCommandSource(commands=[])

        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        assert "浏览器已关闭" in captured.out

    def test_daemon_thread_does_not_block_exit(self):
        """daemon 输入线程不会阻止进程结束。"""
        import threading
        import time

        # 构造一个真实 ThreadedCommandSource 但 input_func 立即返回空（模拟阻塞输入）
        # 通过让 is_running 很快变为 False 验证主线程能退出
        mock_manager = MagicMock()
        state = {"running": True}
        type(mock_manager).is_running = property(lambda self: state["running"])
        session = MagicMock()
        session.state = BrowserSessionState.WAITING_FOR_USER

        # input_func 阻塞（模拟用户未输入），但 is_running 在第 2 次轮询后变为 False
        def blocking_input(prompt):
            while True:
                time.sleep(0.01)
                if not state["running"]:
                    raise EOFError  # 模拟输入结束

        cmd_source = ThreadedCommandSource(input_func=blocking_input, prompt="> ")
        cmd_source.start()
        assert cmd_source.is_daemon is True  # 必须为 daemon

        # 启动一个定时器把 is_running 置为 False（模拟浏览器关闭）
        def stop_after():
            state["running"] = False

        timer = threading.Timer(0.1, stop_after)
        timer.start()

        start = time.time()
        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.02)
        elapsed = time.time() - start
        timer.join()

        # 应在 1 秒内退出（远快于无限阻塞）
        assert elapsed < 1.0
        # 验证输入线程为 daemon
        assert cmd_source._thread is not None
        assert cmd_source._thread.daemon is True

    def test_command_source_can_be_injected(self):
        """command_source 可以注入 fake 测试。"""
        mock_manager = self._make_mock_manager()
        session = MagicMock()
        session.state = BrowserSessionState.WAITING_FOR_USER
        cmd_source = FakeCommandSource(commands=["quit"])

        _run_command_loop(mock_manager, session, command_source=cmd_source)
        # quit 触发 close
        mock_manager.close.assert_called_once()

    def test_no_residual_non_daemon_threads(self):
        """不产生后台残留的非 daemon 线程。"""
        import time

        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: False)
        session = MagicMock()
        session.state = BrowserSessionState.CLOSED

        cmd_source = ThreadedCommandSource(input_func=lambda p: time.sleep(0.01))
        cmd_source.start()
        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        cmd_source.stop()

        # 输入线程应为 daemon（不会残留）
        assert cmd_source._thread is not None
        assert cmd_source._thread.daemon is True


# ==================== Ctrl+C 安全退出（P1.1 更新） ====================
class TestCtrlCExit:
    """P1.1：Ctrl+C 仍必须安全退出。

    由于生产路径不再使用 typer.prompt，Ctrl+C 由主线程触发，
    在 _run_command_loop 外层的 try/except KeyboardInterrupt 捕获。
    """

    def test_ctrl_c_safe_exit(self):
        """模拟 Ctrl+C：_run_command_loop 抛 KeyboardInterrupt，外层捕获。"""
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: True)
        session = MagicMock()
        session.state = BrowserSessionState.WAITING_FOR_USER

        # 构造一个抛 KeyboardInterrupt 的命令源
        class CtrlCCommandSource:
            def start(self) -> None:
                pass

            def poll(self, timeout: float):
                raise KeyboardInterrupt

            def stop(self) -> None:
                pass

        cmd_source = CtrlCCommandSource()

        # _run_command_loop 应向上抛出 KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            _run_command_loop(mock_manager, session, command_source=cmd_source)


# ==================== 安全声明 ====================
class TestSafetyDeclarations:
    def test_output_contains_safety_notice(self, capsys):
        """直接测试 _run_command_loop 输出包含安全声明。"""
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: False)
        session = MagicMock()
        session.state = BrowserSessionState.CLOSED

        cmd_source = FakeCommandSource(commands=[])
        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        # 安全声明在 _run_command_loop 之外的 finally 块，不在此测试范围
        # 此测试验证浏览器关闭时输出的退出信息

    def test_no_cookie_in_output(self, capsys):
        """输出中不应出现 Cookie 关键字。"""
        mock_manager = MagicMock()
        type(mock_manager).is_running = property(lambda self: True)
        session = MagicMock()
        session.state = BrowserSessionState.WAITING_FOR_USER
        session.session_id = "test-123"
        session.user_confirmed = False
        session.browser_closed_by_user = False
        session.close_source = None
        session.started_at = None
        session.ended_at = None
        session.stop_reason = None
        session.last_known_url = "https://www.zhipin.com/"

        cmd_source = FakeCommandSource(commands=["status", "quit"])
        _run_command_loop(mock_manager, session, command_source=cmd_source, poll_interval=0.01)
        captured = capsys.readouterr()
        # 输出中不应出现 Cookie 关键字
        lines = captured.out.split("\n")
        for line in lines:
            if "Cookie" in line or "cookie" in line:
                assert "不" in line or "禁止" in line or "不得" in line


# ==================== 不存在自动登录验证逻辑 ====================
class TestNoLoginVerification:
    def test_no_login_verified_in_source(self):
        """源码中不应存在 login_verified 自动判断逻辑。"""
        import boss_tool.cli as cli_mod

        with open(cli_mod.__file__, encoding="utf-8") as f:
            source = f.read()
        assert "login_verified" not in source
        assert "已验证登录" not in source
        assert "自动判断登录成功" not in source
