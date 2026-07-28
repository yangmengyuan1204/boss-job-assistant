"""browser-login CLI 命令测试。

所有测试通过 mock BrowserManager 避免真实 Playwright 启动。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from boss_tool.browser.signals import BrowserSessionState
from boss_tool.cli import app
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


# ==================== 配置校验 ====================
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


# ==================== 命令循环 ====================
class TestCommandLoop:
    """测试 confirm/quit/status 命令。

    使用 mock BrowserManager 避免真实启动。
    """

    def _make_mock_manager(self, *, is_running: bool = True) -> MagicMock:
        """构造一个 mock BrowserManager，close() 后 is_running 变为 False。

        这样能正确模拟幂等 close 行为，避免 finally 块重复调用。
        """
        mock_manager = MagicMock()
        # 使用 list 包装以支持闭包修改
        state = {"running": is_running}

        # is_running 作为 property 动态返回
        type(mock_manager).is_running = property(lambda self: state["running"])

        def _close(**kwargs):
            state["running"] = False

        mock_manager.close.side_effect = _close
        mock_manager.user_data_dir = "/tmp/ud"
        return mock_manager

    def _patch_manager(self, mock_manager: MagicMock):
        """patch BrowserManager 构造函数返回 mock。"""
        return patch("boss_tool.cli.BrowserManager", return_value=mock_manager)

    def test_quit_exits_safely(self, runner: CliRunner, copied_config_dir: Path):
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="quit\n",
            )
        assert result.exit_code == 0
        mock_manager.close.assert_called_once()
        # stop_reason 应为 USER_ABORTED
        assert mock_manager.close.call_args.kwargs["stop_reason"] == StopReason.USER_ABORTED

    def test_confirm_sets_user_confirmed(self, runner: CliRunner, copied_config_dir: Path):
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="confirm\nquit\n",
            )
        assert result.exit_code == 0
        # confirm_user 被调用
        mock_manager.confirm_user.assert_called_once()
        assert "user_confirmed" in result.output or "已记录用户确认" in result.output

    def test_status_shows_session_info(self, runner: CliRunner, copied_config_dir: Path):
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER
        mock_manager.session.session_id = "test-session-123"
        mock_manager.session.user_confirmed = False
        mock_manager.session.browser_closed_by_user = False
        mock_manager.session.started_at = "2026-07-28 10:00:00"
        mock_manager.session.ended_at = None
        mock_manager.session.stop_reason = None
        mock_manager.session.last_known_url = "https://www.zhipin.com/"

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="status\nquit\n",
            )
        assert result.exit_code == 0
        assert "test-session-123" in result.output
        assert "waiting_for_user" in result.output

    def test_empty_input_does_not_confirm(self, runner: CliRunner, copied_config_dir: Path):
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="\nquit\n",
            )
        assert result.exit_code == 0
        # confirm_user 不应被调用
        mock_manager.confirm_user.assert_not_called()
        assert "空输入无效" in result.output

    def test_invalid_command_does_not_exit(self, runner: CliRunner, copied_config_dir: Path):
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="invalid_command\nquit\n",
            )
        assert result.exit_code == 0
        assert "未知命令" in result.output
        mock_manager.close.assert_called_once()

    def test_confirm_does_not_claim_login_verified(
        self, runner: CliRunner, copied_config_dir: Path
    ):
        """confirm 不应声称已验证登录。"""
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="confirm\nquit\n",
            )
        assert "login_verified" not in result.output
        assert "已验证登录" not in result.output
        assert "不代表程序判断登录成功" in result.output

    def test_browser_closed_by_user_detected(self, runner: CliRunner, copied_config_dir: Path):
        """浏览器被用户关闭后会话结束。"""
        mock_manager = self._make_mock_manager(is_running=False)
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.CLOSED

        with self._patch_manager(mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="",
            )
        assert result.exit_code == 0
        assert "浏览器已关闭" in result.output

    def test_ctrl_c_safe_exit(self, runner: CliRunner, copied_config_dir: Path):
        """Ctrl+C 被转换为明确 stop_reason。"""
        mock_manager = self._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        # 模拟 typer.prompt 抛 KeyboardInterrupt
        with (
            patch("boss_tool.cli.typer.prompt", side_effect=KeyboardInterrupt),
            self._patch_manager(mock_manager),
        ):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="",
            )
        assert result.exit_code == 0
        assert "Ctrl+C" in result.output or "安全退出" in result.output
        mock_manager.close.assert_called()


# ==================== 安全声明 ====================
class TestSafetyDeclarations:
    def test_output_contains_safety_notice(self, runner: CliRunner, copied_config_dir: Path):
        from tests.test_browser_cli import TestCommandLoop

        helper = TestCommandLoop()
        mock_manager = helper._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER

        with patch("boss_tool.cli.BrowserManager", return_value=mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="quit\n",
            )
        assert "不得用于规避平台检测" in result.output

    def test_no_cookie_in_output(self, runner: CliRunner, copied_config_dir: Path):
        from tests.test_browser_cli import TestCommandLoop

        helper = TestCommandLoop()
        mock_manager = helper._make_mock_manager()
        mock_manager.session = MagicMock()
        mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER
        mock_manager.session.session_id = "test-123"
        mock_manager.session.user_confirmed = False
        mock_manager.session.browser_closed_by_user = False
        mock_manager.session.started_at = None
        mock_manager.session.ended_at = None
        mock_manager.session.stop_reason = None
        mock_manager.session.last_known_url = "https://www.zhipin.com/"

        with patch("boss_tool.cli.BrowserManager", return_value=mock_manager):
            result = runner.invoke(
                app,
                ["browser-login", "--config-dir", str(copied_config_dir)],
                input="status\nquit\n",
            )
        # 输出中不应出现 Cookie 关键字（除安全声明外）
        lines = result.output.split("\n")
        for line in lines:
            if "Cookie" in line or "cookie" in line:
                # 仅允许出现在安全声明中
                assert "不" in line or "禁止" in line or "不得" in line
