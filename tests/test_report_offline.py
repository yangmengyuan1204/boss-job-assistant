"""P7 离线阻断测试。

验证 generate-report 全程不访问网络：
- ReportRepository 不创建 socket
- HTMLRenderer 不调用 requests
- run_generate_report 不访问网络
- renderer 模块不导入 playwright
- 所有网络 API 被 monkeypatch 阻断后仍正常运行
"""

from __future__ import annotations

import ast
import inspect
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytest

from boss_tool.enums import ActivityCategory
from boss_tool.models.job_detail import JobDetailRecord
from boss_tool.models.job_list import JobListRecord
from boss_tool.rules.models import AgeStatus, RecommendLevel, RuleResult
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import (
    JobDetailRepository,
    JobListRepository,
    RuleEngineRepository,
)


class TestNoNetworkImports:
    """report 模块不导入网络相关包。"""

    def test_report_not_import_playwright(self) -> None:
        """report 模块不导入 playwright。"""
        import boss_tool.report
        import boss_tool.report.age_fit
        import boss_tool.report.constants
        import boss_tool.report.models
        import boss_tool.report.renderer
        import boss_tool.report.repository
        import boss_tool.report.runner
        import boss_tool.report.sections
        import boss_tool.report.sorting

        modules = [
            boss_tool.report,
            boss_tool.report.age_fit,
            boss_tool.report.constants,
            boss_tool.report.models,
            boss_tool.report.renderer,
            boss_tool.report.repository,
            boss_tool.report.runner,
            boss_tool.report.sections,
            boss_tool.report.sorting,
        ]

        for mod in modules:
            source = inspect.getsource(mod)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        msg = f"{mod.__name__} 导入了 playwright"
                        assert "playwright" not in alias.name.lower(), msg
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and "playwright" in node.module.lower()
                ):
                    raise AssertionError(f"{mod.__name__} 从 playwright 导入")

    def test_report_not_import_requests(self) -> None:
        """report 模块不导入 requests。"""
        import boss_tool.report.renderer
        import boss_tool.report.repository
        import boss_tool.report.runner

        modules = [
            boss_tool.report.renderer,
            boss_tool.report.repository,
            boss_tool.report.runner,
        ]

        for mod in modules:
            source = inspect.getsource(mod)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "requests" not in alias.name.lower(), (
                            f"{mod.__name__} 导入了 requests"
                        )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and "requests" in node.module.lower()
                ):
                    raise AssertionError(f"{mod.__name__} 从 requests 导入")


