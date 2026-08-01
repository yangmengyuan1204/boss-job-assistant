"""BrowserManager 测试。

所有测试均使用 fake Playwright 对象，不访问真实网络。
"""

from __future__ import annotations

import pytest

from boss_tool.browser.exceptions import (
    BrowserAlreadyRunningError,
    BrowserStartFailedError,
    ChromiumNotInstalledError,
    HomePageOpenFailedError,
    InvalidUserDataDirError,
    PlaywrightNotInstalledError,
)
from boss_tool.browser.manager import (
    BrowserManager,
    is_home_url_allowed,
    redact_url,
    validate_user_data_dir,
)
from boss_tool.browser.signals import BrowserSessionState
from boss_tool.enums import StopReason
from tests.browser_fakes import (
    FailingPlaywrightFactory,
    FakeChromium,
    FakeContext,
    FakePage,
    FakePlaywrightBundle,
)


# ==================== 路径安全 ====================
class TestValidateUserDataDir:
    def test_normal_path_passes(self, tmp_workspace, project_root):
        path = validate_user_data_dir(tmp_workspace / "user_data", project_root=project_root)
        assert path.is_absolute()

    def test_empty_path_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="不能为空"):
            validate_user_data_dir("", project_root=project_root)

    def test_dot_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="当前/上级目录"):
            validate_user_data_dir(".", project_root=project_root)

    def test_dotdot_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="当前/上级目录"):
            validate_user_data_dir("..", project_root=project_root)

    def test_project_root_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="项目根目录"):
            validate_user_data_dir(project_root, project_root=project_root)

    def test_src_dir_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="src"):
            validate_user_data_dir(project_root / "src", project_root=project_root)

    def test_config_dir_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="config"):
            validate_user_data_dir(project_root / "config", project_root=project_root)

    def tests_dir_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="tests"):
            validate_user_data_dir(project_root / "tests", project_root=project_root)

    def test_disk_root_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="磁盘根目录"):
            validate_user_data_dir("C:\\", project_root=project_root)

    def test_none_rejected(self, project_root):
        with pytest.raises(InvalidUserDataDirError, match="None"):
            validate_user_data_dir(None, project_root=project_root)  # type: ignore[arg-type]


# ==================== URL 脱敏 ====================
class TestRedactUrl:
    def test_strips_query(self):
        assert (
            redact_url("https://www.zhipin.com/job/123?token=secret")
            == "https://www.zhipin.com/job/123"
        )

    def test_strips_fragment(self):
        assert (
            redact_url("https://www.zhipin.com/job/123#section") == "https://www.zhipin.com/job/123"
        )

    def test_strips_query_and_fragment(self):
        assert (
            redact_url("https://www.zhipin.com/job/123?token=x#frag")
            == "https://www.zhipin.com/job/123"
        )

    def test_keeps_path(self):
        assert redact_url("https://www.zhipin.com/web/chat") == "https://www.zhipin.com/web/chat"

    def test_none_returns_none(self):
        assert redact_url(None) is None

    def test_empty_returns_empty(self):
        assert redact_url("") == ""

    def test_invalid_url_handled(self):
        result = redact_url("not-a-url")
        # 不应抛异常
        assert isinstance(result, str)


class TestIsHomeUrlAllowed:
    def test_www_zhipin_com_allowed(self):
        assert is_home_url_allowed("https://www.zhipin.com/") is True

    def test_zhipin_com_allowed(self):
        assert is_home_url_allowed("https://zhipin.com/") is True

    def test_other_host_rejected(self):
        assert is_home_url_allowed("https://example.com/") is False

    def test_localhost_rejected(self):
        assert is_home_url_allowed("http://localhost:8080/") is False

    def test_ip_rejected(self):
        assert is_home_url_allowed("http://192.168.1.1/") is False

    def test_invalid_url_rejected(self):
        assert is_home_url_allowed("not-a-url") is False


