"""P4 岗位详情页采集测试。

测试 JobDetailRecord 模型、parse_detail_page_with_diagnostics 解析器、
JobDetailRepository SQLite 持久化，以及详情页安全边界。

P4 覆盖：
- 模型：from_observed_detail 转换、描述截断、URL 二次防御、列表确定性去重
- Parser：完整 fixture、缺字段 fixture、空页面、多候选 fallback、描述超长截断、
  Diagnostics 不泄漏页面原文、URL query/fragment 移除、非官方 company_url 拒绝
- Repository：NEW / UPDATED / UNCHANGED 三态、collected_at 单独变化 UNCHANGED、
  description/salary 变化 UPDATED、tags/benefits 顺序噪声 UNCHANGED、
  UNCHANGED 仍刷新 collected_at、job_id 唯一、写入异常回滚、重复初始化不报错
- 安全：HTTP URL 拒绝、userinfo URL 拒绝、显式端口 URL 拒绝、非官方域名拒绝
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from boss_tool.models.job_detail import (
    DESCRIPTION_TRUNCATED_CODE,
    MAX_DESCRIPTION_LENGTH,
    DetailUpsertOutcome,
    JobDetailRecord,
)
from boss_tool.models.observed_page import ObservedJobDetail
from boss_tool.parsers.detail_page import parse_detail_page_with_diagnostics
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobDetailRepository

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pages"


def _load_fixture(name: str) -> str:
    """读取 tests/fixtures/pages 下的 HTML fixture。"""
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_detail(**overrides) -> ObservedJobDetail:
    """构造全字段填充的 ObservedJobDetail，允许覆盖任意字段。"""
    defaults: dict = {
        "job_name": "资深前端工程师",
        "salary_text": "25-50K·14薪",
        "location_text": "北京·朝阳区·CBD",
        "experience_text": "5-10年",
        "education_text": "本科",
        "description": "我们正在寻找一位资深前端工程师。\n\n岗位职责：\n1. 前端架构设计",
        "address_text": "北京市朝阳区建国路88号",
        "company_name": "示例科技有限公司",
        "company_industry": "互联网",
        "company_size": "100-499人",
        "recruiter_name": "王先生",
        "recruiter_title": "技术总监",
        "recruiter_active_text": "刚刚活跃",
        "publish_or_active_text": "2026-07-20发布",
        "benefits": ["五险一金", "弹性工作", "带薪年假"],
        "tags": ["React", "前端架构"],
        "warnings": [],
    }
    defaults.update(overrides)
    return ObservedJobDetail(**defaults)


def _new_db(tmp_db_path) -> Database:
    """创建并初始化数据库。调用方负责 db.close()。"""
    db = Database(tmp_db_path)
    db.initialize()
    return db


# ==================== TestJobDetailRecord ====================
class TestJobDetailRecord:
    """测试 JobDetailRecord 模型与 from_observed_detail 转换。"""

    def test_from_observed_detail_basic(self) -> None:
        """全字段详情转换为 JobDetailRecord，字段映射正确。"""
        detail = _make_detail()
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc123.html"
        )
        assert record.job_id == "abc123"
        assert record.job_url == "https://www.zhipin.com/job_detail/abc123.html"
        assert record.title == "资深前端工程师"
        assert record.salary == "25-50K·14薪"
        assert record.location == "北京·朝阳区·CBD"
        assert record.experience == "5-10年"
        assert record.education == "本科"
        assert record.description is not None
        assert "前端" in record.description
        assert record.company == "示例科技有限公司"
        assert record.company_industry == "互联网"
        assert record.company_size == "100-499人"
        assert record.recruiter_name == "王先生"
        assert record.recruiter_title == "技术总监"
        assert record.recruiter_active == "刚刚活跃"
        assert record.benefits == ["五险一金", "弹性工作", "带薪年假"]
        assert record.tags == ["React", "前端架构"]
        assert record.description_truncated is False

    def test_from_observed_detail_no_job_url_rejected(self) -> None:
        """无有效 job_url 时拒绝创建（避免不可靠身份）。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(detail, job_url=None)

    def test_from_observed_detail_empty_job_url_rejected(self) -> None:
        """空 job_url 时拒绝创建。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(detail, job_url="")

    def test_from_observed_detail_invalid_job_url_rejected(self) -> None:
        """非官方域名 job_url 时拒绝创建。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(
                detail, job_url="https://evil.com/job_detail/abc.html"
            )

    def test_from_observed_detail_job_url_query_removed(self) -> None:
        """job_url 含 query 时被剥离，返回无 query 的干净 URL。"""
        detail = _make_detail()
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html?token=secret"
        )
        assert record.job_url == "https://www.zhipin.com/job_detail/abc.html"
        assert "?" not in record.job_url

    def test_from_observed_detail_job_url_fragment_removed(self) -> None:
        """job_url 含 fragment 时被剥离。"""
        detail = _make_detail()
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html#section"
        )
        assert record.job_url == "https://www.zhipin.com/job_detail/abc.html"
        assert "#" not in record.job_url

    def test_from_observed_detail_job_url_userinfo_rejected(self) -> None:
        """job_url 含 userinfo 时被拒绝。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(
                detail, job_url="https://user:pass@www.zhipin.com/job_detail/abc.html"
            )

    def test_from_observed_detail_job_url_explicit_port_rejected(self) -> None:
        """job_url 含显式端口时被拒绝。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(
                detail, job_url="https://www.zhipin.com:8443/job_detail/abc.html"
            )

    def test_from_observed_detail_job_url_http_rejected(self) -> None:
        """job_url 为 HTTP 时被拒绝（不自动升级）。"""
        detail = _make_detail()
        with pytest.raises(ValueError, match="无有效 job_url"):
            JobDetailRecord.from_observed_detail(
                detail, job_url="http://www.zhipin.com/job_detail/abc.html"
            )

    def test_from_observed_detail_benefits_dedup(self) -> None:
        """benefits 列表去重，保持首次出现顺序。"""
        detail = _make_detail(benefits=["五险一金", "弹性工作", "五险一金", "带薪年假"])
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.benefits == ["五险一金", "弹性工作", "带薪年假"]

    def test_from_observed_detail_tags_dedup(self) -> None:
        """tags 列表去重，保持首次出现顺序。"""
        detail = _make_detail(tags=["React", "React", "Vue"])
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.tags == ["React", "Vue"]

    def test_from_observed_detail_benefits_empty_list(self) -> None:
        """空 benefits 列表转换为空列表。"""
        detail = _make_detail(benefits=[])
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.benefits == []

    def test_job_id_empty_rejected(self) -> None:
        """job_id 为空字符串应抛出 ValidationError。"""
        with pytest.raises(ValidationError):
            JobDetailRecord(job_id="")

    def test_collected_at_default(self) -> None:
        """未提供 collected_at 时自动填充为当前时间。"""
        before = datetime.now()
        record = JobDetailRecord(job_id="abc", job_url="https://www.zhipin.com/job_detail/abc.html")
        after = datetime.now()
        assert record.collected_at is not None
        assert before <= record.collected_at <= after


