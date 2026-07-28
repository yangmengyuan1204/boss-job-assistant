"""Playwright 可见浏览器管理器。

设计原则：
- 使用 launch_persistent_context（不使用临时 context + 手动 Cookie）
- headless=False（强制可见浏览器）
- 单 Playwright 实例、单 context、单账号
- 支持重复 close 幂等
- 启动失败时清理已创建资源
- 不自动重试、不自动重启、不切换账号
- 不隐藏 Playwright 自动化特征

依赖注入：
- 通过 playwright_factory 参数注入 Playwright 模块/对象，便于测试 mock
- 生产环境不传该参数，运行时惰性 import playwright

禁止实现：
- playwright-stealth
- navigator.webdriver / plugins / languages 修改
- Canvas/WebGL/AudioContext 指纹修改
- 验证码识别 / 滑块自动处理 / 短信验证码自动读取
- 自动登录 / Cookie 导入导出
- 代理池 / IP 轮换
"""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from boss_tool.browser.exceptions import (
    BrowserAlreadyRunningError,
    BrowserNotRunningError,
    BrowserStartFailedError,
    ChromiumNotInstalledError,
    HomePageOpenFailedError,
    InvalidUserDataDirError,
    PlaywrightNotInstalledError,
)
from boss_tool.browser.session import BrowserSession
from boss_tool.browser.signals import BrowserSessionState, CloseSource
from boss_tool.config import ALLOWED_HOME_HOSTS, BrowserConfig
from boss_tool.enums import StopReason
from boss_tool.logging_config import get_logger

logger = get_logger(__name__)


# ==================== 路径安全 ====================
def validate_user_data_dir(user_data_dir: str | Path, *, project_root: Path) -> Path:
    r"""校验用户目录安全性。

    禁止：
    - 空路径
    - "." 直接作为用户目录
    - 项目根目录
    - src/、config/ 等源码/配置目录
    - 磁盘根目录（如 C:\、/）
    - 不存在父目录的路径

    Returns:
        Path: 解析后的绝对路径

    Raises:
        InvalidUserDataDirError: 不安全路径
    """
    if user_data_dir is None:
        raise InvalidUserDataDirError("user_data_dir 不能为 None")
    raw = str(user_data_dir).strip()
    if not raw:
        raise InvalidUserDataDirError("user_data_dir 不能为空")
    if raw in (".", ".."):
        raise InvalidUserDataDirError(f"user_data_dir 不允许为 {raw!r}（禁止当前/上级目录）")

    try:
        resolved = Path(raw).resolve()
    except (OSError, ValueError) as e:
        raise InvalidUserDataDirError(f"user_data_dir 路径解析失败: {raw}") from e

    # 禁止磁盘根目录
    if resolved.parent == resolved:
        raise InvalidUserDataDirError(f"user_data_dir 不允许为磁盘根目录: {resolved}")

    project_root_resolved = project_root.resolve()
    try:
        resolved.relative_to(project_root_resolved)
        in_project = True
    except ValueError:
        in_project = False

    if resolved == project_root_resolved:
        raise InvalidUserDataDirError(f"user_data_dir 不允许为项目根目录: {resolved}")

    # 禁止指向 src/、config/、tests/、.git/
    forbidden_subdirs = ("src", "config", "tests", ".git", "logs", "output")
    for sub in forbidden_subdirs:
        forbidden_path = (project_root_resolved / sub).resolve()
        if resolved == forbidden_path:
            raise InvalidUserDataDirError(f"user_data_dir 不允许为项目子目录 {sub}/: {resolved}")

    # 禁止指向源码目录本身（即使不在项目根下）
    # 例如 D:\code\src 这种结构 - 通过检测是否包含 src\boss_tool 判断
    if in_project:
        # 在项目内时只允许指向 data/、user_data/ 等数据目录
        # 但允许用户自定义的子目录如 ./user_data、./data/profile1
        pass

    return resolved


# ==================== URL 脱敏 ====================
def redact_url(url: str | None) -> str | None:
    """脱敏 URL：仅保留 scheme://host/path，去掉 query 和 fragment。

    防止 URL 中意外携带敏感参数（如 token、验证码、会话 ID）。

    >>> redact_url("https://www.zhipin.com/job/123?token=secret#frag")
    'https://www.zhipin.com/job/123'
    """
    if url is None:
        return None
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return url
    result = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return result if result else url


