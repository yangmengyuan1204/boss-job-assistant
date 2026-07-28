"""浏览器自动化层。

P1 阶段实现：
- 可见浏览器（headed）启动
- 持久化用户目录加载
- 用户手动登录确认
- 异常检测与立即停止

P1.1 修复：
- 统一首页 URL 校验（validate_home_url）
- 改进关闭来源语义（CloseSource）
- CLI 非阻塞命令循环（CommandSource）

永久禁止实现：
- stealth 插件
- 修改 navigator.webdriver 等自动化标志
- 验证码识别或自动绕过
- 代理池、IP 轮换
- Cookie 跨账号导入导出

本模块仅作为安全、可测试的浏览器基础层，不实现任何采集/解析逻辑。
"""

from __future__ import annotations

from boss_tool.browser.commands import (
    CommandSource,
    FakeCommandSource,
    ThreadedCommandSource,
)
from boss_tool.browser.exceptions import (
    BrowserAlreadyRunningError,
    BrowserClosedByUserError,
    BrowserError,
    BrowserNotRunningError,
    BrowserStartFailedError,
    ChromiumNotInstalledError,
    HomePageOpenFailedError,
    InvalidUserDataDirError,
    PlaywrightNotInstalledError,
)
from boss_tool.browser.manager import (
    BrowserManager,
    PlaywrightFactory,
    is_home_url_allowed,
    redact_url,
    validate_home_url,
    validate_user_data_dir,
)
from boss_tool.browser.session import BrowserSession
from boss_tool.browser.signals import BrowserSessionState, CloseSource, can_transition

__all__ = [
    # 异常
    "BrowserError",
    "BrowserAlreadyRunningError",
    "BrowserClosedByUserError",
    "BrowserNotRunningError",
    "BrowserStartFailedError",
    "ChromiumNotInstalledError",
    "HomePageOpenFailedError",
    "InvalidUserDataDirError",
    "PlaywrightNotInstalledError",
    # 状态机
    "BrowserSessionState",
    "CloseSource",
    "can_transition",
    "BrowserSession",
    # 管理器
    "BrowserManager",
    "PlaywrightFactory",
    # 命令源（P1.1）
    "CommandSource",
    "ThreadedCommandSource",
    "FakeCommandSource",
    # 工具
    "validate_user_data_dir",
    "redact_url",
    "validate_home_url",
    "is_home_url_allowed",
]
