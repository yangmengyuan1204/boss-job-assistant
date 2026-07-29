"""collect_runner 命令逻辑测试。

P3：直接测试 _run_collect_loop 与 _handle_collect，注入 FakeCommandSource
与 mock BrowserManager。不启动真实 Playwright，不访问网络。

覆盖：
- 命令循环：quit / status / 空输入 / 未知命令
- _handle_collect：搜索结果页采集 / 非搜索页拒绝 / 空结果页拒绝 / 日志写入 / 去重 UPSERT
- _write_collect_log：必填字段 / 异常分支 / 追加模式
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from boss_tool.browser import FakeCommandSource
from boss_tool.browser.signals import BrowserSessionState
from boss_tool.collect_runner import (
    _handle_collect,
    _run_collect_loop,
    _write_collect_log,
)
from boss_tool.enums import StopReason
from boss_tool.models.observed_page import PageType, PageTypeDetection
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobListRepository


# ==================== 辅助函数 ====================
def _make_cmd_source(commands: list[str]) -> FakeCommandSource:
    """构造 FakeCommandSource。"""
    return FakeCommandSource(commands=commands)


def _make_session() -> MagicMock:
    """构造一个 mock session，状态非终止。"""
    session = MagicMock()
    session.state = BrowserSessionState.WAITING_FOR_USER
    session.is_terminal.return_value = False
    session.session_id = "test-collect-123"
    session.user_confirmed = False
    return session


def _make_mock_manager(
    *,
    is_running: bool = True,
    page_html: str = "<html><body><div class='search-job-result'></div></body></html>",
    page_url: str = "https://www.zhipin.com/web/geek/job",
) -> MagicMock:
    """构造 mock BrowserManager，含 _page / session / PageObserver 行为。

    注意：PageObserver 类需要在测试中通过 @patch 装饰器注入。
    本函数仅设置 manager._page 与 manager.session 的基础行为。
    """
    mock_manager = MagicMock()
    mock_manager.is_running = is_running
    mock_manager.session = MagicMock()
    mock_manager.session.state = BrowserSessionState.WAITING_FOR_USER
    mock_manager.session.is_terminal.return_value = False
    mock_manager.session.session_id = "test-collect-123"
    mock_manager.session.user_confirmed = False

    mock_page = MagicMock()
    mock_page.url = page_url
    mock_page.content.return_value = page_html
    mock_manager._page = mock_page  # noqa: SLF001

    def _close(**kwargs):
        mock_manager.is_running = False
        mock_manager.session.state = BrowserSessionState.CLOSED
        mock_manager.session.is_terminal.return_value = True

    mock_manager.close.side_effect = _close
    return mock_manager


def _load_list_page_basic_html() -> str:
    """加载 list_page_basic.html fixture 内容。"""
    fixture_path = Path(__file__).parent / "fixtures" / "pages" / "list_page_basic.html"
    return fixture_path.read_text(encoding="utf-8")


# ==================== 命令循环测试 ====================
class TestCollectLoopCommands:
    """_run_collect_loop 各命令分支测试。"""

    @patch("boss_tool.collect_runner.PageObserver")
    def test_quit_closes_session(self, mock_observer_cls):
        """quit 命令触发 close 并以 USER_ABORTED 退出。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["quit"])
        _run_collect_loop(
            mock_manager,
            session,
            db=MagicMock(),
            log_path=Path("dummy.log"),
            page_no=None,
            command_source=cmd_source,
        )
        mock_manager.close.assert_called_once()
        assert mock_manager.close.call_args.kwargs["stop_reason"] == StopReason.USER_ABORTED

    @patch("boss_tool.collect_runner.PageObserver")
    def test_status_outputs_info(self, mock_observer_cls, capsys):
        """status 命令输出 session_id、页面类型等信息。"""
        mock_observer_instance = MagicMock()
        mock_observer_instance.detect_type.return_value = PageTypeDetection(
            page_type=PageType.SEARCH_LIST, confidence=0.85
        )
        mock_observer_instance.get_current_url.return_value = "https://www.zhipin.com/web/geek/job"
        mock_observer_cls.return_value = mock_observer_instance

        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["status", "quit"])
        _run_collect_loop(
            mock_manager,
            session,
            db=MagicMock(),
            log_path=Path("dummy.log"),
            page_no=None,
            command_source=cmd_source,
        )
        captured = capsys.readouterr()
        assert "test-collect-123" in captured.out
        assert "waiting_for_user" in captured.out
        assert "search_list" in captured.out
        assert "可以执行 collect" in captured.out

    @patch("boss_tool.collect_runner.PageObserver")
    def test_empty_input_warns(self, mock_observer_cls, capsys):
        """空输入提示无效，不退出循环。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["", "quit"])
        _run_collect_loop(
            mock_manager,
            session,
            db=MagicMock(),
            log_path=Path("dummy.log"),
            page_no=None,
            command_source=cmd_source,
        )
        captured = capsys.readouterr()
        assert "空输入无效" in captured.out

    @patch("boss_tool.collect_runner.PageObserver")
    def test_unknown_command_warns(self, mock_observer_cls, capsys):
        """未知命令提示，不退出循环。"""
        mock_manager = _make_mock_manager()
        session = _make_session()
        cmd_source = _make_cmd_source(["foobar", "quit"])
        _run_collect_loop(
            mock_manager,
            session,
            db=MagicMock(),
            log_path=Path("dummy.log"),
            page_no=None,
            command_source=cmd_source,
        )
        captured = capsys.readouterr()
        assert "未知命令" in captured.out
        assert "foobar" in captured.out


# ==================== _handle_collect 测试 ====================
class TestHandleCollect:
    """_handle_collect 命令测试。"""

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_search_list_page(self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace):
        """搜索结果页采集成功：输出含 '发现职位' 与 '新增'，记录入库。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.SEARCH_LIST, confidence=0.85
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        html = _load_list_page_basic_html()
        mock_manager = _make_mock_manager(page_html=html)

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            assert "发现职位" in captured.out
            assert "新增" in captured.out
            # fixture 含 2 个岗位卡片
            assert "2" in captured.out

            # 数据库应有 2 条记录
            repo = JobListRepository(db.connection)
            assert repo.count() == 2
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_non_search_page_rejected(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """登录页拒绝采集，输出提示且不写记录。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.LOGIN, confidence=0.9
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        mock_manager = _make_mock_manager(page_html="<html></html>")

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            assert "不是职位搜索结果页" in captured.out
            # 无记录入库
            repo = JobListRepository(db.connection)
            assert repo.count() == 0
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_empty_results_page(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """空结果页拒绝采集。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.EMPTY_RESULTS, confidence=0.9
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        mock_manager = _make_mock_manager(page_html="<html></html>")

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            assert "不是职位搜索结果页" in captured.out
            repo = JobListRepository(db.connection)
            assert repo.count() == 0
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_writes_log(self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace):
        """采集成功后日志文件存在且包含关键字段。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.SEARCH_LIST, confidence=0.85
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        html = _load_list_page_basic_html()
        mock_manager = _make_mock_manager(page_html=html)

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            assert log_path.exists()
            content = log_path.read_text(encoding="utf-8")
            assert "开始时间" in content
            assert "结束时间" in content
            assert "脱敏URL" in content
            assert "页面类型" in content
            assert "search_list" in content
            assert "岗位数量" in content
            assert "新增" in content
            assert "更新" in content
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_upsert_dedup(self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace):
        """同一页面采集两次：第二次全部为更新，无新增，数据库不膨胀。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.SEARCH_LIST, confidence=0.85
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        html = _load_list_page_basic_html()
        mock_manager = _make_mock_manager(page_html=html)

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"

            # 第一次采集
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)
            first_out = capsys.readouterr().out
            assert "新增" in first_out

            repo = JobListRepository(db.connection)
            assert repo.count() == 2

            # 第二次采集同一页面
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)
            second_out = capsys.readouterr().out
            # 第二次应为全量更新，新增为 0
            assert "新增:     0" in second_out
            assert "更新:     2" in second_out

            # 数据库仍为 2 条（UPSERT 去重）
            assert repo.count() == 2
        finally:
            db.close()


