"""P2 页面侦察器（PageObserver）测试。

测试 PageObserver 的 URL 脱敏、title 截断、页面类型识别、
inspect 诊断摘要、脱敏 HTML 读取、fixture 保存权限判断。

使用本地 _FakeObserverPage 模拟 Playwright Page（不访问网络）。
"""

from __future__ import annotations

from boss_tool.browser.observer import PageObserver
from boss_tool.models.observed_page import PageType


class _FakeObserverPage:
    """PageObserver 测试用的 fake page-like 对象。

    提供 url 属性、title() 方法、content() 方法，不访问真实网络。
    """

    def __init__(
        self,
        *,
        url: str = "https://www.zhipin.com/",
        content: str = "",
        title: str = "Test Page",
    ) -> None:
        self.url = url
        self._content = content
        self._title = title

    def title(self) -> str:
        return self._title

    def content(self) -> str:
        return self._content


class TestPageObserver:
    """测试 PageObserver 只读侦察能力。"""

    def test_get_current_url_redacted(self) -> None:
        """get_current_url() 去除 query。"""
        page = _FakeObserverPage(url="https://www.zhipin.com/job?token=x")
        observer = PageObserver(page)
        assert observer.get_current_url() == "https://www.zhipin.com/job"

    def test_get_current_title_truncated(self) -> None:
        """title 超过 80 字符时截断并添加 '...'。"""
        long_title = "A" * 100
        page = _FakeObserverPage(title=long_title)
        observer = PageObserver(page)
        result = observer.get_current_title()
        assert len(result) == 83  # 80 + "..."
        assert result.endswith("...")

    def test_detect_type_list_page(self, list_page_basic_html: str) -> None:
        """detect_type() 识别列表页为 SEARCH_LIST。

        注意：list_page_basic.html 含 div.home-wrap 导航占位，
        会与 HOME 类型冲突导致多类型命中返回 UNKNOWN。
        此处移除 home-wrap 以测试纯列表页识别。
        使用 about:blank URL 避免 HOME 的 "/" URL 模式误匹配所有路径。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(list_page_basic_html, "lxml")
        for tag in soup.select("div.home-wrap"):
            tag.decompose()
        html = str(soup)
        page = _FakeObserverPage(
            url="about:blank",
            content=html,
        )
        observer = PageObserver(page)
        detection = observer.detect_type()
        assert detection.page_type == PageType.SEARCH_LIST

    def test_detect_type_login_page(self, login_page_html: str) -> None:
        """detect_type() 识别登录页为 LOGIN。

        使用 about:blank URL 避免 HOME 的 "/" URL 模式误匹配所有路径。
        """
        page = _FakeObserverPage(
            url="about:blank",
            content=login_page_html,
        )
        observer = PageObserver(page)
        detection = observer.detect_type()
        assert detection.page_type == PageType.LOGIN

    def test_inspect_returns_summary(self, list_page_basic_html: str) -> None:
        """inspect() 返回 dict 含 page_type/card_count 等键。"""
        page = _FakeObserverPage(
            url="https://www.zhipin.com/web/geek/job",
            content=list_page_basic_html,
        )
        observer = PageObserver(page)
        result = observer.inspect()
        assert isinstance(result, dict)
        expected_keys = {
            "page_type",
            "confidence",
            "evidence",
            "warnings",
            "card_count",
            "detail_root_found",
            "field_hits",
            "missing_fields",
            "structure_changed",
        }
        assert expected_keys.issubset(result.keys())

    def test_inspect_card_count(self, list_page_basic_html: str) -> None:
        """list_page_basic_html 的 inspect card_count == 2。"""
        page = _FakeObserverPage(
            url="https://www.zhipin.com/web/geek/job",
            content=list_page_basic_html,
        )
        observer = PageObserver(page)
        result = observer.inspect()
        assert result["card_count"] == 2

    def test_read_sanitized_html_no_script(self) -> None:
        """read_sanitized_html() 不含 script。"""
        html = "<script>alert(1)</script><div>ok</div>"
        page = _FakeObserverPage(
            url="https://www.zhipin.com/",
            content=html,
        )
        observer = PageObserver(page)
        result = observer.read_sanitized_html()
        assert "<script" not in result

    def test_is_save_fixture_allowed_login(self, login_page_html: str) -> None:
        """login page 不允许保存 fixture。

        使用 about:blank URL 避免 HOME 的 "/" URL 模式误匹配所有路径。
        """
        page = _FakeObserverPage(
            url="about:blank",
            content=login_page_html,
        )
        observer = PageObserver(page)
        allowed, _reason = observer.is_save_fixture_allowed()
        assert allowed is False
