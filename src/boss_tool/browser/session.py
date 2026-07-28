"""浏览器会话状态模型。

BrowserSession 仅记录会话级状态，不保存任何敏感信息：
- 不保存 Cookie / localStorage / sessionStorage
- 不保存验证码、手机号、账号密码
- 不保存页面 HTML 或截图
- 不保存浏览器指纹信息
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from boss_tool.browser.signals import BrowserSessionState, CloseSource, can_transition
from boss_tool.enums import StopReason


class BrowserSession(BaseModel):
    """浏览器会话状态。

    所有字段均为会话级元数据，不含敏感信息。
    状态迁移通过 transition_to() 校验，避免非法迁移。

    P1.1 新增字段 close_source 用于区分关闭来源：
    - manager: BrowserManager.close() 主动调用
    - page: 明确收到 page "close" 事件
    - context: context 关闭但此前无 page close 证据
    - startup_failure: 启动阶段失败
    - unknown: 未知来源

    注意：context 来源无法可靠判断是否为用户主动行为，
    故 browser_closed_by_user 仅在 page 来源时为 True。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    session_id: str = Field(default_factory=lambda: uuid4().hex, description="会话唯一 ID")
    started_at: datetime | None = Field(default=None, description="浏览器启动时间")
    ended_at: datetime | None = Field(default=None, description="会话结束时间")
    state: BrowserSessionState = Field(
        default=BrowserSessionState.CREATED, description="会话状态机"
    )

    # 用户交互
    user_confirmed: bool = Field(default=False, description="用户是否在终端确认")
    browser_closed_by_user: bool = Field(
        default=False, description="是否由用户关闭浏览器窗口（非程序主动关闭）"
    )

    # 停止原因与关闭来源
    stop_reason: StopReason | None = Field(default=None, description="停止原因")
    close_source: CloseSource | None = Field(
        default=None,
        description="浏览器关闭来源（manager/page/context/startup_failure/unknown）",
    )

    # 路径与 URL（仅记录 host/path，不记录 query/fragment）
    home_url: str | None = Field(default=None, description="配置的首页 URL")
    user_data_dir: str | None = Field(
        default=None, description="用户目录规范化绝对路径（不含敏感信息）"
    )
    last_known_url: str | None = Field(
        default=None, description="最后已知 URL（脱敏后，仅 scheme://host/path）"
    )

    # 异常
    error_message: str | None = Field(default=None, description="异常信息（不包含敏感值）")

    def transition_to(self, dst: BrowserSessionState) -> None:
        """校验并执行状态迁移。

        Args:
            dst: 目标状态

        Raises:
            ValueError: 非法状态迁移
        """
        if self.state.is_terminal():
            raise ValueError(f"会话已处于终态 {self.state!r}，不可迁移到 {dst!r}")
        if not can_transition(self.state, dst):
            raise ValueError(f"非法状态迁移: {self.state!r} -> {dst!r}")
        self.state = dst

    def mark_started(self, *, home_url: str, user_data_dir: str) -> None:
        """标记会话已启动。"""
        self.started_at = datetime.now()
        self.home_url = home_url
        self.user_data_dir = user_data_dir
        self.transition_to(BrowserSessionState.STARTING)

    def mark_waiting_for_user(self) -> None:
        """标记会话进入等待用户确认状态。"""
        self.transition_to(BrowserSessionState.WAITING_FOR_USER)

    def mark_user_confirmed(self) -> None:
        """标记用户已确认。

        注意：confirm 仅代表用户自述已处理完成，
        不代表程序自动判断登录成功。
        """
        self.user_confirmed = True
        self.transition_to(BrowserSessionState.USER_CONFIRMED)

    def mark_closing(self) -> None:
        """标记会话正在关闭。"""
        if self.state.is_terminal():
            return
        # 从任意非终态均可进入 closing（user_confirmed/waiting_for_user/starting）
        self.state = BrowserSessionState.CLOSING

    def mark_closed(
        self,
        *,
        stop_reason: StopReason | None = None,
        error_message: str | None = None,
        browser_closed_by_user: bool = False,
        close_source: CloseSource | None = None,
    ) -> None:
        """标记会话已关闭。

        Args:
            stop_reason: 停止原因
            error_message: 异常信息（不含敏感值）
            browser_closed_by_user: 是否由用户关闭（仅 page 来源可较可靠判断）
            close_source: 关闭来源（manager/page/context/startup_failure/unknown）
        """
        self.ended_at = datetime.now()
        self.stop_reason = stop_reason
        self.error_message = error_message
        self.browser_closed_by_user = browser_closed_by_user
        if close_source is not None:
            self.close_source = close_source
        # closing -> closed 直接设置（幂等）
        if self.state != BrowserSessionState.CLOSED:
            self.state = BrowserSessionState.CLOSED

    def mark_failed(
        self,
        *,
        stop_reason: StopReason | None = None,
        error_message: str | None = None,
        close_source: CloseSource | None = None,
    ) -> None:
        """标记会话失败。"""
        self.ended_at = datetime.now()
        self.stop_reason = stop_reason
        self.error_message = error_message
        if close_source is not None:
            self.close_source = close_source
        if self.state != BrowserSessionState.FAILED:
            self.state = BrowserSessionState.FAILED


__all__ = ["BrowserSession"]
