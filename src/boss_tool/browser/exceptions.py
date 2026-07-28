"""浏览器层异常。

所有异常均不暴露敏感信息（Cookie/账号/密码/验证码）。
错误信息仅用于定位问题，不携带任何用户身份信息。
"""

from __future__ import annotations


class BrowserError(Exception):
    """浏览器层基础异常。"""


class PlaywrightNotInstalledError(BrowserError):
    """playwright 包未安装。

    提示用户运行：
        pip install playwright
        python -m playwright install chromium
    """


class ChromiumNotInstalledError(BrowserError):
    """playwright 包已安装但 chromium 浏览器二进制未安装。

    提示用户运行：
        python -m playwright install chromium
    """


class InvalidUserDataDirError(BrowserError):
    """用户目录不安全（指向项目根/源码目录/磁盘根等危险路径）。"""


class BrowserAlreadyRunningError(BrowserError):
    """浏览器会话已存在，重复 start 被拒绝。"""


class BrowserNotRunningError(BrowserError):
    """浏览器未启动即调用 close 等 API。"""


class BrowserStartFailedError(BrowserError):
    """浏览器启动失败（Playwright 异常）。"""


class HomePageOpenFailedError(BrowserError):
    """首页打开失败（goto 异常）。"""


class BrowserClosedByUserError(BrowserError):
    """用户主动关闭浏览器。"""


__all__ = [
    "BrowserError",
    "PlaywrightNotInstalledError",
    "ChromiumNotInstalledError",
    "InvalidUserDataDirError",
    "BrowserAlreadyRunningError",
    "BrowserNotRunningError",
    "BrowserStartFailedError",
    "HomePageOpenFailedError",
    "BrowserClosedByUserError",
]
