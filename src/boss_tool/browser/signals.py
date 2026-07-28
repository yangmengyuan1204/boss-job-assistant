"""浏览器会话状态机与停止信号。

状态机：

    created
       │
       ▼
    starting  ─────► failed
       │
       ▼
    waiting_for_user  ─────► failed
       │
       │ (user confirm)
       ▼
    user_confirmed
       │
       │ (user quit / Ctrl+C / browser closed)
       ▼
    closing  ─────► failed
       │
       ▼
    closed

规则：
- 任何状态均可因异常转入 failed
- closing/closed/failed 为终态，不可回退
- 不允许从 user_confirmed 自动回到 waiting_for_user（不自动重新登录）
"""

from __future__ import annotations

from enum import Enum


class BrowserSessionState(str, Enum):
    """浏览器会话状态。

    继承 str 便于序列化与日志输出。
    """

    CREATED = "created"
    STARTING = "starting"
    WAITING_FOR_USER = "waiting_for_user"
    USER_CONFIRMED = "user_confirmed"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """是否为终态（不可再迁移）。"""
        return self in (BrowserSessionState.CLOSED, BrowserSessionState.FAILED)


class CloseSource(str, Enum):
    """浏览器关闭来源（用于区分用户关闭、异常断开与程序主动关闭）。

    P1.1 新增：避免把所有非程序主动关闭一律标记为"用户关闭"。

    取值：
    - manager: BrowserManager.close() 主动调用
    - page: 明确收到 page "close" 事件（可较可靠判断为用户关闭唯一工作页面）
    - context: context 关闭但此前没有 page close 证据（来源不确定）
    - startup_failure: 启动阶段失败
    - unknown: 未知来源
    """

    MANAGER = "manager"
    PAGE = "page"
    CONTEXT = "context"
    STARTUP_FAILURE = "startup_failure"
    UNKNOWN = "unknown"


# 允许的状态迁移
_TRANSITIONS: dict[BrowserSessionState, frozenset[BrowserSessionState]] = {
    BrowserSessionState.CREATED: frozenset(
        {BrowserSessionState.STARTING, BrowserSessionState.FAILED, BrowserSessionState.CLOSED}
    ),
    BrowserSessionState.STARTING: frozenset(
        {
            BrowserSessionState.WAITING_FOR_USER,
            BrowserSessionState.FAILED,
            BrowserSessionState.CLOSING,
        }
    ),
    BrowserSessionState.WAITING_FOR_USER: frozenset(
        {
            BrowserSessionState.USER_CONFIRMED,
            BrowserSessionState.CLOSING,
            BrowserSessionState.FAILED,
        }
    ),
    BrowserSessionState.USER_CONFIRMED: frozenset(
        {BrowserSessionState.CLOSING, BrowserSessionState.FAILED}
    ),
    BrowserSessionState.CLOSING: frozenset(
        {BrowserSessionState.CLOSED, BrowserSessionState.FAILED}
    ),
    BrowserSessionState.CLOSED: frozenset(),
    BrowserSessionState.FAILED: frozenset(),
}


def can_transition(src: BrowserSessionState, dst: BrowserSessionState) -> bool:
    """是否允许从 src 迁移到 dst。"""
    return dst in _TRANSITIONS.get(src, frozenset())


__all__ = ["BrowserSessionState", "CloseSource", "can_transition"]
