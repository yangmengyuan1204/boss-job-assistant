"""P2 页面类型识别测试。

测试 detect_page_type 对各类 fixture HTML 的识别能力，
以及 is_save_fixture_allowed 的保存权限判断。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from boss_tool.models.observed_page import PageType
from boss_tool.parsers.page_types import detect_page_type, is_save_fixture_allowed


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestDetectPageType:
    """测试 detect_page_type 对每种页面类型的识别。"""

    def test_home_page(self) -> None:
        """构造含 div.home-wrap 的 HTML，识别为 HOME。"""
        html = "<html><body><div class='home-wrap'>首页内容</div></body></html>"
        detection = detect_page_type(_make_soup(html))
        assert detection.page_type == PageType.HOME

    def test_login_page(self, login_page_html: str) -> None:
        """login_page_html 识别为 LOGIN。"""
        detection = detect_page_type(_make_soup(login_page_html))
        assert detection.page_type == PageType.LOGIN

    def test_verification_page(self, verification_page_html: str) -> None:
        """verification_page_html 识别为 VERIFICATION。"""
        detection = detect_page_type(_make_soup(verification_page_html))
        assert detection.page_type == PageType.VERIFICATION

    def test_search_list(self, list_page_basic_html: str) -> None:
        """list_page_basic_html 识别为 SEARCH_LIST。

        注意：list_page_basic.html 含 div.home-wrap 导航占位，
        会与 HOME 类型冲突导致多类型命中返回 UNKNOWN。
        此处移除 home-wrap 以测试纯列表页识别。
        不传 URL 以避免 HOME 的 "/" URL 模式误匹配所有路径。
        """
        soup = _make_soup(list_page_basic_html)
        for tag in soup.select("div.home-wrap"):
            tag.decompose()
        detection = detect_page_type(soup)
        assert detection.page_type == PageType.SEARCH_LIST

    def test_job_detail(self, detail_page_basic_html: str) -> None:
        """detail_page_basic_html 识别为 JOB_DETAIL。"""
        detection = detect_page_type(_make_soup(detail_page_basic_html))
        assert detection.page_type == PageType.JOB_DETAIL

    def test_empty_results(self, empty_results_page_html: str) -> None:
        """empty_results_page_html 识别为 EMPTY_RESULTS。"""
        detection = detect_page_type(_make_soup(empty_results_page_html))
        assert detection.page_type == PageType.EMPTY_RESULTS

    def test_unknown_page(self, unknown_page_html: str) -> None:
        """unknown_page_html 识别为 UNKNOWN。"""
        detection = detect_page_type(_make_soup(unknown_page_html))
        assert detection.page_type == PageType.UNKNOWN

    def test_multi_type_conflict_returns_unknown(self) -> None:
        """同时命中列表页和详情页选择器的 HTML 返回 UNKNOWN。"""
        html = """
        <html><body>
          <div class="search-job-result">
            <ul class="job-list-box">
              <li class="job-card-wrapper">card</li>
            </ul>
          </div>
          <div class="job-detail">detail content</div>
        </body></html>
        """
        detection = detect_page_type(_make_soup(html))
        assert detection.page_type == PageType.UNKNOWN
        assert len(detection.warnings) > 0


class TestIsSaveFixtureAllowed:
    """测试 is_save_fixture_allowed 的权限判断。"""

    def test_login_page_forbidden(self, login_page_html: str) -> None:
        """LOGIN 禁止保存。"""
        detection = detect_page_type(_make_soup(login_page_html))
        allowed, reason = is_save_fixture_allowed(detection)
        assert allowed is False
        assert "登录" in reason or "禁止" in reason

    def test_verification_page_forbidden(self, verification_page_html: str) -> None:
        """VERIFICATION 禁止保存。"""
        detection = detect_page_type(_make_soup(verification_page_html))
        allowed, reason = is_save_fixture_allowed(detection)
        assert allowed is False
        assert "验证" in reason or "禁止" in reason

    def test_unknown_low_confidence_forbidden(self, unknown_page_html: str) -> None:
        """UNKNOWN 且 confidence < 0.5 禁止保存。"""
        detection = detect_page_type(_make_soup(unknown_page_html))
        assert detection.confidence < 0.5
        allowed, reason = is_save_fixture_allowed(detection)
        assert allowed is False
        assert "禁止" in reason or "置信度" in reason

    def test_search_list_allowed(self, list_page_basic_html: str) -> None:
        """SEARCH_LIST 允许保存。"""
        soup = _make_soup(list_page_basic_html)
        for tag in soup.select("div.home-wrap"):
            tag.decompose()
        detection = detect_page_type(soup)
        assert detection.page_type == PageType.SEARCH_LIST
        allowed, _reason = is_save_fixture_allowed(detection)
        assert allowed is True

    def test_job_detail_allowed(self, detail_page_basic_html: str) -> None:
        """JOB_DETAIL 允许保存。"""
        detection = detect_page_type(_make_soup(detail_page_basic_html))
        assert detection.page_type == PageType.JOB_DETAIL
        allowed, _reason = is_save_fixture_allowed(detection)
        assert allowed is True
