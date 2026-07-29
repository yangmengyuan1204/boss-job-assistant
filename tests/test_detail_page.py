"""P2 详情页解析器测试。

测试 parse_detail_page / find_detail_root，
覆盖基本字段解析、缺字段处理、描述换行保留。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from boss_tool.models.observed_page import ObservedJobDetail
from boss_tool.parsers.detail_page import find_detail_root, parse_detail_page


class TestParseDetailPage:
    """测试 parse_detail_page 基本解析能力。"""

    def test_basic_job_name(self, detail_page_basic_html: str) -> None:
        """detail_page_basic_html 解析 job_name == '资深前端工程师'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.job_name == "资深前端工程师"

    def test_basic_salary(self, detail_page_basic_html: str) -> None:
        """salary_text == '25-50K·14薪'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.salary_text == "25-50K·14薪"

    def test_basic_location(self, detail_page_basic_html: str) -> None:
        """location_text 含 '北京'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.location_text is not None
        assert "北京" in detail.location_text

    def test_basic_description(self, detail_page_basic_html: str) -> None:
        """description 非空且含 '前端'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.description is not None
        assert "前端" in detail.description

    def test_basic_address(self, detail_page_basic_html: str) -> None:
        """address_text 含 '北京市'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.address_text is not None
        assert "北京市" in detail.address_text

    def test_basic_company(self, detail_page_basic_html: str) -> None:
        """company_name == '示例科技有限公司'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.company_name == "示例科技有限公司"

    def test_basic_recruiter(self, detail_page_basic_html: str) -> None:
        """recruiter_name == '王先生'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.recruiter_name == "王先生"

    def test_basic_benefits(self, detail_page_basic_html: str) -> None:
        """benefits 含 '五险一金'。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert "五险一金" in detail.benefits

    def test_description_preserves_newlines(self, detail_page_basic_html: str) -> None:
        """description 含换行。"""
        detail, _ = parse_detail_page(detail_page_basic_html)
        assert detail.description is not None
        assert "\n" in detail.description


class TestParseDetailPageMissingFields:
    """测试 parse_detail_page 对缺字段页面的处理。"""

    def test_missing_fields_not_none(self, detail_page_missing_html: str) -> None:
        """detail_page_missing_html 解析返回 ObservedJobDetail 对象。"""
        detail, _ = parse_detail_page(detail_page_missing_html)
        assert isinstance(detail, ObservedJobDetail)

    def test_missing_job_name(self, detail_page_missing_html: str) -> None:
        """job_name == '缺字段的测试岗位'。"""
        detail, _ = parse_detail_page(detail_page_missing_html)
        assert detail.job_name == "缺字段的测试岗位"

    def test_missing_location(self, detail_page_missing_html: str) -> None:
        """location_text is None。"""
        detail, _ = parse_detail_page(detail_page_missing_html)
        assert detail.location_text is None

    def test_missing_company(self, detail_page_missing_html: str) -> None:
        """company_name is None。"""
        detail, _ = parse_detail_page(detail_page_missing_html)
        assert detail.company_name is None

    def test_missing_recruiter(self, detail_page_missing_html: str) -> None:
        """recruiter_name is None。"""
        detail, _ = parse_detail_page(detail_page_missing_html)
        assert detail.recruiter_name is None


class TestFindDetailRoot:
    """测试 find_detail_root 详情主容器查找。"""

    def test_find_root_from_basic(self, detail_page_basic_html: str) -> None:
        """从 BeautifulSoup 解析 detail_page_basic_html 找到 detail root。"""
        soup = BeautifulSoup(detail_page_basic_html, "lxml")
        root = find_detail_root(soup)
        assert root is not None