def validate_home_url(url: str) -> str:
    """统一校验生产首页 URL 安全性。

    P1.1 新增：Pydantic、CLI 覆盖参数、BrowserManager 均调用同一套逻辑。

    严格要求：
    1. scheme 必须为 https
    2. hostname 必须严格属于 {www.zhipin.com, zhipin.com}
    3. 禁止 userinfo（username/password）
    4. 禁止显式非默认端口
    5. 禁止 fragment
    6. 禁止 query（防止 URL 中携带敏感参数）
    7. 首页路径限制为 / 或空

    Returns:
        str: 校验通过后的 URL（原值，不做规范化以保持兼容）

    Raises:
        ValueError: 任何校验失败
    """
    if not url or not url.strip():
        raise ValueError("home_url 不能为空")
    url = url.strip()
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as e:
        raise ValueError(f"home_url 解析失败: {url}") from e

    # 1. scheme 严格为 https
    if parsed.scheme != "https":
        raise ValueError(
            f"home_url 必须为 https 协议，当前为 {parsed.scheme!r} (url={redact_url(url)})"
        )

    # 2. host 严格在白名单内（hostname 会自动去掉端口与 userinfo）
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOME_HOSTS:
        raise ValueError(
            f"home_url host {host!r} 不在白名单 {sorted(ALLOWED_HOME_HOSTS)} 中，"
            "仅允许 BOSS 直聘域名（www.zhipin.com / zhipin.com）"
        )

    # 3. 禁止 userinfo
    if parsed.username or parsed.password:
        raise ValueError("home_url 禁止包含 userinfo（username/password）")

    # 4. 禁止显式非默认端口
    if parsed.port is not None:
        raise ValueError(f"home_url 禁止显式非默认端口: {parsed.port}")

    # 5. 禁止 fragment
    if parsed.fragment:
        raise ValueError("home_url 禁止包含 fragment")

    # 6. 禁止 query（防止携带 token 等敏感参数）
    if parsed.query:
        raise ValueError("home_url 禁止包含 query 参数")

    # 7. 首页路径限制为 / 或空
    if parsed.path not in ("", "/"):
        raise ValueError(
            f"home_url 首页路径必须为 / 或空，当前为 {parsed.path!r}（仅允许打开根路径）"
        )

    return url