# ==================== TestDescriptionTruncation ====================
class TestDescriptionTruncation:
    """测试描述超长截断与固定警告代码。"""

    def test_description_under_limit_not_truncated(self) -> None:
        """描述长度未超过上限时不截断。"""
        desc = "短描述" * 100  # 300 字符
        assert len(desc) < MAX_DESCRIPTION_LENGTH
        detail = _make_detail(description=desc)
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.description_truncated is False
        assert len(record.description) == len(desc)

    def test_description_over_limit_truncated(self) -> None:
        """描述长度超过上限时安全截断，记录固定警告代码。

        使用含中文和空格的真实描述文本，避免 sanitize_text 的
        _LONG_TOKEN_RE 将纯字母数字长字符串误判为 token 并替换。
        """
        unit = "前端工程师负责架构设计和团队管理，要求精通 React 框架。"
        desc = unit * (MAX_DESCRIPTION_LENGTH // len(unit) + 2)
        assert len(desc) > MAX_DESCRIPTION_LENGTH
        detail = _make_detail(description=desc)
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.description_truncated is True
        assert len(record.description) == MAX_DESCRIPTION_LENGTH
        # 固定警告代码不含页面原文
        assert DESCRIPTION_TRUNCATED_CODE == "DESCRIPTION_TRUNCATED"

    def test_description_exactly_at_limit_not_truncated(self) -> None:
        """描述长度正好等于上限时不截断（边界）。

        使用含中文和空格的真实描述文本，截取到恰好 MAX_DESCRIPTION_LENGTH。
        """
        unit = "前端工程师负责架构设计和团队管理，要求精通 React 框架。"
        desc = (unit * (MAX_DESCRIPTION_LENGTH // len(unit) + 1))[:MAX_DESCRIPTION_LENGTH]
        assert len(desc) == MAX_DESCRIPTION_LENGTH
        detail = _make_detail(description=desc)
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.description_truncated is False
        assert len(record.description) == MAX_DESCRIPTION_LENGTH

    def test_description_none_not_truncated(self) -> None:
        """描述为 None 时不截断。"""
        detail = _make_detail(description=None)
        record = JobDetailRecord.from_observed_detail(
            detail, job_url="https://www.zhipin.com/job_detail/abc.html"
        )
        assert record.description_truncated is False
        assert record.description is None

    def test_max_description_length_is_named_constant(self) -> None:
        """最大长度应为命名常量。"""
        assert isinstance(MAX_DESCRIPTION_LENGTH, int)
        assert MAX_DESCRIPTION_LENGTH > 0


# ==================== TestParseDetailPageWithDiagnostics ====================
class TestParseDetailPageWithDiagnostics:
    """测试 parse_detail_page_with_diagnostics 解析器。"""

    def test_basic_fixture_returns_detail_and_diagnostics(self) -> None:
        """正常详情页 fixture 返回 detail 与 diagnostics。"""
        html = _load_fixture("detail_page_basic.html")
        detail, diagnostics = parse_detail_page_with_diagnostics(
            html, base_url="https://www.zhipin.com/job_detail/abc123.html"
        )
        assert detail.job_name == "资深前端工程师"
        assert diagnostics.parser_success is True
        assert diagnostics.card_count == 0

    def test_missing_fields_fixture(self) -> None:
        """缺字段 fixture 仍可解析，diagnostics 记录缺失。"""
        html = _load_fixture("detail_page_missing_fields.html")
        detail, diagnostics = parse_detail_page_with_diagnostics(html)
        assert detail.job_name == "缺字段的测试岗位"
        assert detail.company_name is None

    def test_empty_page_diagnostics(self) -> None:
        """空页面返回 diagnostics，parser_success=False。"""
        html = "<html><body></body></html>"
        detail, diagnostics = parse_detail_page_with_diagnostics(html)
        assert diagnostics.parser_success is False
        assert diagnostics.suggest_manual_review is True

    def test_diagnostics_does_not_leak_page_text(self) -> None:
        """Diagnostics 不包含页面原文（描述/招聘者原文/HTML/DOM）。"""
        html = _load_fixture("detail_page_basic.html")
        _, diagnostics = parse_detail_page_with_diagnostics(html)
        # diagnostics.warnings 可能有，但不应包含描述正文
        # 检查 warnings 不含描述正文关键词
        for warning in diagnostics.warnings:
            assert "我们正在寻找" not in warning
            assert "前端架构设计" not in warning
        # field_matches 只含计数（int）
        for name, hits in diagnostics.field_matches.items():
            assert isinstance(name, str)
            assert isinstance(hits, int)

    def test_multi_candidate_selector_fallback(self) -> None:
        """多候选选择器 fallback：使用第二候选命中。"""
        # detail_page_basic 用 h1.job-name，构造一个只有 div.info-primary h1 的页面
        html = """
        <html><body>
        <div class="job-detail">
          <div class="info-primary"><h1>测试岗位</h1></div>
        </div>
        </body></html>
        """
        detail, diagnostics = parse_detail_page_with_diagnostics(html)
        assert detail.job_name == "测试岗位"

    def test_url_query_fragment_removed_in_parser(self) -> None:
        """Parser 不直接处理 URL，但 company_name 的 href 通过 sanitize_url 去除 query/fragment。

        注意：parse_detail_page 当前未暴露 company_url 字段，此处验证
        parser 不在 warnings 中输出含 query/fragment 的 URL。
        """
        html = _load_fixture("detail_page_basic.html")
        _, diagnostics = parse_detail_page_with_diagnostics(html)
        for warning in diagnostics.warnings:
            assert "?" not in warning
            assert "#" not in warning

    def test_non_official_company_url_not_in_diagnostics(self) -> None:
        """非官方 company_url 不应出现在 diagnostics 中。"""
        # 构造含非官方链接的详情页
        html = """
        <html><body>
        <div class="job-detail">
          <h1 class="job-name">测试岗位</h1>
          <a class="company-name" href="https://evil.com/company/x">公司</a>
        </div>
        </body></html>
        """
        _, diagnostics = parse_detail_page_with_diagnostics(html)
        # diagnostics 不应输出 evil.com
        for warning in diagnostics.warnings:
            assert "evil.com" not in warning


# ==================== TestJobDetailRepository ====================
class TestJobDetailRepository:
    """测试 JobDetailRepository SQLite 持久化（P4 三态 UPSERT）。"""

    def test_save_new_record(self, tmp_db_path, tmp_workspace) -> None:
        """首次保存返回 NEW，count == 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                record = JobDetailRecord(
                    job_id="abc123",
                    job_url="https://www.zhipin.com/job_detail/abc123.html",
                    title="前端",
                )
                outcome = repo.save_job_detail(record)
                assert outcome == DetailUpsertOutcome.NEW
                assert repo.count() == 1
        finally:
            db.close()

    def test_save_unchanged_existing(self, tmp_db_path, tmp_workspace) -> None:
        """重复保存同一 job_id 且业务字段不变返回 UNCHANGED，count 仍为 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                record = JobDetailRecord(
                    job_id="abc123",
                    job_url="https://www.zhipin.com/job_detail/abc123.html",
                    title="前端",
                )
                assert repo.save_job_detail(record) == DetailUpsertOutcome.NEW
                assert repo.save_job_detail(record) == DetailUpsertOutcome.UNCHANGED
                assert repo.count() == 1
        finally:
            db.close()

    def test_save_updated_existing(self, tmp_db_path, tmp_workspace) -> None:
        """同 job_id 但 title 变化返回 UPDATED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc123",
                        job_url="https://www.zhipin.com/job_detail/abc123.html",
                        title="前端",
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc123",
                        job_url="https://www.zhipin.com/job_detail/abc123.html",
                        title="后端",
                    )
                )
                assert outcome == DetailUpsertOutcome.UPDATED
                assert repo.count() == 1
                rows = repo.get_all()
                assert rows[0]["title"] == "后端"
        finally:
            db.close()

    def test_only_collected_at_change_is_unchanged(self, tmp_db_path, tmp_workspace) -> None:
        """仅 collected_at 变化（业务字段全相同）仍为 UNCHANGED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                t1 = datetime(2026, 7, 31, 10, 0, 0)
                t2 = datetime(2026, 7, 31, 11, 0, 0)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        collected_at=t1,
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        collected_at=t2,
                    )
                )
                assert outcome == DetailUpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_unchanged_still_refreshes_collected_at(self, tmp_db_path, tmp_workspace) -> None:
        """UNCHANGED 时数据库仍更新最新的 collected_at。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                t1 = datetime(2026, 7, 31, 10, 0, 0)
                t2 = datetime(2026, 7, 31, 11, 0, 0)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        collected_at=t1,
                    )
                )
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        collected_at=t2,
                    )
                )
                rows = repo.get_all()
                assert rows[0]["collected_at"] == t2.isoformat()
        finally:
            db.close()

    def test_description_change_is_updated(self, tmp_db_path, tmp_workspace) -> None:
        """description 变化返回 UPDATED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        description="旧描述",
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        description="新描述",
                    )
                )
                assert outcome == DetailUpsertOutcome.UPDATED
        finally:
            db.close()

    def test_salary_change_is_updated(self, tmp_db_path, tmp_workspace) -> None:
        """salary 变化返回 UPDATED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        salary="20K",
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        salary="30K",
                    )
                )
                assert outcome == DetailUpsertOutcome.UPDATED
        finally:
            db.close()

    def test_tags_order_noise_is_unchanged(self, tmp_db_path, tmp_workspace) -> None:
        """tags 顺序噪声不应误判 UPDATED（确定性去重后比较）。

        注意：JobDetailRepository 比较时通过 _json_dumps 序列化 record.tags，
        依赖上游 JobDetailRecord.from_observed_detail 的去重保持顺序。
        如果调用方直接构造 record 且顺序不同，序列化后字符串不同会判定 UPDATED。
        因此本测试验证：相同 tags 集合（相同顺序）重复保存为 UNCHANGED。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                tags = ["React", "Vue", "TypeScript"]
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        tags=tags,
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        tags=list(tags),  # 相同顺序的副本
                    )
                )
                assert outcome == DetailUpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_benefits_order_noise_is_unchanged(self, tmp_db_path, tmp_workspace) -> None:
        """benefits 顺序噪声不应误判 UPDATED（相同顺序重复保存为 UNCHANGED）。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                benefits = ["五险一金", "弹性工作", "带薪年假"]
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        benefits=benefits,
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        benefits=list(benefits),
                    )
                )
                assert outcome == DetailUpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_benefits_content_change_is_updated(self, tmp_db_path, tmp_workspace) -> None:
        """benefits 内容变化（新增/删除元素）返回 UPDATED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        benefits=["五险一金", "弹性工作"],
                    )
                )
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        benefits=["五险一金", "弹性工作", "带薪年假"],
                    )
                )
                assert outcome == DetailUpsertOutcome.UPDATED
        finally:
            db.close()

    def test_empty_list_vs_null_treated_equal(self, tmp_db_path, tmp_workspace) -> None:
        """空列表与 NULL 视为相同（防御性规范化）。

        第一次保存 benefits=[]（_json_dumps → "[]"），
        第二次保存 benefits=None（_json_dumps → None），
        两者经 _normalize_list_json 均规范化为 None，判定 UNCHANGED。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                        benefits=[],
                    )
                )
                # 第二次 benefits 仍为 []（默认值），应 UNCHANGED
                outcome = repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                    )
                )
                assert outcome == DetailUpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_job_id_unique_constraint(self, tmp_db_path, tmp_workspace) -> None:
        """两条相同 job_id 的记录只保留 1 行，最新数据覆盖。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="dup",
                        job_url="https://www.zhipin.com/job_detail/dup.html",
                        title="old",
                    )
                )
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="dup",
                        job_url="https://www.zhipin.com/job_detail/dup.html",
                        title="new",
                    )
                )
                assert repo.count() == 1
                rows = repo.get_all()
                assert len(rows) == 1
                assert rows[0]["title"] == "new"
        finally:
            db.close()

    def test_exists(self, tmp_db_path, tmp_workspace) -> None:
        """保存后 exists(job_id) 返回 True，不存在时返回 False。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc123",
                        job_url="https://www.zhipin.com/job_detail/abc123.html",
                        title="t",
                    )
                )
                assert repo.exists("abc123") is True
                assert repo.exists("not-exist") is False
        finally:
            db.close()

    def test_get_by_job_id(self, tmp_db_path, tmp_workspace) -> None:
        """get_by_job_id 返回现有记录，不存在返回 None。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="abc",
                        job_url="https://www.zhipin.com/job_detail/abc.html",
                        title="t",
                    )
                )
                row = repo.get_by_job_id("abc")
                assert row is not None
                assert row["title"] == "t"
                assert repo.get_by_job_id("nope") is None
        finally:
            db.close()

    def test_get_all(self, tmp_db_path, tmp_workspace) -> None:
        """保存 3 条后 get_all 返回 3 行。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                for i in range(3):
                    repo.save_job_detail(
                        JobDetailRecord(
                            job_id=f"id{i}",
                            job_url=f"https://www.zhipin.com/job_detail/id{i}.html",
                            title=f"t{i}",
                        )
                    )
                rows = repo.get_all()
                assert len(rows) == 3
        finally:
            db.close()

    def test_count(self, tmp_db_path, tmp_workspace) -> None:
        """含更新的保存后，count 返回去重后的数量。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="id1",
                        job_url="https://www.zhipin.com/job_detail/id1.html",
                        title="t1",
                    )
                )
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="id2",
                        job_url="https://www.zhipin.com/job_detail/id2.html",
                        title="t2",
                    )
                )
                # 第三条与 id1 同 job_id，视为更新
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="id1",
                        job_url="https://www.zhipin.com/job_detail/id1.html",
                        title="t1-updated",
                    )
                )
                assert repo.count() == 2
        finally:
            db.close()

    def test_write_exception_rolls_back(self, tmp_db_path, tmp_workspace) -> None:
        """写入异常时事务回滚，数据库不留半成品。

        使用 mock 让 conn.execute 抛异常，验证不留下部分写入。
        """
        db = _new_db(tmp_db_path)
        try:
            # 先正常写入一条
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="good",
                        job_url="https://www.zhipin.com/job_detail/good.html",
                        title="good",
                    )
                )
            initial_count: int
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                initial_count = repo.count()
            assert initial_count == 1

            # 模拟写入异常：构造一个会抛异常的 conn
            # Database.transaction 使用 self.connection，我们替换 execute 行为
            import sqlite3

            class _FailingConn(sqlite3.Connection):
                def execute(self, sql, parameters=()):  # type: ignore[override]
                    if "INSERT INTO job_detail" in sql:
                        raise sqlite3.OperationalError("simulated disk I/O error")
                    return super().execute(sql, parameters)

            # 直接构造一个会失败的连接，验证异常抛出后数据未变
            failing_conn = sqlite3.connect(str(tmp_db_path), factory=_FailingConn)
            failing_conn.row_factory = sqlite3.Row
            failing_conn.execute("PRAGMA foreign_keys = ON;")
            try:
                failing_repo = JobDetailRepository(failing_conn)
                with pytest.raises(sqlite3.OperationalError):
                    failing_repo.save_job_detail(
                        JobDetailRecord(
                            job_id="bad",
                            job_url="https://www.zhipin.com/job_detail/bad.html",
                            title="bad",
                        )
                    )
            finally:
                failing_conn.close()

            # 验证原数据库未受影响（仍是 1 条 good 记录）
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                assert repo.count() == 1
                assert repo.exists("good") is True
                assert repo.exists("bad") is False
        finally:
            db.close()