# ==================== _write_collect_log 测试 ====================
class TestWriteCollectLog:
    """_write_collect_log 单元测试。"""

    def test_log_contains_required_fields(self, tmp_workspace):
        """日志包含全部必填字段。"""
        log_path = tmp_workspace / "logs" / "collect.log"
        started_at = datetime(2026, 7, 29, 10, 0, 0)
        ended_at = started_at + timedelta(seconds=2)
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=2,
            new_count=2,
            update_count=0,
        )
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "开始时间" in content
        assert "结束时间" in content
        assert "耗时" in content
        assert "脱敏URL" in content
        assert "岗位数量" in content
        assert "新增" in content
        assert "更新" in content
        assert "search_list" in content
        assert "https://www.zhipin.com/web/geek/job" in content

    def test_log_with_error(self, tmp_workspace):
        """异常分支日志包含 '异常' 与错误信息。"""
        log_path = tmp_workspace / "logs" / "collect.log"
        started_at = datetime(2026, 7, 29, 10, 0, 0)
        ended_at = started_at + timedelta(seconds=1)
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="login",
            job_count=0,
            new_count=0,
            update_count=0,
            error="test error",
        )
        content = log_path.read_text(encoding="utf-8")
        assert "异常" in content
        assert "test error" in content

    def test_log_appends(self, tmp_workspace):
        """追加模式：调用两次后文件含 2 条记录。"""
        log_path = tmp_workspace / "logs" / "collect.log"
        started_at = datetime(2026, 7, 29, 10, 0, 0)
        ended_at = started_at + timedelta(seconds=1)

        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=2,
            new_count=2,
            update_count=0,
        )
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=0,
            new_count=0,
            update_count=2,
        )
        content = log_path.read_text(encoding="utf-8")
        # "采集记录" 标题出现 2 次
        assert content.count("采集记录") == 2
