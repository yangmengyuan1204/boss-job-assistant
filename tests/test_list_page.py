"""P2 列表页解析器测试。

测试 parse_list_page / parse_single_card / find_job_cards，
覆盖基本卡片解析、字段缺失处理、标签去重、URL 标准化、卡片顺序保留。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from boss_tool.parsers.list_page import find_job_cards, parse_list_page

_BASE_URL = "https://www.zhipin.com/"


class TestParseListPage:
    """测试 parse_list_page 基本解析能力。"""

    def test_basic_two_cards(self, list_page_basic_html: str) -> None:
        """list_page_basic_html 解析出 2 个卡片。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert len(cards) == 2

    def test_first_card_job_name(self, list_page_basic_html: str) -> None:
        """第一个卡片 job_name == '资深前端工程师'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert cards[0].job_name == "资深前端工程师"

    def test_first_card_salary(self, list_page_basic_html: str) -> None:
        """第一个卡片 salary_text == '25-50K·14薪'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert cards[0].salary_text == "25-50K·14薪"

    def test_first_card_company(self, list_page_basic_html: str) -> None:
        """第一个卡片 company_name == '示例科技有限公司'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert cards[0].company_name == "示例科技有限公司"

    def test_first_card_recruiter(self, list_page_basic_html: str) -> None:
        """第一个卡片 recruiter_name == '张先生'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert cards[0].recruiter_name == "张先生"

    def test_first_card_benefits(self, list_page_basic_html: str) -> None:
        """第一个卡片 benefits 含 '五险一金'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert "五险一金" in cards[0].benefits

    def test_first_card_tags(self, list_page_basic_html: str) -> None:
        """第一个卡片 tags 含 'React'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        assert "React" in cards[0].tags

    def test_card_order_preserved(self, list_page_basic_html: str) -> None:
        """source_index 从 0 递增。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        for i, card in enumerate(cards):
            assert card.source_index == i

    def test_url_absolute(self, list_page_basic_html: str) -> None:
        """job_url 是绝对 URL（以 https://www.zhipin.com 开头）。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        for card in cards:
            if card.job_url is not None:
                assert card.job_url.startswith("https://www.zhipin.com")

    def test_url_no_query(self, list_page_basic_html: str) -> None:
        """job_url 不含 '?'。"""
        cards = parse_list_page(list_page_basic_html, base_url=_BASE_URL)
        for card in cards:
            if card.job_url is not None:
                assert "?" not in card.job_url


class TestParseListPageMissingFields:
    """测试 parse_list_page 对缺字段卡片的处理。"""

    def test_missing_fields_card_count(self, list_page_missing_html: str) -> None:
        """list_page_missing_html 解析出 3 个卡片。"""
        cards = parse_list_page(list_page_missing_html, base_url=_BASE_URL)
        assert len(cards) == 3

    def test_invalid_card_warning(self, list_page_missing_html: str) -> None:
        """第2个卡片（source_index=1）warnings 含 '缺少岗位名和岗位URL'。"""
        cards = parse_list_page(list_page_missing_html, base_url=_BASE_URL)
        invalid_card = cards[1]
        assert invalid_card.source_index == 1
        warning_text = " ".join(invalid_card.warnings)
        assert "缺少岗位名和岗位URL" in warning_text

    def test_missing_salary(self, list_page_missing_html: str) -> None:
        """第1个卡片 salary_text is None。"""
        cards = parse_list_page(list_page_missing_html, base_url=_BASE_URL)
        assert cards[0].salary_text is None

    def test_duplicate_tags_deduped(self, list_page_missing_html: str) -> None:
        """第3个卡片 benefits 去重后不含重复 '重复标签'。"""
        cards = parse_list_page(list_page_missing_html, base_url=_BASE_URL)
        third_card = cards[2]
        assert third_card.source_index == 2
        assert third_card.benefits.count("重复标签") == 1


class TestFindJobCards:
    """测试 find_job_cards 卡片查找。"""

    def test_find_cards_from_basic(self, list_page_basic_html: str) -> None:
        """从 BeautifulSoup 解析 list_page_basic_html 找到 2 个卡片。"""
        soup = BeautifulSoup(list_page_basic_html, "lxml")
        cards = find_job_cards(soup)
        assert len(cards) == 2