class TestSocketBlocked:
    """阻断 socket 后仍能正常运行。"""

    @pytest.fixture
    def db_with_data(self, tmp_db_path: Path) -> Path:
        """创建有数据的数据库。"""
        db = Database(tmp_db_path)
        db.initialize()

        list_repo = JobListRepository(db.connection)
        detail_repo = JobDetailRepository(db.connection)
        rule_repo = RuleEngineRepository(db.connection)

        list_repo.save_job_list(
            JobListRecord(
                job_id="offline-001",
                job_url="https://www.zhipin.com/job_detail/offline-001.html",
                title="小区保洁",
                salary="3000-4000元/月",
                company="示例物业",
                location="杭州·拱墅区",
                collected_at=datetime(2026, 8, 1, 10, 0, 0),
            )
        )
        detail_repo.save_job_detail(
            JobDetailRecord(
                job_id="offline-001",
                job_url="https://www.zhipin.com/job_detail/offline-001.html",
                title="小区保洁",
                salary="4000-5000元/月",
                location="杭州·拱墅区",
                description="岗位描述",
                company="示例物业",
                recruiter_active="今日活跃",
                tags=["保洁"],
                benefits=["五险一金"],
                collected_at=datetime(2026, 8, 1, 10, 0, 0),
                distance_meter=1500.0,
                within_3km=True,
            )
        )
        rule_repo.save_rule_result(
            "offline-001",
            RuleResult(
                score=90,
                recommend_level=RecommendLevel.A,
                job_category="保洁",
                age_status=AgeStatus.NO_LIMIT,
                recruiter_active_level=ActivityCategory.ACTIVE_3D,
                distance_meter=1500.0,
                matched_rules=["category:保洁"],
                failed_rules=[],
                warnings=[],
                explanations=["岗位分类为保洁，加分+30"],
                labor_intensity_tags=[],
                score_breakdown={"category": 30},
            ),
        )
        db.commit()
        db.close()
        return tmp_db_path

    def test_repository_works_with_socket_blocked(
        self,
        db_with_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ReportRepository 在 socket 被阻断时仍能查询。"""

        def _fail_socket(*args, **kwargs):
            raise AssertionError("socket.socket 不应被调用")

        monkeypatch.setattr(socket, "socket", _fail_socket)

        import sqlite3

        conn = sqlite3.connect(f"file:{db_with_data.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        from boss_tool.report.repository import ReportRepository

        repo = ReportRepository(conn)
        jobs = repo.fetch_all_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "offline-001"
        conn.close()

    def test_renderer_works_with_socket_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTMLRenderer 在 socket 被阻断时仍能渲染。"""

        def _fail_socket(*args, **kwargs):
            raise AssertionError("socket.socket 不应被调用")

        monkeypatch.setattr(socket, "socket", _fail_socket)

        from boss_tool.report.age_fit import CandidateAgeFit
        from boss_tool.report.models import (
            ReportJob,
            ReportMetadata,
            ReportSection,
            ReportSectionType,
            ReportSummary,
        )
        from boss_tool.report.renderer import HTMLRenderer

        job = ReportJob(
            job_id="test-001",
            title="测试岗位",
            candidate_age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=True,
            recommend_level="A",
            score=90,
        )
        sections = [
            ReportSection(
                section_type=ReportSectionType.STRONGLY_RECOMMEND,
                title="强烈推荐",
                color="#27ae60",
                jobs=[job],
            )
        ]
        summary = ReportSummary(total=1, strongly_recommend_count=1)
        metadata = ReportMetadata()

        renderer = HTMLRenderer()
        html = renderer.render([job], sections, summary, metadata)
        assert "<!DOCTYPE html>" in html

    def test_runner_works_with_socket_blocked(
        self,
        db_with_data: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_generate_report 在 socket 被阻断时仍能完整运行。"""

        def _fail_socket(*args, **kwargs):
            raise AssertionError("socket.socket 不应被调用")

        monkeypatch.setattr(socket, "socket", _fail_socket)

        from boss_tool.report.runner import run_generate_report

        output_path = tmp_path / "offline_report.html"
        result = run_generate_report(
            config_dir=None,
            output=output_path,
            db_path=db_with_data,
            open_browser=False,
        )
        assert result.exists()
        assert result.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


class TestRequestsBlocked:
    """阻断 requests 后仍能正常运行。"""

    def test_runner_works_without_requests(
        self,
        tmp_db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_generate_report 在 requests 被移除时仍能运行。"""
        # 初始化空数据库
        db = Database(tmp_db_path)
        db.initialize()
        db.close()

        # 移除 requests 模块
        monkeypatch.setitem(sys.modules, "requests", None)
        monkeypatch.setitem(sys.modules, "urllib.request", None)

        from boss_tool.report.runner import run_generate_report

        output_path = tmp_path / "no_requests_report.html"
        result = run_generate_report(
            config_dir=None,
            output=output_path,
            db_path=tmp_db_path,
            open_browser=False,
        )
        assert result.exists()


class TestNoWebBrowserAutoOpen:
    """不自动打开浏览器（除非显式 --open）。"""

    def test_default_no_browser_open(
        self,
        tmp_db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """默认不调用 webbrowser.open。"""
        db = Database(tmp_db_path)
        db.initialize()
        db.close()

        import webbrowser

        called = False

        def _spy_open(*args, **kwargs):
            nonlocal called
            called = True
            return True

        monkeypatch.setattr(webbrowser, "open", _spy_open)

        from boss_tool.report.runner import run_generate_report

        output_path = tmp_path / "no_browser.html"
        run_generate_report(
            config_dir=None,
            output=output_path,
            db_path=tmp_db_path,
            open_browser=False,  # 默认不打开
        )
        assert not called, "webbrowser.open 不应被调用"

    def test_explicit_open_calls_browser(
        self,
        tmp_db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """显式 --open 调用 webbrowser.open。"""
        db = Database(tmp_db_path)
        db.initialize()
        db.close()

        import webbrowser

        called = False

        def _spy_open(*args, **kwargs):
            nonlocal called
            called = True
            return True

        monkeypatch.setattr(webbrowser, "open", _spy_open)

        from boss_tool.report.runner import run_generate_report

        output_path = tmp_path / "with_browser.html"
        run_generate_report(
            config_dir=None,
            output=output_path,
            db_path=tmp_db_path,
            open_browser=True,
        )
        assert called, "webbrowser.open 应被调用"