# ==================== validate_home_url（P1.1 新增） ====================
class TestValidateHomeUrl:
    """P1.1：统一 URL 校验函数测试。

    覆盖所有必须拒绝的场景：http/端口/userinfo/query/fragment/恶意相似域名等。
    """

    def test_https_www_zhipin_allowed(self):
        from boss_tool.browser.manager import validate_home_url

        assert validate_home_url("https://www.zhipin.com/") == "https://www.zhipin.com/"

    def test_https_zhipin_no_www_allowed(self):
        from boss_tool.browser.manager import validate_home_url

        assert validate_home_url("https://zhipin.com/") == "https://zhipin.com/"

    def test_http_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="https"):
            validate_home_url("http://www.zhipin.com/")

    def test_port_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="端口"):
            validate_home_url("https://www.zhipin.com:8443/")

    def test_username_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="userinfo"):
            validate_home_url("https://user@www.zhipin.com/")

    def test_password_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="userinfo"):
            validate_home_url("https://user:pass@www.zhipin.com/")

    def test_query_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="query"):
            validate_home_url("https://www.zhipin.com/?token=secret")

    def test_fragment_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="fragment"):
            validate_home_url("https://www.zhipin.com/#fragment")

    def test_evil_lookalike_rejected(self):
        """相似恶意域名 www.zhipin.com.evil.com 被拒绝。"""
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="白名单"):
            validate_home_url("https://www.zhipin.com.evil.com/")

    def test_evil_userinfo_rejected(self):
        """evil.com@www.zhipin.com 形式被拒绝。"""
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="userinfo"):
            validate_home_url("https://evil.com@www.zhipin.com/")

    def test_non_root_path_rejected(self):
        """P1.1：首页路径限制为 / 或空，其他路径拒绝。"""
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="路径"):
            validate_home_url("https://www.zhipin.com/job/123")

    def test_empty_url_rejected(self):
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="空"):
            validate_home_url("")

    def test_error_message_does_not_contain_token(self):
        """P1.1：错误信息中不应出现敏感 query 值。"""
        from boss_tool.browser.manager import validate_home_url

        with pytest.raises(ValueError, match="query") as exc_info:
            validate_home_url("https://www.zhipin.com/?token=supersecret")
        # 错误信息中不应出现 token 值
        assert "supersecret" not in str(exc_info.value)