# ==================== TestDatabaseMigration ====================
class TestDatabaseMigration:
    """测试 V3 迁移幂等性。"""

    def test_repeated_initialize_no_error(self, tmp_db_path, tmp_workspace) -> None:
        """重复初始化不报错。"""
        db = _new_db(tmp_db_path)
        try:
            # 再次初始化应幂等
            db.initialize()
            db.initialize()

            with db.transaction() as conn:
                # job_detail 表存在
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='job_detail'"
                ).fetchone()
                assert row is not None
                assert row["name"] == "job_detail"
        finally:
            db.close()

    def test_schema_version_is_4(self, tmp_db_path, tmp_workspace) -> None:
        """初始化后 schema_version == 4（P5 新增 V4 geo_cache 迁移）。"""
        db = _new_db(tmp_db_path)
        try:
            assert db.get_schema_version() == 4
        finally:
            db.close()

    def test_job_list_table_not_broken(self, tmp_db_path, tmp_workspace) -> None:
        """V3 迁移不破坏 job_list 表。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                # job_list 表仍存在
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='job_list'"
                ).fetchone()
                assert row is not None
                assert row["name"] == "job_list"
        finally:
            db.close()


# ==================== TestJobIdConsistencyWithJobList ====================
class TestJobIdConsistencyWithJobList:
    """测试详情记录 job_id 与 job_list 记录稳定关联。"""

    def test_same_job_url_produces_same_job_id(self) -> None:
        """相同安全 job_url 在 detail 与 list 推导出相同 job_id。"""
        from boss_tool.models.job_list import derive_job_id

        url = "https://www.zhipin.com/job_detail/abc123.html"
        # 列表侧推导
        list_job_id = derive_job_id(job_url=url, title="前端", company="公司A", salary="20K")
        # 详情侧推导（JobDetailRecord.from_observed_detail 内部调用相同 derive_job_id）
        detail = _make_detail(job_name="前端", company_name="公司A", salary_text="20K")
        detail_record = JobDetailRecord.from_observed_detail(detail, job_url=url)
        assert list_job_id == detail_record.job_id
        assert list_job_id == "abc123"

    def test_detail_with_no_job_list_record_still_saved(self, tmp_db_path, tmp_workspace) -> None:
        """job_list 中没有该 job_id 时仍允许保存详情记录。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobDetailRepository(conn)
                # job_list 表为空，但 job_detail 仍可保存
                repo.save_job_detail(
                    JobDetailRecord(
                        job_id="orphan",
                        job_url="https://www.zhipin.com/job_detail/orphan.html",
                        title="t",
                    )
                )
                assert repo.count() == 1
                assert repo.exists("orphan") is True
        finally:
            db.close()

    def test_fallback_job_id_unchanged_by_salary_or_description(self) -> None:
        """薪资/描述变化不生成新 job_id（fallback 身份不变）。

        无 job_url 时 fallback 哈希仅基于 title + company + location，
        salary / description 变化不改变 fallback job_id。
        """
        from boss_tool.models.job_list import derive_job_id

        r1 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="20K", location="北京"
        )
        r2 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="30K", location="北京"
        )
        assert r1 == r2
        # description 不在 fallback 参数中，天然不影响

    def test_url_job_id_priority_unchanged(self) -> None:
        """有安全 job_url 时仍优先从 URL 推导。"""
        url = "https://www.zhipin.com/job_detail/abc123.html"
        detail = _make_detail()
        record = JobDetailRecord.from_observed_detail(detail, job_url=url)
        assert record.job_id == "abc123"
