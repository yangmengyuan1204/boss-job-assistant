"""P3 搜索结果列表采集测试。

测试 JobListRecord 模型、derive_job_id 推导函数、JobListRepository SQLite 持久化，
以及 parse_list_page → JobListRecord 的集成转换。

P3.1：
- 三态 UPSERT 测试（NEW / UPDATED / UNCHANGED）
- URL 二次防御校验测试（from_observed_card 边界再次 sanitize_url）
- 同批次重复 job_id 测试
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from boss_tool.models.job_list import (
    DiagnosticsSummary,
    JobListRecord,
    UpsertOutcome,
    build_diagnostics_summary,
    derive_job_id,
)
from boss_tool.models.observed_page import ObservedJobCard, PageType, ParseDiagnostics
from boss_tool.parsers.list_page import parse_list_page, parse_list_page_with_diagnostics
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobListRepository

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pages"
_BASE_URL = "https://www.zhipin.com/"


def _load_fixture(name: str) -> str:
    """读取 tests/fixtures/pages 下的 HTML fixture。"""
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_card(**overrides) -> ObservedJobCard:
    """构造全字段填充的 ObservedJobCard，允许覆盖任意字段。"""
    defaults: dict = {
        "source_index": 0,
        "job_name": "前端工程师",
        "job_url": "https://www.zhipin.com/job_detail/abc123.html",
        "salary_text": "25K",
        "area_text": "北京·朝阳区",
        "experience_text": "3-5年",
        "education_text": "本科",
        "company_name": "示例科技公司",
        "company_url": "https://www.zhipin.com/company/def456.html",
        "company_industry": "互联网",
        "company_size": "100-499人",
        "recruiter_name": "张先生",
        "recruiter_title": "HR",
        "recruiter_active_text": "刚刚活跃",
        "benefits": ["五险一金"],
        "tags": ["React"],
        "warnings": [],
    }
    defaults.update(overrides)
    return ObservedJobCard(**defaults)


def _new_db(tmp_db_path) -> Database:
    """创建并初始化数据库。调用方负责 db.close()。"""
    db = Database(tmp_db_path)
    db.initialize()
    return db


# ==================== TestDeriveJobId ====================
class TestDeriveJobId:
    """测试 derive_job_id 推导逻辑。"""

    def test_derive_from_url(self) -> None:
        """有 job_url 且带 .html 后缀时，提取路径末段去后缀。"""
        result = derive_job_id(
            job_url="https://www.zhipin.com/job_detail/abc123.html",
            title="前端",
            company="科技公司",
            salary="25K",
        )
        assert result == "abc123"

    def test_derive_from_url_no_extension(self) -> None:
        """有 job_url 但无后缀时，直接返回路径末段。"""
        result = derive_job_id(
            job_url="https://www.zhipin.com/job_detail/xyz",
            title="前端",
            company="科技公司",
            salary="25K",
        )
        assert result == "xyz"

    def test_derive_from_url_htm(self) -> None:
        """有 job_url 且带 .htm 后缀时，去除 .htm 后缀。"""
        result = derive_job_id(
            job_url="https://www.zhipin.com/job_detail/abc.htm",
            title="前端",
            company="科技公司",
            salary="25K",
        )
        assert result == "abc"

    def test_derive_no_url_uses_hash(self) -> None:
        """job_url=None 时回退到 hash 前缀。"""
        result = derive_job_id(job_url=None, title="前端", company="科技公司", salary="25K")
        assert result.startswith("hash:")

    def test_derive_no_url_stable_hash(self) -> None:
        """相同输入产生相同 hash。"""
        r1 = derive_job_id(job_url=None, title="前端", company="科技公司", salary="25K")
        r2 = derive_job_id(job_url=None, title="前端", company="科技公司", salary="25K")
        assert r1 == r2

    def test_derive_no_url_different_inputs_different_hash(self) -> None:
        """不同 title 产生不同 hash。"""
        r1 = derive_job_id(job_url=None, title="前端", company="科技公司", salary="25K")
        r2 = derive_job_id(job_url=None, title="后端", company="科技公司", salary="25K")
        assert r1 != r2

    def test_derive_empty_url_uses_hash(self) -> None:
        """job_url='' 视为空，回退到 hash。"""
        result = derive_job_id(job_url="", title="前端", company="科技公司", salary="25K")
        assert result.startswith("hash:")

    def test_derive_url_root_path(self) -> None:
        """job_url 仅有根路径时无路径末段，回退到 hash。"""
        result = derive_job_id(
            job_url="https://www.zhipin.com/",
            title="前端",
            company="科技公司",
            salary="25K",
        )
        assert result.startswith("hash:")

    # ==================== P3.2 fallback job_id 稳定性测试 ====================
    def test_fallback_job_id_ignores_salary(self) -> None:
        """P3.2：fallback job_id 不包含 salary。

        salary 变化不得改变 fallback job_id（工资调整不应改变岗位身份）。
        """
        r1 = derive_job_id(job_url=None, title="前端", company="公司A", salary="20K")
        r2 = derive_job_id(job_url=None, title="前端", company="公司A", salary="30K")
        assert r1 == r2

    def test_fallback_job_id_ignores_page_no(self) -> None:
        """P3.2：fallback job_id 不包含 page_no（采集元数据）。"""
        # page_no 不作为 derive_job_id 参数，验证不参与
        r1 = derive_job_id(job_url=None, title="前端", company="公司A", salary="20K")
        r2 = derive_job_id(job_url=None, title="前端", company="公司A", salary="20K")
        assert r1 == r2  # 相同输入必相同

    def test_fallback_job_id_normalizes_whitespace(self) -> None:
        """P3.2：文本规范化——strip + 连续空白折叠。"""
        r1 = derive_job_id(job_url=None, title="前端", company="公司A", salary="20K")
        r2 = derive_job_id(job_url=None, title="  前端  ", company="公司A", salary="20K")
        assert r1 == r2
        # 连续空白折叠：title 内部多空格/tab 折叠为单空格
        r3 = derive_job_id(job_url=None, title="前端\t工程师", company="公司A", salary="20K")
        r4 = derive_job_id(job_url=None, title="前端  工程师", company="公司A", salary="20K")
        assert r3 == r4

    def test_fallback_job_id_same_identity_is_stable(self) -> None:
        """P3.2：相同 title + company + location 产生稳定相同的 fallback job_id。"""
        r1 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="20K", location="北京"
        )
        r2 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="30K", location="北京"
        )
        assert r1 == r2

    def test_fallback_job_id_location_change_changes_identity(self) -> None:
        """P3.2：location 变化视为不同岗位身份，产生不同 fallback job_id。"""
        r1 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="20K", location="北京"
        )
        r2 = derive_job_id(
            job_url=None, title="前端", company="公司A", salary="20K", location="上海"
        )
        assert r1 != r2

    def test_url_job_id_still_has_priority(self) -> None:
        """P3.2：有安全 job_url 时仍优先从 URL 推导，不使用 fallback。"""
        r_url = derive_job_id(
            job_url="https://www.zhipin.com/job_detail/abc123.html",
            title="前端",
            company="公司A",
            salary="20K",
        )
        assert r_url == "abc123"
        # fallback 产生 hash 前缀
        r_hash = derive_job_id(job_url=None, title="前端", company="公司A", salary="20K")
        assert r_hash.startswith("hash:")


# ==================== TestJobListRecord ====================
class TestJobListRecord:
    """测试 JobListRecord 模型与 from_observed_card 转换。"""

    def test_from_observed_card_basic(self) -> None:
        """全字段卡片转换为 JobListRecord，字段映射正确。"""
        card = _make_card()
        record = JobListRecord.from_observed_card(card)
        assert record.job_id == "abc123"
        assert record.title == "前端工程师"
        assert record.salary == "25K"
        assert record.company == "示例科技公司"
        assert record.location == "北京·朝阳区"
        assert record.experience == "3-5年"
        assert record.education == "本科"
        assert record.job_url == "https://www.zhipin.com/job_detail/abc123.html"
        assert record.company_url == "https://www.zhipin.com/company/def456.html"

    def test_from_observed_card_missing_fields(self) -> None:
        """大量字段缺失的卡片仍可转换，job_id 由 hash 推导。"""
        card = ObservedJobCard(source_index=0)
        record = JobListRecord.from_observed_card(card)
        assert record.job_id.startswith("hash:")
        assert record.title is None
        assert record.company is None
        assert record.salary is None

    def test_from_observed_card_with_page_no(self) -> None:
        """page_no 通过关键字参数传入。"""
        card = _make_card()
        record = JobListRecord.from_observed_card(card, page_no=3)
        assert record.page_no == 3

    def test_from_observed_card_job_id_from_url(self) -> None:
        """card.job_url 为绝对官方 URL 时，job_id 从路径末段推导。"""
        card = _make_card(job_url="https://www.zhipin.com/job_detail/abc123.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_id == "abc123"

    def test_from_observed_card_relative_url_rejected(self) -> None:
        """card.job_url 为相对路径时，from_observed_card 防御性拒绝（无 base 无法脱敏）。

        相对 URL 无法通过 sanitize_url（无 scheme），job_url 置 None，
        job_id 回退到 hash。
        """
        card = _make_card(job_url="/job_detail/abc123.html")
        record = JobListRecord.from_observed_card(card)
        # 相对 URL 被拒绝，job_url 为 None
        assert record.job_url is None
        # job_id 回退到 hash
        assert record.job_id.startswith("hash:")

    def test_job_id_empty_rejected(self) -> None:
        """job_id 为空字符串应抛出 ValidationError。"""
        with pytest.raises(ValidationError):
            JobListRecord(job_id="")

    def test_collected_at_default(self) -> None:
        """未提供 collected_at 时自动填充为当前时间。"""
        before = datetime.now()
        record = JobListRecord(job_id="test")
        after = datetime.now()
        assert record.collected_at is not None
        assert before <= record.collected_at <= after


# ==================== TestJobListRepository ====================
class TestJobListRepository:
    """测试 JobListRepository SQLite 持久化（P3.1 三态 UPSERT）。"""

    def test_save_new_record(self, tmp_db_path, tmp_workspace) -> None:
        """首次保存返回 NEW，count == 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                record = JobListRecord(job_id="abc123", title="前端")
                outcome = repo.save_job_list(record)
                assert outcome == UpsertOutcome.NEW
                assert repo.count() == 1
        finally:
            db.close()

    def test_save_unchanged_existing(self, tmp_db_path, tmp_workspace) -> None:
        """重复保存同一 job_id 且业务字段不变返回 UNCHANGED，count 仍为 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                record = JobListRecord(job_id="abc123", title="前端")
                assert repo.save_job_list(record) == UpsertOutcome.NEW
                assert repo.save_job_list(record) == UpsertOutcome.UNCHANGED
                assert repo.count() == 1
        finally:
            db.close()

    def test_save_updated_existing(self, tmp_db_path, tmp_workspace) -> None:
        """同 job_id 但 title 变化返回 UPDATED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="abc123", title="前端"))
                outcome = repo.save_job_list(JobListRecord(job_id="abc123", title="后端"))
                assert outcome == UpsertOutcome.UPDATED
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
                repo = JobListRepository(conn)
                t1 = datetime(2026, 7, 29, 10, 0, 0)
                t2 = datetime(2026, 7, 29, 11, 0, 0)
                repo.save_job_list(JobListRecord(job_id="abc", title="t", collected_at=t1))
                outcome = repo.save_job_list(
                    JobListRecord(job_id="abc", title="t", collected_at=t2)
                )
                assert outcome == UpsertOutcome.UNCHANGED
        finally:
            db.close()

    # ==================== P3.2 page_no 语义修正测试 ====================
    def test_only_page_no_change_is_unchanged(self, tmp_db_path, tmp_workspace) -> None:
        """P3.2：仅 page_no 变化（业务字段全相同）返回 UNCHANGED。

        page_no 视为采集元数据（与 collected_at 同级），不参与业务字段比较。
        原因：同一岗位可能因排序变化从第 1 页移到第 2 页，内容无变化。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="abc", title="t", page_no=1))
                outcome = repo.save_job_list(JobListRecord(job_id="abc", title="t", page_no=2))
                assert outcome == UpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_page_no_and_collected_at_change_is_unchanged(self, tmp_db_path, tmp_workspace) -> None:
        """P3.2：page_no 和 collected_at 同时变化仍为 UNCHANGED。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                t1 = datetime(2026, 7, 29, 10, 0, 0)
                t2 = datetime(2026, 7, 29, 11, 0, 0)
                repo.save_job_list(
                    JobListRecord(job_id="abc", title="t", page_no=1, collected_at=t1)
                )
                outcome = repo.save_job_list(
                    JobListRecord(job_id="abc", title="t", page_no=2, collected_at=t2)
                )
                assert outcome == UpsertOutcome.UNCHANGED
        finally:
            db.close()

    def test_unchanged_still_refreshes_page_no(self, tmp_db_path, tmp_workspace) -> None:
        """P3.2：UNCHANGED 时数据库仍更新最新的 page_no。

        统计为 UNCHANGED，但 page_no 作为采集元数据应被刷新。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="abc", title="t", page_no=1))
                repo.save_job_list(JobListRecord(job_id="abc", title="t", page_no=5))
                rows = repo.get_all()
                assert rows[0]["page_no"] == 5
        finally:
            db.close()

    def test_business_field_change_with_page_change_is_updated(
        self, tmp_db_path, tmp_workspace
    ) -> None:
        """P3.2：业务字段变化 + page_no 同时变化仍为 UPDATED。

        page_no 不参与判断，但 title/salary 等业务字段任一变化仍 UPDATED。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(
                    JobListRecord(job_id="abc", title="前端", salary="20K", page_no=1)
                )
                outcome = repo.save_job_list(
                    JobListRecord(job_id="abc", title="后端", salary="25K", page_no=3)
                )
                assert outcome == UpsertOutcome.UPDATED
        finally:
            db.close()

    def test_bulk_upsert_all_new(self, tmp_db_path, tmp_workspace) -> None:
        """批量保存 3 条新记录返回 new=3，count == 3。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                records = [
                    JobListRecord(job_id="id1", title="t1"),
                    JobListRecord(job_id="id2", title="t2"),
                    JobListRecord(job_id="id3", title="t3"),
                ]
                result = repo.bulk_upsert_job_list(records)
                assert result.new_count == 3
                assert result.updated_count == 0
                assert result.unchanged_count == 0
                assert result.total == 3
                assert repo.count() == 3
        finally:
            db.close()

    def test_bulk_upsert_all_unchanged_on_repeat(self, tmp_db_path, tmp_workspace) -> None:
        """同一批数据第二次保存全部为 UNCHANGED，重复数量不再恒为 0。"""
        db = _new_db(tmp_db_path)
        try:
            records = [
                JobListRecord(job_id="id1", title="t1"),
                JobListRecord(job_id="id2", title="t2"),
                JobListRecord(job_id="id3", title="t3"),
            ]
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                first = repo.bulk_upsert_job_list(records)
                assert first.new_count == 3
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                second = repo.bulk_upsert_job_list(records)
                assert second.new_count == 0
                assert second.updated_count == 0
                assert second.unchanged_count == 3
                assert second.total == 3
        finally:
            db.close()

    def test_bulk_upsert_mixed(self, tmp_db_path, tmp_workspace) -> None:
        """混合批量：1 新增 + 1 更新 + 1 重复。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                # 预置 1 条
                repo.save_job_list(JobListRecord(job_id="exist", title="old"))
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                records = [
                    JobListRecord(job_id="exist", title="new"),  # UPDATED
                    JobListRecord(job_id="brand_new", title="fresh"),  # NEW
                    JobListRecord(job_id="exist", title="new"),  # UNCHANGED（同批次内重复）
                ]
                result = repo.bulk_upsert_job_list(records)
                assert result.new_count == 1
                assert result.updated_count == 1
                assert result.unchanged_count == 1
                assert result.total == 3
        finally:
            db.close()

    def test_same_batch_duplicate_job_id(self, tmp_db_path, tmp_workspace) -> None:
        """同一批次重复 job_id：统计不超过输入记录数。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                records = [
                    JobListRecord(job_id="dup", title="v1"),
                    JobListRecord(job_id="dup", title="v2"),  # UPDATED (相对 v1)
                    JobListRecord(job_id="dup", title="v2"),  # UNCHANGED (相对 v2)
                ]
                result = repo.bulk_upsert_job_list(records)
                assert result.total == 3
                assert result.new_count == 1
                assert result.updated_count == 1
                assert result.unchanged_count == 1
                assert repo.count() == 1
        finally:
            db.close()

    def test_job_id_dedup(self, tmp_db_path, tmp_workspace) -> None:
        """两条相同 job_id 的记录只保留 1 行，最新数据覆盖。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="dup", title="old"))
                repo.save_job_list(JobListRecord(job_id="dup", title="new"))
                assert repo.count() == 1
                rows = repo.get_all()
                assert len(rows) == 1
                assert rows[0]["title"] == "new"
        finally:
            db.close()

    def test_fallback_hash_dedup(self, tmp_db_path, tmp_workspace) -> None:
        """无 job_url 但 title+company 相同 → 同一 hash job_id → 去重。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                jid = derive_job_id(None, "前端", "公司", "25K")
                r1 = JobListRecord(job_id=jid, title="前端", company="公司", salary="25K")
                r2 = JobListRecord(
                    job_id=jid, title="前端", company="公司", salary="25K", location="北京"
                )
                repo.save_job_list(r1)
                repo.save_job_list(r2)
                assert repo.count() == 1
        finally:
            db.close()

    def test_fallback_salary_change_results_in_updated_not_new(
        self, tmp_db_path, tmp_workspace
    ) -> None:
        """P3.2 仓储集成：无 URL 岗位 salary 变化 → UPDATED 而非 NEW。

        场景：
        - 第一次保存：title/company/location 固定，salary=A → NEW
        - 第二次保存：title/company/location 不变，salary=B → UPDATED（不是 NEW）
        - 数据库总记录数仍为 1

        关键：fallback job_id 不再包含 salary，所以 salary 变化不改 job_id。
        """
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                # 第一次：salary=A
                jid_a = derive_job_id(
                    job_url=None, title="前端", company="公司A", salary="20K", location="北京"
                )
                r1 = JobListRecord(
                    job_id=jid_a, title="前端", company="公司A", salary="20K", location="北京"
                )
                outcome1 = repo.save_job_list(r1)
                assert outcome1 == UpsertOutcome.NEW

                # 第二次：salary=B，但身份字段不变
                jid_b = derive_job_id(
                    job_url=None, title="前端", company="公司A", salary="30K", location="北京"
                )
                assert jid_a == jid_b  # fallback job_id 不因 salary 变化
                r2 = JobListRecord(
                    job_id=jid_b, title="前端", company="公司A", salary="30K", location="北京"
                )
                outcome2 = repo.save_job_list(r2)
                assert outcome2 == UpsertOutcome.UPDATED
                assert repo.count() == 1
        finally:
            db.close()

    def test_url_dedup(self, tmp_db_path, tmp_workspace) -> None:
        """同一 job_url 推导同一 job_id → 去重。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                url = "https://www.zhipin.com/job_detail/abc123.html"
                jid = derive_job_id(url, "t", "c", "s")
                r1 = JobListRecord(job_id=jid, job_url=url, title="t1")
                r2 = JobListRecord(job_id=jid, job_url=url, title="t2")
                repo.save_job_list(r1)
                repo.save_job_list(r2)
                assert repo.count() == 1
        finally:
            db.close()

    def test_exists(self, tmp_db_path, tmp_workspace) -> None:
        """保存后 exists(job_id) 返回 True，不存在时返回 False。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="abc123", title="t"))
                assert repo.exists("abc123") is True
                assert repo.exists("not-exist") is False
        finally:
            db.close()

    def test_get_by_job_id(self, tmp_db_path, tmp_workspace) -> None:
        """get_by_job_id 返回现有记录，不存在返回 None。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="abc", title="t"))
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
                repo = JobListRepository(conn)
                for i in range(3):
                    repo.save_job_list(JobListRecord(job_id=f"id{i}", title=f"t{i}"))
                rows = repo.get_all()
                assert len(rows) == 3
        finally:
            db.close()

    def test_count(self, tmp_db_path, tmp_workspace) -> None:
        """含更新的保存后，count 返回去重后的数量。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                repo.save_job_list(JobListRecord(job_id="id1", title="t1"))
                repo.save_job_list(JobListRecord(job_id="id2", title="t2"))
                # 第三条与 id1 同 job_id，视为更新
                repo.save_job_list(JobListRecord(job_id="id1", title="t1-updated"))
                assert repo.count() == 2
        finally:
            db.close()


# ==================== TestParseListPageToJobListRecord ====================
class TestParseListPageToJobListRecord:
    """集成测试：parse_list_page → JobListRecord。"""

    def test_parse_basic_fixture(self) -> None:
        """解析 list_page_basic.html，转换后所有记录 job_id 非空。"""
        html = _load_fixture("list_page_basic.html")
        cards = parse_list_page(html, base_url=_BASE_URL)
        records = [JobListRecord.from_observed_card(card) for card in cards]
        assert len(records) == len(cards)
        assert len(records) == 2
        for record in records:
            assert record.job_id
            assert record.job_id.strip() != ""
        # 基础 fixture 的两个 job_id 来自 URL 路径末段
        job_ids = {record.job_id for record in records}
        assert "abc123" in job_ids
        assert "ghi789" in job_ids

    def test_parse_missing_fields_fixture(self) -> None:
        """解析缺字段 fixture，转换后记录仍可创建（含无效卡片的 hash job_id）。"""
        html = _load_fixture("list_page_missing_fields.html")
        cards = parse_list_page(html, base_url=_BASE_URL)
        records = [JobListRecord.from_observed_card(card) for card in cards]
        assert len(records) == len(cards)
        assert len(records) == 3
        for record in records:
            assert record.job_id
            assert record.job_id.strip() != ""

    def test_parse_empty_page(self) -> None:
        """解析空结果页，返回空列表。"""
        html = _load_fixture("empty_results_page.html")
        cards = parse_list_page(html, base_url=_BASE_URL)
        assert cards == []
        assert len(cards) == 0


# ==================== TestParseListPageWithDiagnostics ====================
class TestParseListPageWithDiagnostics:
    """P3.1: parse_list_page_with_diagnostics 接入测试。"""

    def test_basic_fixture_returns_diagnostics(self) -> None:
        """正常页面返回 cards 与 diagnostics。"""
        html = _load_fixture("list_page_basic.html")
        cards, diagnostics = parse_list_page_with_diagnostics(html, base_url=_BASE_URL)
        assert len(cards) == 2
        assert diagnostics.card_count == 2
        assert diagnostics.parser_success is True

    def test_missing_fields_fixture_diagnostics(self) -> None:
        """缺字段 fixture 的 diagnostics 统计缺失字段。"""
        html = _load_fixture("list_page_missing_fields.html")
        cards, diagnostics = parse_list_page_with_diagnostics(html, base_url=_BASE_URL)
        assert len(cards) == 3
        assert diagnostics.card_count == 3

    def test_empty_page_preserves_diagnostics(self) -> None:
        """空页面仍返回 diagnostics（card_count=0，suggest_manual_review=True）。"""
        html = _load_fixture("empty_results_page.html")
        cards, diagnostics = parse_list_page_with_diagnostics(html, base_url=_BASE_URL)
        assert cards == []
        assert diagnostics.card_count == 0
        assert diagnostics.parser_success is False
        assert diagnostics.suggest_manual_review is True

    def test_diagnostics_summary_no_raw_content(self) -> None:
        """DiagnosticsSummary 安全摘要不包含页面原文。"""
        html = _load_fixture("list_page_missing_fields.html")
        cards, diagnostics = parse_list_page_with_diagnostics(html, base_url=_BASE_URL)
        summary = build_diagnostics_summary(diagnostics)
        # summary 只含计数与字段名，不含文本样本
        assert isinstance(summary, DiagnosticsSummary)
        assert summary.card_count == len(cards)
        # missing_field_counts 的值都是整数（缺失数），不是文本
        for name, count in summary.missing_field_counts.items():
            assert isinstance(name, str)
            assert isinstance(count, int)
            assert count > 0


# ==================== TestDiagnosticsSummary ====================
class TestDiagnosticsSummary:
    """P3.1: build_diagnostics_summary 安全摘要测试。"""

    def test_summary_from_full_diagnostics(self) -> None:
        """完整 diagnostics 转换为安全摘要。"""
        diag = ParseDiagnostics(
            page_type=PageType.SEARCH_LIST,
            selector_version="p2-v1",
            root_matches={".job-list": 1, "job_card_total": 2},
            field_matches={"job_name": 2, "job_url": 2, "salary_text": 1, "education_text": 0},
            missing_required_fields=["education_text"],
            ambiguous_fields=[],
            warnings=["1 个卡片缺少 education"],
            parser_success=True,
            card_count=2,
            suggest_manual_review=False,
        )
        summary = build_diagnostics_summary(diag)
        assert summary.card_count == 2
        assert summary.warning_count == 1
        assert summary.missing_required_fields == ["education_text"]
        # salary_text 命中 1，缺失 = 2 - 1 = 1
        assert summary.missing_field_counts.get("salary_text") == 1
        # education_text 命中 0 → selector_miss_count 计入，不在 missing_field_counts
        assert "education_text" not in summary.missing_field_counts
        assert summary.selector_miss_count == 1
        assert summary.fallback_count == 0
        assert summary.parser_success is True

    def test_summary_empty_diagnostics(self) -> None:
        """空 diagnostics 转换为安全摘要（card_count=0）。"""
        diag = ParseDiagnostics(
            page_type=PageType.UNKNOWN,
            selector_version="p2-v1",
            root_matches={},
            field_matches={},
            missing_required_fields=[],
            ambiguous_fields=[],
            warnings=["未解析到任何岗位卡片"],
            parser_success=False,
            card_count=0,
            suggest_manual_review=True,
        )
        summary = build_diagnostics_summary(diag)
        assert summary.card_count == 0
        assert summary.warning_count == 1
        assert summary.missing_field_counts == {}
        assert summary.selector_miss_count == 0
        assert summary.suggest_manual_review is True

    def test_summary_does_not_leak_page_text(self) -> None:
        """安全摘要字段均为计数/字段名/布尔，不含页面原文。"""
        diag = ParseDiagnostics(
            page_type=PageType.SEARCH_LIST,
            selector_version="p2-v1",
            root_matches={".sensitive-selector": 1},
            field_matches={"job_name": 1},
            missing_required_fields=[],
            ambiguous_fields=[],
            warnings=["页面含敏感文本示例不应泄露"],
            parser_success=True,
            card_count=1,
            suggest_manual_review=False,
        )
        summary = build_diagnostics_summary(diag)
        # summary 不应包含 warnings 原文（仅 warning_count）
        assert not hasattr(summary, "warnings")
        assert summary.warning_count == 1
        # root_matches 不出现在 summary
        assert not hasattr(summary, "root_matches")


# ==================== TestUrlDefensiveValidation ====================
class TestUrlDefensiveValidation:
    """P3.1: from_observed_card URL 二次防御校验测试。

    验证进入 JobListRecord 的 job_url / company_url 不包含：
    query / fragment / userinfo / 显式端口 / 非 HTTPS / 非 BOSS 官方域名。
    """

    def test_job_url_query_removed(self) -> None:
        """job_url 含 query 时被剥离，返回无 query 的干净 URL。"""
        card = _make_card(job_url="https://www.zhipin.com/job_detail/abc.html?token=secret")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url == "https://www.zhipin.com/job_detail/abc.html"
        assert "?" not in record.job_url

    def test_job_url_fragment_removed(self) -> None:
        """job_url 含 fragment 时被剥离，返回无 fragment 的干净 URL。"""
        card = _make_card(job_url="https://www.zhipin.com/job_detail/abc.html#section")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url == "https://www.zhipin.com/job_detail/abc.html"
        assert "#" not in record.job_url

    def test_job_url_userinfo_rejected(self) -> None:
        """job_url 含 userinfo 时被拒绝。"""
        card = _make_card(job_url="https://user:pass@www.zhipin.com/job_detail/abc.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url is None

    def test_job_url_explicit_port_rejected(self) -> None:
        """job_url 含显式端口时被拒绝。"""
        card = _make_card(job_url="https://www.zhipin.com:8443/job_detail/abc.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url is None

    def test_job_url_http_rejected(self) -> None:
        """job_url 为 HTTP 时被拒绝（不自动升级）。"""
        card = _make_card(job_url="http://www.zhipin.com/job_detail/abc.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url is None

    def test_job_url_non_official_domain_rejected(self) -> None:
        """job_url 为非官方域名时被拒绝。"""
        card = _make_card(job_url="https://evil.com/job_detail/abc.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_url is None

    def test_company_url_query_removed(self) -> None:
        """company_url 含 query 时被剥离，返回无 query 的干净 URL。"""
        card = _make_card(company_url="https://www.zhipin.com/company/def.html?ref=x")
        record = JobListRecord.from_observed_card(card)
        assert record.company_url == "https://www.zhipin.com/company/def.html"
        assert "?" not in record.company_url

    def test_company_url_userinfo_rejected(self) -> None:
        """company_url 含 userinfo 时被拒绝。"""
        card = _make_card(company_url="https://user@www.zhipin.com/company/def.html")
        record = JobListRecord.from_observed_card(card)
        assert record.company_url is None

    def test_company_url_port_rejected(self) -> None:
        """company_url 含显式端口时被拒绝。"""
        card = _make_card(company_url="https://www.zhipin.com:443/company/def.html")
        record = JobListRecord.from_observed_card(card)
        assert record.company_url is None

    def test_company_url_http_rejected(self) -> None:
        """company_url 为 HTTP 时被拒绝。"""
        card = _make_card(company_url="http://www.zhipin.com/company/def.html")
        record = JobListRecord.from_observed_card(card)
        assert record.company_url is None

    def test_company_url_non_official_rejected(self) -> None:
        """company_url 为非官方域名时被拒绝。"""
        card = _make_card(company_url="https://evil.com/company/def.html")
        record = JobListRecord.from_observed_card(card)
        assert record.company_url is None

    def test_clean_official_urls_preserved(self) -> None:
        """干净的官方 HTTPS URL（无 query/fragment/userinfo/port）被保留。"""
        card = _make_card(
            job_url="https://www.zhipin.com/job_detail/abc123.html",
            company_url="https://www.zhipin.com/company/def456.html",
        )
        record = JobListRecord.from_observed_card(card)
        assert record.job_url == "https://www.zhipin.com/job_detail/abc123.html"
        assert record.company_url == "https://www.zhipin.com/company/def456.html"
