"""collect_runner 命令逻辑测试。

P3：直接测试 _run_collect_loop 与 _handle_collect，注入 FakeCommandSource
与 mock BrowserManager。不启动真实 Playwright，不访问网络。

P3.1 覆盖：
- 命令循环：quit / status / 空输入 / 未知命令
- _handle_collect：搜索结果页采集 / 非搜索页拒绝 / 空结果页拒绝 / 日志写入 / 三态 UPSERT
- collect_runner 调用 parse_list_page_with_diagnostics（不再调用无 diagnostics 版本）
- 异常隔离：Parser/SQLite/Diagnostics 异常后仍可继续命令循环
- 三态统计输出：新增 / 更新 / 重复（重复不再恒为 0）
- _write_collect_log：必填字段 / 异常分支 / 追加模式 / 诊断摘要
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
    _write_collect_log_safe,
)
from boss_tool.enums import StopReason
from boss_tool.models.job_list import DiagnosticsSummary
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
        """采集成功后日志文件存在且包含关键字段（含三态统计与诊断摘要）。"""
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
            # P3.1：日志包含重复统计与诊断摘要
            assert "重复" in content
            assert "diagnostics_warning_count" in content
            assert "missing_field_count" in content
            assert "selector_miss_count" in content
            assert "fallback_count" in content
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_upsert_three_state_dedup(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """同一页面采集两次：第二次全部为重复(UNCHANGED)，新增为 0，数据库不膨胀。

        P3.1：重复数量不再恒为 0。相同内容第二次采集 → UNCHANGED。
        """
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

            # 第一次采集 → 全部新增
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)
            first_out = capsys.readouterr().out
            assert "新增:     2" in first_out

            repo = JobListRepository(db.connection)
            assert repo.count() == 2

            # 第二次采集同一页面 → 全部 UNCHANGED（重复）
            _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)
            second_out = capsys.readouterr().out
            assert "新增:     0" in second_out
            assert "重复:     2" in second_out

            # 数据库仍为 2 条（UPSERT 去重）
            assert repo.count() == 2
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_uses_diagnostics_parser(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """collect_runner 调用 parse_list_page_with_diagnostics，不再调用无 diagnostics 版本。"""
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
            with patch(
                "boss_tool.collect_runner.parse_list_page_with_diagnostics",
                wraps=__import__(
                    "boss_tool.parsers.list_page", fromlist=["parse_list_page_with_diagnostics"]
                ).parse_list_page_with_diagnostics,
            ) as spy:
                _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)
                assert spy.called

            captured = capsys.readouterr()
            # 诊断输出存在
            assert "诊断警告" in captured.out
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_outputs_three_state_counts(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """采集输出包含新增/更新/重复三态数量。"""
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
            assert "新增:" in captured.out
            assert "更新:" in captured.out
            assert "重复:" in captured.out
            assert "数据库:   job_list" in captured.out
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_parser_exception_does_not_crash(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """Parser 异常后输出错误类型，仍可继续命令循环。"""
        mock_observer_cls.return_value.detect_type.return_value = PageTypeDetection(
            page_type=PageType.SEARCH_LIST, confidence=0.85
        )
        mock_observer_cls.return_value.get_current_url.return_value = (
            "https://www.zhipin.com/web/geek/job"
        )

        mock_manager = _make_mock_manager(page_html="<html></html>")

        db = Database(tmp_db_path)
        db.initialize()
        try:
            log_path = tmp_workspace / "logs" / "collect.log"
            with patch(
                "boss_tool.collect_runner.parse_list_page_with_diagnostics",
                side_effect=RuntimeError("parser boom with <script>secret</script>"),
            ):
                _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            # 错误类型输出到 stderr（err=True），不输出异常原文（含敏感内容）
            assert "解析异常" in captured.err
            assert "secret" not in captured.err
            assert "secret" not in captured.out
            assert "RuntimeError" in captured.err
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_sqlite_exception_does_not_crash(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """SQLite 异常后输出错误类型，仍可继续命令循环。"""
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
            # Mock db.transaction 抛异常
            mock_db = MagicMock()
            mock_db.transaction.side_effect = RuntimeError("disk I/O error")
            _handle_collect(mock_manager, db=mock_db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            assert "数据库写入异常" in captured.err
        finally:
            db.close()

    @patch("boss_tool.collect_runner.PageObserver")
    def test_collect_diagnostics_exception_does_not_crash(
        self, mock_observer_cls, capsys, tmp_db_path, tmp_workspace
    ):
        """Diagnostics 摘要构建异常后不崩溃，使用空摘要继续。"""
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
            with patch(
                "boss_tool.collect_runner.build_diagnostics_summary",
                side_effect=RuntimeError("diag boom"),
            ):
                _handle_collect(mock_manager, db=db, log_path=log_path, page_no=1)

            captured = capsys.readouterr()
            # 诊断摘要异常输出到 stderr；采集仍继续
            assert "诊断摘要异常" in captured.err
            assert "新增:" in captured.out
        finally:
            db.close()


# ==================== _write_collect_log 测试 ====================
class TestWriteCollectLog:
    """_write_collect_log 单元测试（P3.1 三态统计 + 诊断摘要）。"""

    def test_log_contains_required_fields(self, tmp_workspace):
        """日志包含全部必填字段（含三态统计与诊断摘要）。"""
        log_path = tmp_workspace / "logs" / "collect.log"
        started_at = datetime(2026, 7, 29, 10, 0, 0)
        ended_at = started_at + timedelta(seconds=2)
        diag = DiagnosticsSummary(
            card_count=2,
            warning_count=1,
            missing_required_fields=["education_text"],
            missing_field_counts={"salary_text": 1},
            selector_miss_count=1,
            fallback_count=0,
        )
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=2,
            new_count=2,
            updated_count=0,
            unchanged_count=0,
            diag_summary=diag,
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
        assert "重复" in content
        assert "search_list" in content
        assert "https://www.zhipin.com/web/geek/job" in content
        # 诊断摘要字段
        assert "diagnostics_warning_count" in content
        assert "missing_field_count" in content
        assert "selector_miss_count" in content
        assert "fallback_count" in content
        # 诊断摘要只含字段名与计数，不含页面原文
        assert "education_text=all_missing" in content
        assert "salary_text=1" in content

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
            updated_count=0,
            unchanged_count=0,
        )
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=0,
            new_count=0,
            updated_count=0,
            unchanged_count=2,
        )
        content = log_path.read_text(encoding="utf-8")
        # "采集记录" 标题出现 2 次
        assert content.count("采集记录") == 2

    def test_log_safe_does_not_leak_sensitive(self, tmp_workspace):
        """_write_collect_log_safe 不输出异常消息原文（可能含敏感内容）。"""
        log_path = tmp_workspace / "logs" / "collect.log"
        started_at = datetime(2026, 7, 29, 10, 0, 0)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url="https://www.zhipin.com/web/geek/job",
            page_type="search_list",
            job_count=0,
            error_type="解析器异常",
        )
        content = log_path.read_text(encoding="utf-8")
        assert "异常类型" in content
        assert "解析器异常" in content
        # 不含敏感原文（error_detail 未传入）
        assert "Cookie" not in content
        assert "SecurityId" not in content
