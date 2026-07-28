"""测试专用 fake Playwright 对象。

所有 fake 对象均不访问真实网络，仅用于单元测试。

禁止：在测试中真正启动 Playwright 或访问 https://www.zhipin.com。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class FakePage:
    """fake Playwright Page。"""

    def __init__(self, *, goto_ok: bool = True, url: str = "https://www.zhipin.com/"):
        self._closed = False
        self._goto_ok = goto_ok
        self._handlers: dict[str, list] = {"close": []}
        self._goto_calls: list[str] = []
        self.url = url

    def goto(self, url: str, **kwargs) -> None:
        self._goto_calls.append(url)
        if not self._goto_ok:
            raise RuntimeError("goto failed (test)")
        self.url = url

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        for h in self._handlers.get("close", []):
            h()

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    @property
    def goto_calls(self) -> list[str]:
        return list(self._goto_calls)

    def trigger_close(self) -> None:
        """测试专用：模拟页面被外部关闭。"""
        for h in self._handlers.get("close", []):
            h()


class FakeContext:
    """fake Playwright BrowserContext。"""

    def __init__(self, *, pages: list[FakePage] | None = None):
        self._closed = False
        self._pages: list[FakePage] = pages if pages is not None else []
        self._handlers: dict[str, list] = {"close": []}

    @property
    def pages(self) -> list[FakePage]:
        return list(self._pages)

    def new_page(self) -> FakePage:
        page = FakePage()
        self._pages.append(page)
        return page

    def close(self) -> None:
        self._closed = True
        for h in self._handlers.get("close", []):
            h()

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def trigger_close(self) -> None:
        """测试专用：模拟 context 被外部关闭。"""
        for h in self._handlers.get("close", []):
            h()


class FakeChromium:
    """fake Playwright chromium 模块。"""

    def __init__(
        self,
        *,
        context: FakeContext | None = None,
        launch_error: Exception | None = None,
    ):
        self._context = context if context is not None else FakeContext()
        self._launch_error = launch_error
        self.launch_calls: list[dict] = []

    def launch_persistent_context(self, user_data_dir: str, **kwargs) -> FakeContext:
        self.launch_calls.append({"user_data_dir": user_data_dir, **kwargs})
        if self._launch_error is not None:
            raise self._launch_error
        return self._context


class FakePlaywrightInstance:
    """fake playwright 实例（sync_playwright().start() 返回值）。"""

    def __init__(self, chromium: FakeChromium):
        self.chromium = chromium


class FakePlaywrightCtxManager:
    """fake sync_playwright() 返回的上下文管理器（含 .start()/.stop()）。"""

    def __init__(self, chromium: FakeChromium):
        self._instance = FakePlaywrightInstance(chromium)
        self.started = False
        self.stopped = False

    def start(self) -> FakePlaywrightInstance:
        self.started = True
        return self._instance

    def stop(self) -> None:
        self.stopped = True


@dataclass
class FakePlaywrightBundle:
    """打包一组 fake 对象，便于注入 BrowserManager。

    Attributes:
        page: 默认工作页面
        context: fake BrowserContext
        chromium: fake chromium 模块
        ctx_manager: fake sync_playwright() 返回值
        factory: 可直接传给 BrowserManager(playwright_factory=...) 的可调用对象
    """

    page: FakePage = field(default_factory=FakePage)
    context: FakeContext | None = None
    chromium: FakeChromium | None = None
    ctx_manager: FakePlaywrightCtxManager | None = None

    def __post_init__(self) -> None:
        if self.context is None:
            self.context = FakeContext(pages=[self.page])
        if self.chromium is None:
            self.chromium = FakeChromium(context=self.context)
        if self.ctx_manager is None:
            self.ctx_manager = FakePlaywrightCtxManager(self.chromium)

    def factory(self):
        """返回可注入 BrowserManager 的工厂函数。"""
        return lambda: self.ctx_manager


class FailingPlaywrightFactory:
    """始终抛出指定异常的工厂，用于测试启动失败路径。"""

    def __init__(self, error: Exception):
        self._error = error

    def __call__(self):
        raise self._error


__all__ = [
    "FakePage",
    "FakeContext",
    "FakeChromium",
    "FakePlaywrightInstance",
    "FakePlaywrightCtxManager",
    "FakePlaywrightBundle",
    "FailingPlaywrightFactory",
]
