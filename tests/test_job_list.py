"""P3 搜索结果列表采集测试。

测试 JobListRecord 模型、derive_job_id 推导函数、JobListRepository SQLite 持久化，
以及 parse_list_page → JobListRecord 的集成转换。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from boss_tool.models.job_list import JobListRecord, derive_job_id
from boss_tool.models.observed_page import ObservedJobCard
from boss_tool.parsers.list_page import parse_list_page
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
        """card.job_url 为相对路径时，job_id 仍从路径末段推导。"""
        card = _make_card(job_url="/job_detail/abc123.html")
        record = JobListRecord.from_observed_card(card)
        assert record.job_id == "abc123"

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
    """测试 JobListRepository SQLite 持久化。"""

    def test_save_new_record(self, tmp_db_path, tmp_workspace) -> None:
        """首次保存返回 True，count == 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                record = JobListRecord(job_id="abc123", title="前端")
                is_new = repo.save_job_list(record)
                assert is_new is True
                assert repo.count() == 1
        finally:
            db.close()

    def test_save_update_existing(self, tmp_db_path, tmp_workspace) -> None:
        """重复保存同一 job_id 返回 False，count 仍为 1。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                record = JobListRecord(job_id="abc123", title="前端")
                assert repo.save_job_list(record) is True
                assert repo.save_job_list(record) is False
                assert repo.count() == 1
        finally:
            db.close()

    def test_bulk_upsert_all_new(self, tmp_db_path, tmp_workspace) -> None:
        """批量保存 3 条新记录返回 (3, 0)，count == 3。"""
        db = _new_db(tmp_db_path)
        try:
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                records = [
                    JobListRecord(job_id="id1", title="t1"),
                    JobListRecord(job_id="id2", title="t2"),
                    JobListRecord(job_id="id3", title="t3"),
                ]
                new_count, update_count = repo.bulk_upsert_job_list(records)
                assert (new_count, update_count) == (3, 0)
                assert repo.count() == 3
        finally:
            db.close()

    def test_bulk_upsert_mixed(self, tmp_db_path, tmp_workspace) -> None:
        """先保存 3 条，再保存同一批返回 (0, 3)。"""
        db = _new_db(tmp_db_path)
        try:
            records = [
                JobListRecord(job_id="id1", title="t1"),
                JobListRecord(job_id="id2", title="t2"),
                JobListRecord(job_id="id3", title="t3"),
            ]
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                assert repo.bulk_upsert_job_list(records) == (3, 0)
            with db.transaction() as conn:
                repo = JobListRepository(conn)
                assert repo.bulk_upsert_job_list(records) == (0, 3)
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
        """无 job_url 但 title+company+salary 相同 → 同一 hash job_id → 去重。"""
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