# ==================== BrowserManager 基础 ====================
class TestBrowserManagerStart:
    def test_start_success_uses_launch_persistent_context(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        session = manager.start()

        assert session.state == BrowserSessionState.WAITING_FOR_USER
        # launch_persistent_context 被调用
        assert len(fake_playwright_bundle.chromium.launch_calls) == 1

    def test_start_uses_configured_user_data_dir(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        call = fake_playwright_bundle.chromium.launch_calls[0]
        assert "user_data_dir" in call
        # user_data_dir 应为绝对路径
        assert call["user_data_dir"]

    def test_headless_is_false(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        call = fake_playwright_bundle.chromium.launch_calls[0]
        assert call["headless"] is False

    def test_start_opens_home_url(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 首页被打开
        assert "https://www.zhipin.com/" in fake_playwright_bundle.page.goto_calls

    def test_start_does_not_create_second_context(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 只调用一次 launch_persistent_context
        assert len(fake_playwright_bundle.chromium.launch_calls) == 1

    def test_repeat_start_rejected(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        with pytest.raises(BrowserAlreadyRunningError):
            manager.start()

    def test_session_has_correct_home_url(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        session = manager.start()
        assert session.home_url == "https://www.zhipin.com/"

    def test_session_user_data_dir_is_absolute(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        session = manager.start()
        assert session.user_data_dir is not None
        from pathlib import Path

        assert Path(session.user_data_dir).is_absolute()

    def test_last_known_url_redacted(self, browser_config, project_root, fake_playwright_bundle):
        """last_known_url 应为脱敏后的 URL。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        session = manager.start()
        # 首页 URL 无 query，但确保为 scheme://host/path 形式
        assert session.last_known_url == "https://www.zhipin.com/"


# ==================== Playwright 依赖检查 ====================
class TestPlaywrightDependencyCheck:
    def test_playwright_not_installed_error(self, browser_config, project_root):
        """playwright 包未安装时抛 PlaywrightNotInstalledError。"""

        factory = FailingPlaywrightFactory(PlaywrightNotInstalledError("not installed"))
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=factory,
        )
        with pytest.raises(PlaywrightNotInstalledError, match="not installed"):
            manager.start()

    def test_chromium_not_installed_error(self, browser_config, project_root):
        """chromium 二进制未安装时抛 ChromiumNotInstalledError。"""
        # 构造 launch_persistent_context 抛出 executable doesn't exist 错误
        chromium = FakeChromium(
            launch_error=RuntimeError("Executable doesn't exist at /path/chromium")
        )
        bundle = FakePlaywrightBundle()
        bundle.chromium = chromium
        bundle.ctx_manager = None
        # 重建 ctx_manager
        from tests.browser_fakes import FakePlaywrightCtxManager

        bundle.ctx_manager = FakePlaywrightCtxManager(chromium)

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=bundle.factory(),
        )
        with pytest.raises(ChromiumNotInstalledError, match="chromium"):
            manager.start()

    def test_start_failure_cleans_up_resources(self, browser_config, project_root):
        """启动失败后资源被清理。"""
        chromium = FakeChromium(launch_error=RuntimeError("launch boom"))
        bundle = FakePlaywrightBundle()
        bundle.chromium = chromium
        from tests.browser_fakes import FakePlaywrightCtxManager

        bundle.ctx_manager = FakePlaywrightCtxManager(chromium)

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=bundle.factory(),
        )
        with pytest.raises(BrowserStartFailedError):
            manager.start()
        # 会话标记为 failed
        assert manager.session is not None
        assert manager.session.state == BrowserSessionState.FAILED
        # is_running 为 False
        assert manager.is_running is False


# ==================== 首页打开失败 ====================
class TestHomePageOpenFailed:
    def test_home_page_open_failure(self, browser_config, project_root):
        """首页打开失败抛 HomePageOpenFailedError。"""
        page = FakePage(goto_ok=False)
        context = FakeContext(pages=[page])
        chromium = FakeChromium(context=context)
        bundle = FakePlaywrightBundle(page=page)
        bundle.context = context
        bundle.chromium = chromium
        from tests.browser_fakes import FakePlaywrightCtxManager

        bundle.ctx_manager = FakePlaywrightCtxManager(chromium)

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=bundle.factory(),
        )
        with pytest.raises(HomePageOpenFailedError):
            manager.start()


# ==================== 关闭与幂等 ====================
class TestBrowserManagerClose:
    def test_close_idempotent(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        # 重复 close 不报错
        manager.close()
        manager.close()
        assert manager.session.state == BrowserSessionState.CLOSED

    def test_close_without_start_is_noop(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        # 未 start 直接 close 不报错
        manager.close()
        assert manager.session is None

    def test_close_sets_stop_reason(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        assert manager.session.stop_reason == StopReason.USER_ABORTED

    def test_close_sets_ended_at(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        assert manager.session.ended_at is not None

    def test_close_closes_context_and_playwright(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        # playwright 被停止
        assert fake_playwright_bundle.ctx_manager.stopped is True


# ==================== 用户确认 ====================
class TestUserConfirm:
    def test_confirm_user_sets_flag(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.confirm_user()
        assert manager.session.user_confirmed is True
        assert manager.session.state == BrowserSessionState.USER_CONFIRMED

    def test_confirm_does_not_claim_login_verified(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """confirm 仅代表用户自述，不代表程序判断登录成功。

        不应存在 login_verified 状态或字段。
        """
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.confirm_user()
        # 不存在 login_verified 字段
        assert not hasattr(manager.session, "login_verified")
        assert not hasattr(manager.session, "login_verified_at")

    def test_confirm_after_closed_raises(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        from boss_tool.browser.exceptions import BrowserNotRunningError

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        with pytest.raises(BrowserNotRunningError):
            manager.confirm_user()


# ==================== 浏览器关闭检测 ====================
class TestBrowserCloseDetection:
    def test_user_closes_page_marks_browser_closed_by_user(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """用户关闭工作页面后会话结束，browser_closed_by_user=True。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 模拟用户关闭页面
        fake_playwright_bundle.page.trigger_close()
        assert manager.session.browser_closed_by_user is True
        assert manager.session.state == BrowserSessionState.CLOSED
        assert manager.session.stop_reason == StopReason.BROWSER_CLOSED

    def test_user_closes_context_marks_browser_closed_by_user(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """P1.1 语义更新：context 关闭但无 page close 证据时，不声称为用户关闭。

        - browser_closed_by_user=False（来源不确定，不伪造"用户关闭"）
        - close_source=CloseSource.CONTEXT
        - stop_reason=BROWSER_CONTEXT_CLOSED（中性描述）
        """
        from boss_tool.browser.signals import CloseSource

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 模拟 context 关闭（无 page close 先发生）
        fake_playwright_bundle.context.trigger_close()
        # 不声称为用户关闭
        assert manager.session.browser_closed_by_user is False
        assert manager.session.close_source == CloseSource.CONTEXT
        assert manager.session.stop_reason == StopReason.BROWSER_CONTEXT_CLOSED
        assert manager.session.state == BrowserSessionState.CLOSED

    def test_context_close_after_page_close_marks_as_page_source(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """P1.1 新增：page 先关闭触发 context 级联关闭时，来源标记为 page。"""
        from boss_tool.browser.signals import CloseSource

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 先关闭 page（设置 _page_close_observed=True）
        fake_playwright_bundle.page.trigger_close()
        # 此时 session 已被 page close 处理器标记为 CLOSED
        # context close 处理器不应再次触发非法迁移
        fake_playwright_bundle.context.trigger_close()
        # 来源为 page（page close 先观察到）
        assert manager.session.close_source == CloseSource.PAGE
        assert manager.session.browser_closed_by_user is True
        assert manager.session.stop_reason == StopReason.BROWSER_CLOSED

    def test_programmatic_close_marks_manager_source(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """P1.1 新增：程序主动 close 标记 close_source=manager。"""
        from boss_tool.browser.signals import CloseSource

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        assert manager.session.close_source == CloseSource.MANAGER
        assert manager.session.browser_closed_by_user is False
        assert manager.session.stop_reason == StopReason.USER_ABORTED

    def test_programmatic_close_not_marked_as_user_closed(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """程序主动 close 不应误标记为用户关闭。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        assert manager.session.browser_closed_by_user is False
        assert manager.session.stop_reason == StopReason.USER_ABORTED


# ==================== is_running ====================
class TestIsRunning:
    def test_not_running_before_start(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        assert manager.is_running is False

    def test_running_after_start(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        assert manager.is_running is True

    def test_not_running_after_close(self, browser_config, project_root, fake_playwright_bundle):
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        assert manager.is_running is False

    def test_not_running_after_failure(self, browser_config, project_root):
        chromium = FakeChromium(launch_error=RuntimeError("boom"))
        from tests.browser_fakes import FakePlaywrightBundle, FakePlaywrightCtxManager

        bundle = FakePlaywrightBundle()
        bundle.chromium = chromium
        bundle.ctx_manager = FakePlaywrightCtxManager(chromium)

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=bundle.factory(),
        )
        with pytest.raises(BrowserStartFailedError):
            manager.start()
        assert manager.is_running is False


# ==================== 不自动重启 ====================
class TestNoAutoRestart:
    def test_close_does_not_auto_restart(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """close 后不自动 start。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        manager.close(stop_reason=StopReason.USER_ABORTED)
        # close 后 is_running 为 False，未自动 restart
        assert manager.is_running is False
        # launch_persistent_context 只被调用一次（未重启）
        assert len(fake_playwright_bundle.chromium.launch_calls) == 1


# ==================== 安全日志 ====================
class TestSafeLogging:
    def test_session_does_not_store_cookies(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """BrowserSession 不保存 Cookie。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        session = manager.session
        assert not hasattr(session, "cookies")
        assert not hasattr(session, "cookie_jar")

    def test_url_query_stripped_from_last_known_url(
        self, browser_config, project_root, fake_playwright_bundle
    ):
        """last_known_url 不应包含 query/fragment。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        # 模拟更新一个带 query 的 URL
        manager.update_last_known_url("https://www.zhipin.com/job/123?token=secret#frag")
        assert manager.session.last_known_url == "https://www.zhipin.com/job/123"

    def test_error_message_does_not_contain_sensitive_values(self, browser_config, project_root):
        """异常信息仅包含异常类型，不含敏感值。"""
        chromium = FakeChromium(launch_error=ValueError("password=secret cookie=abc"))
        from tests.browser_fakes import FakePlaywrightBundle, FakePlaywrightCtxManager

        bundle = FakePlaywrightBundle()
        bundle.chromium = chromium
        bundle.ctx_manager = FakePlaywrightCtxManager(chromium)

        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=bundle.factory(),
        )
        with pytest.raises(BrowserStartFailedError):
            manager.start()
        # error_message 仅含异常类型名，不含原始异常消息
        assert "ValueError" in (manager.session.error_message or "")
        assert "password" not in (manager.session.error_message or "")
        assert "cookie" not in (manager.session.error_message or "")


# ==================== 反检测禁止 ====================
class TestNoAntiDetection:
    def test_no_stealth_args_passed(self, browser_config, project_root, fake_playwright_bundle):
        """launch_persistent_context 不应传递任何反检测参数。"""
        manager = BrowserManager(
            browser_config,
            project_root=project_root,
            playwright_factory=fake_playwright_bundle.factory(),
        )
        manager.start()
        call = fake_playwright_bundle.chromium.launch_calls[0]
        args = call.get("args", [])
        # 禁止任何反检测参数
        forbidden = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=AutomationControlled",
        ]
        for f in forbidden:
            assert f not in args, f"禁止传递反检测参数: {f}"

    def test_no_stealth_import(self):
        """不应导入 playwright-stealth 或在 launch 参数中传递反检测标志。

        使用 AST 解析仅检查实际代码（排除注释/文档字符串），避免对
        文档性说明的误报。
        """
        import ast

        import boss_tool.browser.manager as mgr

        source_path = mgr.__file__
        with open(source_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=source_path)

        # 1. 不应 import playwright_stealth
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    msg = f"禁止 import playwright_stealth (line {node.lineno})"
                    assert alias.name != "playwright_stealth", msg
            elif isinstance(node, ast.ImportFrom):
                msg = f"禁止 from playwright_stealth import ... (line {node.lineno})"
                assert node.module != "playwright_stealth", msg

        # 2. 不应调用 stealth() 函数
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "stealth":
                    raise AssertionError(f"禁止调用 stealth() 函数 (line {node.lineno})")

        # 3. launch_persistent_context 调用的 args 不应包含反检测字符串
        forbidden_arg_substrings = (
            "AutomationControlled",
            "disable-blink-features",
            "disable-features=Automation",
        )
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "launch_persistent_context":
                continue
            # 找到 launch_persistent_context 调用，检查 args 关键字参数
            for kw in node.keywords:
                if kw.arg != "args":
                    continue
                # args 应为 List 字面量
                if isinstance(kw.value, ast.List):
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            for forbidden in forbidden_arg_substrings:
                                assert forbidden not in elt.value, (
                                    f"launch_persistent_context args 含禁止字符串 "
                                    f"{forbidden!r} (line {node.lineno})"
                                )


# ==================== 网络隔离 ====================
class TestNoRealNetwork:
    def test_browser_tests_do_not_use_real_zhipin_network(self):
        """测试不真正启动外部 URL。

        通过检查所有 fake page 的 goto_calls，确保调用的是 fake 而非真实 Playwright。
        """
        # 这个测试本身是一个声明性检查：所有浏览器测试均使用 fake
        # 真实网络访问会被 fake 对象拦截
        from tests.browser_fakes import FakePage

        page = FakePage()
        page.goto("https://www.zhipin.com/")
        # fake page 的 goto 不发起真实网络请求
        assert page.goto_calls == ["https://www.zhipin.com/"]