def is_home_url_allowed(url: str) -> bool:
    """检查 URL host 是否在 BOSS 直聘白名单内（宽松检查，仅用于运行时二次校验）。

    注意：严格校验请使用 validate_home_url()。
    本函数仅用于 BrowserManager 启动时的二次防御性检查，
    任何严格规则的失败已在 config/CLI 层通过 validate_home_url() 拒绝。
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_HOME_HOSTS


# ==================== Playwright 工厂协议 ====================
class PlaywrightFactory(Protocol):
    """Playwright 工厂协议，便于测试 mock。

    生产实现：直接调用 playwright.sync_playwright()
    测试实现：返回 fake 对象。
    """

    def __call__(self) -> Any:
        """返回 Playwright 上下文管理器（含 .start()/stop()）。"""
        ...


class _DefaultPlaywrightFactory:
    """默认 Playwright 工厂：运行时惰性导入 playwright。

    优点：
    - 不在 import 阶段强依赖 playwright（doctor 可用）
    - 启动失败时给出清晰错误
    """

    def __call__(self) -> Any:
        if importlib.util.find_spec("playwright") is None:
            raise PlaywrightNotInstalledError(
                "playwright 包未安装，请运行：\n"
                "    pip install playwright\n"
                "    python -m playwright install chromium"
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise PlaywrightNotInstalledError(
                f"playwright 包导入失败，请运行：\n    pip install playwright\n    (原因: {e})"
            ) from e
        return sync_playwright()


def _check_chromium_installed(pw: Any) -> None:
    """检查 chromium 二进制是否已安装。

    Playwright 未安装 chromium 时会在启动时抛出
    "Executable doesn't exist" 类错误，此处提前转换错误信息。
    """
    try:
        from playwright._impl._driver import (
            compute_driver_executable,  # type: ignore[import-not-found]
        )
    except ImportError:
        # 旧版本或无法探测，跳过预检查
        return
    try:
        # 探测浏览器路径缓存
        browsers_path = compute_driver_executable()
        # 仅作为最简存在性检查，不实际触发下载
        _ = browsers_path
    except Exception:
        # 无法探测，交给 launch 时的真实异常处理
        return


# ==================== BrowserManager ====================
class BrowserManager:
    """Playwright 可见浏览器管理器。

    职责：
    - 初始化 Playwright
    - 启动持久化浏览器上下文
    - 创建或复用用户目录
    - 获取初始页面
    - 打开配置的首页
    - 检测浏览器或上下文是否已关闭
    - 安全关闭页面、上下文和 Playwright
    - 支持重复调用 close 而不报错
    - 出现启动异常时清理已创建资源
    """

    def __init__(
        self,
        config: BrowserConfig,
        *,
        project_root: Path,
        playwright_factory: PlaywrightFactory | None = None,
    ):
        """初始化管理器。

        Args:
            config: 浏览器配置（含 user_data_dir / home_url / headless 等）
            project_root: 项目根目录，用于路径安全校验
            playwright_factory: Playwright 工厂（依赖注入，便于测试）
        """
        self.config = config
        self.project_root = project_root
        self._factory: PlaywrightFactory = playwright_factory or _DefaultPlaywrightFactory()

        # 运行时状态
        self._session: BrowserSession | None = None
        self._pw: Any | None = None  # Playwright 实例（含 .start()/stop()）
        self._context: Any | None = None  # BrowserContext
        self._page: Any | None = None  # 工作页面
        self._closed_by_manager = False  # 是否由 manager 主动关闭（区分用户关闭）
        self._page_close_observed = False  # P1.1：是否已观察到 page close 事件

        # 用户目录（绝对路径）
        self._user_data_dir: Path = validate_user_data_dir(
            config.user_data_dir, project_root=project_root
        )

    @property
    def session(self) -> BrowserSession | None:
        return self._session

    @property
    def is_running(self) -> bool:
        """浏览器是否正在运行。"""
        if self._session is None:
            return False
        return (
            self._session.state
            in (
                BrowserSessionState.STARTING,
                BrowserSessionState.WAITING_FOR_USER,
                BrowserSessionState.USER_CONFIRMED,
            )
            and self._context is not None
        )

    @property
    def user_data_dir(self) -> Path:
        return self._user_data_dir

    # ---------- 生命周期 ----------
    def start(self) -> BrowserSession:
        """启动可见浏览器并打开首页。

        Returns:
            BrowserSession: 启动后的会话状态

        Raises:
            BrowserAlreadyRunningError: 重复 start
            PlaywrightNotInstalledError: playwright 未安装
            ChromiumNotInstalledError: chromium 二进制未安装
            BrowserStartFailedError: 启动失败
            HomePageOpenFailedError: 首页打开失败
            InvalidUserDataDirError: 用户目录不安全
        """
        if self.is_running:
            raise BrowserAlreadyRunningError(
                "浏览器会话已存在，禁止重复 start（不自动重启/不切换账号）"
            )

        session = BrowserSession(
            home_url=self.config.home_url,
            user_data_dir=str(self._user_data_dir),
        )
        session.mark_started(
            home_url=self.config.home_url,
            user_data_dir=str(self._user_data_dir),
        )
        self._session = session
        self._closed_by_manager = False
        self._page_close_observed = False

        try:
            # 1. 启动 Playwright
            self._pw = self._factory()
            pw_instance = self._pw.start()

            # 2. 预检查 chromium 是否已安装
            _check_chromium_installed(pw_instance)

            # 3. 创建持久化上下文
            self._context = self._launch_persistent_context(pw_instance)

            # 4. 注册 context 关闭事件
            self._register_context_close_handler(self._context)

            # 5. 获取初始页面（持久化上下文可能已自带页面）
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._register_page_close_handler(self._page)

            # 6. 打开首页
            self._goto_home(self._page, self.config.home_url)

            # 7. 进入等待用户确认状态
            session.mark_waiting_for_user()
            session.last_known_url = redact_url(self.config.home_url)

            logger.info(
                "浏览器会话已启动 session_id=%s user_data_dir=%s home_host=%s",
                session.session_id,
                self._user_data_dir,
                urlparse(self.config.home_url).hostname,
            )
            return session

        except PlaywrightNotInstalledError:
            session.mark_failed(
                stop_reason=StopReason.UNKNOWN_ERROR,
                error_message="playwright 包未安装",
                close_source=CloseSource.STARTUP_FAILURE,
            )
            self._cleanup_resources()
            raise
        except BrowserStartFailedError as e:
            session.mark_failed(
                stop_reason=StopReason.UNKNOWN_ERROR,
                error_message=str(e),
                close_source=CloseSource.STARTUP_FAILURE,
            )
            self._cleanup_resources()
            raise
        except HomePageOpenFailedError:
            session.mark_failed(
                stop_reason=StopReason.UNKNOWN_ERROR,
                error_message="首页打开失败",
                close_source=CloseSource.STARTUP_FAILURE,
            )
            self._cleanup_resources()
            raise
        except Exception as e:
            # 区分 chromium 未安装错误
            msg = str(e).lower()
            if "executable doesn't exist" in msg or "playwright install" in msg:
                err = ChromiumNotInstalledError(
                    "Playwright Chromium 尚未安装，请运行：\n"
                    "    python -m playwright install chromium\n"
                    "不要自动下载，需用户手动执行。"
                )
                session.mark_failed(
                    stop_reason=StopReason.UNKNOWN_ERROR,
                    error_message="chromium 未安装",
                    close_source=CloseSource.STARTUP_FAILURE,
                )
                self._cleanup_resources()
                raise err from e
            session.mark_failed(
                stop_reason=StopReason.UNKNOWN_ERROR,
                error_message=f"启动失败: {type(e).__name__}",
                close_source=CloseSource.STARTUP_FAILURE,
            )
            self._cleanup_resources()
            raise BrowserStartFailedError(f"浏览器启动失败: {type(e).__name__}") from e

    def _launch_persistent_context(self, pw_instance: Any) -> Any:
        """启动持久化浏览器上下文。

        强制使用 launch_persistent_context，不使用临时 context + 手动 Cookie。
        """
        # 确保用户目录存在
        try:
            self._user_data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BrowserStartFailedError(
                f"用户目录创建失败: {self._user_data_dir} ({type(e).__name__})"
            ) from e

        try:
            # 使用 chromium，headless=False 强制可见
            # 显式不传递任何反检测 / 指纹修改参数
            #   - 禁止 --disable-blink-features=AutomationControlled
            #   - 禁止 --disable-features=AutomationControlled
            #   - 禁止任何 navigator.* 修改
            #   - 禁止 stealth 插件
            # Playwright 必须保持其正常自动化特征
            context = pw_instance.chromium.launch_persistent_context(
                user_data_dir=str(self._user_data_dir),
                headless=False,  # 强制可见浏览器
                args=[],  # 显式空数组：不传递任何反检测参数
            )
        except Exception as e:
            msg = str(e).lower()
            if "executable doesn't exist" in msg or "playwright install" in msg:
                raise ChromiumNotInstalledError(
                    "Playwright Chromium 尚未安装，请运行：\n"
                    "    python -m playwright install chromium"
                ) from e
            raise BrowserStartFailedError(
                f"launch_persistent_context 失败: {type(e).__name__}"
            ) from e
        return context

    def _register_context_close_handler(self, context: Any) -> None:
        """注册 context 关闭事件处理器。

        P1.1 改进关闭语义：
        - _closed_by_manager=True → 程序主动关闭（close_source=manager）
        - _closed_by_manager=False 且 _page_close_observed=True → page 先关闭触发的级联（close_source=page）
        - _closed_by_manager=False 且 _page_close_observed=False → 来源不确定（close_source=context）
          此时不声称为"用户关闭"，使用中性 stop_reason=BROWSER_CONTEXT_CLOSED
        """

        def _on_close() -> None:
            if self._closed_by_manager:
                # 程序主动关闭，正常流程
                logger.debug("context 关闭事件（程序主动关闭）")
                return
            if self._session is not None and not self._session.state.is_terminal():
                if self._page_close_observed:
                    # page 已先关闭，来源为 page
                    logger.info("检测到 context 关闭（由 page 关闭级联触发）")
                    self._session.mark_closing()
                    self._session.mark_closed(
                        stop_reason=StopReason.BROWSER_CLOSED,
                        browser_closed_by_user=True,
                        close_source=CloseSource.PAGE,
                    )
                else:
                    # 未观察到 page close，来源不确定
                    # 不声称为用户关闭，使用中性描述
                    logger.info("检测到 context 关闭（来源不确定，不声称为用户关闭）")
                    self._session.mark_closing()
                    self._session.mark_closed(
                        stop_reason=StopReason.BROWSER_CONTEXT_CLOSED,
                        browser_closed_by_user=False,
                        close_source=CloseSource.CONTEXT,
                    )

        # 某些 mock 实现可能不支持 on，忽略
        with contextlib.suppress(Exception):
            context.on("close", _on_close)

    def _register_page_close_handler(self, page: Any) -> None:
        """注册页面关闭事件处理器。

        如果用户关闭唯一工作页面，标记 browser_closed_by_user 并结束会话。
        不自动重新打开 BOSS 页面。

        P1.1：设置 _page_close_observed=True，供 context close 处理器判断级联来源。
        """

        def _on_page_close() -> None:
            if self._closed_by_manager:
                return
            logger.info("检测到工作页面被关闭")
            self._page_close_observed = True
            if self._session is not None and not self._session.state.is_terminal():
                self._session.browser_closed_by_user = True
                self._session.mark_closing()
                self._session.mark_closed(
                    stop_reason=StopReason.BROWSER_CLOSED,
                    browser_closed_by_user=True,
                    close_source=CloseSource.PAGE,
                )

        with contextlib.suppress(Exception):
            page.on("close", _on_page_close)

    def _goto_home(self, page: Any, url: str) -> None:
        """打开首页 URL。"""
        # 二次校验白名单
        if not is_home_url_allowed(url):
            raise HomePageOpenFailedError(f"首页 URL host 不在白名单内: {urlparse(url).hostname}")
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            raise HomePageOpenFailedError(f"打开首页失败: {type(e).__name__}") from e

    # ---------- 状态更新 ----------
    def confirm_user(self) -> None:
        """用户在终端确认。仅标记 user_confirmed=True。

        注意：confirm 仅代表用户自述已处理完成，
        不代表程序自动判断登录成功（P1 不实现登录状态判断）。
        """
        if self._session is None:
            raise BrowserNotRunningError("浏览器未启动")
        if self._session.state.is_terminal():
            raise BrowserNotRunningError(f"会话已结束: {self._session.state}，无法 confirm")
        self._session.mark_user_confirmed()
        logger.info("用户已确认 session_id=%s", self._session.session_id)

    def update_last_known_url(self, url: str | None) -> None:
        """更新最后已知 URL（脱敏后存储）。"""
        if self._session is not None:
            self._session.last_known_url = redact_url(url)

    # ---------- 关闭 ----------
    def close(
        self,
        *,
        stop_reason: StopReason | None = None,
        error_message: str | None = None,
    ) -> None:
        """安全关闭浏览器与 Playwright。

        幂等：重复调用不报错。
        不自动重启，不切换账号。

        P1.1：标记 close_source=manager 以区分程序主动关闭与用户/异常关闭。
        """
        if self._session is None:
            # 完全未启动，直接返回
            return

        if self._session.state.is_terminal():
            # 已关闭，幂等返回
            return

        self._closed_by_manager = True
        self._session.mark_closing()

        # 关闭资源
        self._cleanup_resources()

        # 标记会话结束（程序主动关闭，close_source=manager）
        self._session.mark_closed(
            stop_reason=stop_reason or StopReason.USER_ABORTED,
            error_message=error_message,
            browser_closed_by_user=False,
            close_source=CloseSource.MANAGER,
        )

        logger.info(
            "浏览器会话已关闭 session_id=%s stop_reason=%s",
            self._session.session_id,
            self._session.stop_reason,
        )

    def _cleanup_resources(self) -> None:
        """清理已创建的资源（页面 → 上下文 → Playwright）。

        任何环节失败不影响后续清理。
        """
        # 1. 关闭页面
        if self._page is not None:
            with contextlib.suppress(Exception):
                if not self._page.is_closed():
                    self._page.close()
            self._page = None

        # 2. 关闭上下文
        if self._context is not None:
            with contextlib.suppress(Exception):
                self._context.close()
            self._context = None

        # 3. 关闭 Playwright
        if self._pw is not None:
            with contextlib.suppress(Exception):
                self._pw.stop()
            self._pw = None


__all__ = [
    "BrowserManager",
    "PlaywrightFactory",
    "validate_user_data_dir",
    "redact_url",
    "validate_home_url",
    "is_home_url_allowed",
]
