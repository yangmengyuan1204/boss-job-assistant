"""BrowserSession 状态机测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from boss_tool.browser.session import BrowserSession
from boss_tool.browser.signals import BrowserSessionState, can_transition
from boss_tool.enums import StopReason


class TestBrowserSessionState:
    def test_initial_state_is_created(self):
        s = BrowserSession()
        assert s.state == BrowserSessionState.CREATED
        assert s.user_confirmed is False
        assert s.browser_closed_by_user is False
        assert s.stop_reason is None

    def test_session_has_unique_id(self):
        s1 = BrowserSession()
        s2 = BrowserSession()
        assert s1.session_id != s2.session_id
        assert len(s1.session_id) > 0

    def test_mark_started_sets_fields(self):
        s = BrowserSession()
        s.mark_started(home_url="https://www.zhipin.com/", user_data_dir="/tmp/ud")
        assert s.state == BrowserSessionState.STARTING
        assert s.started_at is not None
        assert s.home_url == "https://www.zhipin.com/"
        assert s.user_data_dir == "/tmp/ud"

    def test_mark_waiting_for_user(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_waiting_for_user()
        assert s.state == BrowserSessionState.WAITING_FOR_USER

    def test_mark_user_confirmed(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_waiting_for_user()
        s.mark_user_confirmed()
        assert s.state == BrowserSessionState.USER_CONFIRMED
        assert s.user_confirmed is True

    def test_mark_closing_from_waiting(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_waiting_for_user()
        s.mark_closing()
        assert s.state == BrowserSessionState.CLOSING

    def test_mark_closing_from_user_confirmed(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_waiting_for_user()
        s.mark_user_confirmed()
        s.mark_closing()
        assert s.state == BrowserSessionState.CLOSING

    def test_mark_closed(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_closing()
        s.mark_closed(stop_reason=StopReason.USER_ABORTED)
        assert s.state == BrowserSessionState.CLOSED
        assert s.ended_at is not None
        assert s.stop_reason == StopReason.USER_ABORTED

    def test_mark_failed(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_failed(
            stop_reason=StopReason.UNKNOWN_ERROR,
            error_message="boom",
        )
        assert s.state == BrowserSessionState.FAILED
        assert s.ended_at is not None
        assert s.stop_reason == StopReason.UNKNOWN_ERROR
        assert s.error_message == "boom"

    def test_mark_closing_idempotent_on_terminal(self):
        """终态时调用 mark_closing 不应报错也不应迁移。"""
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_closing()
        s.mark_closed()
        # 已 closed，再调用 mark_closing 应直接返回
        s.mark_closing()
        assert s.state == BrowserSessionState.CLOSED

    def test_mark_closed_idempotent(self):
        """重复 mark_closed 不报错。"""
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_closing()
        s.mark_closed(stop_reason=StopReason.USER_ABORTED)
        s.mark_closed(stop_reason=StopReason.BROWSER_CLOSED)
        assert s.state == BrowserSessionState.CLOSED
        # ended_at 会被更新为新时间，但状态保持 closed

    def test_illegal_transition_created_to_user_confirmed(self):
        """不能从 CREATED 直接跳到 USER_CONFIRMED。"""
        s = BrowserSession()
        with pytest.raises(ValueError, match="非法状态迁移"):
            s.mark_user_confirmed()

    def test_illegal_transition_closed_to_starting(self):
        """终态不可迁移。"""
        s = BrowserSession()
        s.mark_closed()
        with pytest.raises(ValueError, match="终态"):
            s.transition_to(BrowserSessionState.STARTING)

    def test_illegal_transition_failed_to_starting(self):
        s = BrowserSession()
        s.mark_failed()
        with pytest.raises(ValueError, match="终态"):
            s.transition_to(BrowserSessionState.STARTING)

    def test_browser_closed_by_user_flag(self):
        s = BrowserSession()
        s.mark_started(home_url="https://x", user_data_dir="/tmp")
        s.mark_closing()
        s.mark_closed(
            stop_reason=StopReason.BROWSER_CLOSED,
            browser_closed_by_user=True,
        )
        assert s.browser_closed_by_user is True
        assert s.stop_reason == StopReason.BROWSER_CLOSED

    def test_no_sensitive_fields_stored(self):
        """BrowserSession 不应保存 Cookie / 密码 / 验证码等敏感字段。"""
        s = BrowserSession()
        # extra='forbid' 拒绝未知字段赋值（pydantic 抛出 ValueError）
        with pytest.raises((ValidationError, ValueError)):
            s.cookies = "fake-cookie"  # type: ignore[attr-defined]
        with pytest.raises((ValidationError, ValueError)):
            s.password = "secret"  # type: ignore[attr-defined]


class TestCanTransition:
    def test_created_to_starting(self):
        assert can_transition(BrowserSessionState.CREATED, BrowserSessionState.STARTING)

    def test_starting_to_waiting(self):
        assert can_transition(BrowserSessionState.STARTING, BrowserSessionState.WAITING_FOR_USER)

    def test_waiting_to_confirmed(self):
        assert can_transition(
            BrowserSessionState.WAITING_FOR_USER, BrowserSessionState.USER_CONFIRMED
        )

    def test_confirmed_to_closing(self):
        assert can_transition(BrowserSessionState.USER_CONFIRMED, BrowserSessionState.CLOSING)

    def test_closing_to_closed(self):
        assert can_transition(BrowserSessionState.CLOSING, BrowserSessionState.CLOSED)

    def test_cannot_go_backwards(self):
        assert not can_transition(
            BrowserSessionState.USER_CONFIRMED, BrowserSessionState.WAITING_FOR_USER
        )

    def test_terminal_no_transitions(self):
        assert not can_transition(BrowserSessionState.CLOSED, BrowserSessionState.STARTING)
        assert not can_transition(BrowserSessionState.FAILED, BrowserSessionState.STARTING)


class TestIsTerminal:
    def test_closed_is_terminal(self):
        assert BrowserSessionState.CLOSED.is_terminal()

    def test_failed_is_terminal(self):
        assert BrowserSessionState.FAILED.is_terminal()

    def test_waiting_not_terminal(self):
        assert not BrowserSessionState.WAITING_FOR_USER.is_terminal()

    def test_confirmed_not_terminal(self):
        assert not BrowserSessionState.USER_CONFIRMED.is_terminal()
