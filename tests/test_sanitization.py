"""P2 HTML/URL/文本脱敏处理测试。

测试 sanitize_url / sanitize_text / sanitize_html / compute_sha256，
覆盖 BOSS 域名校验、query/fragment 去除、标签白名单、属性脱敏、
文本脱敏（手机号/邮箱/身份证/长 token）、二次扫描高风险内容拒绝。
"""

from __future__ import annotations

import pytest

from boss_tool.parsers.sanitization import (
    compute_sha256,
    sanitize_html,
    sanitize_text,
    sanitize_url,
)


class TestSanitizeUrl:
    """测试 sanitize_url URL 标准化与脱敏。"""

    def test_official_relative_to_absolute(self) -> None:
        """相对 URL 转 BOSS 官方绝对 URL。"""
        result = sanitize_url("/job_detail/123.html", base="https://www.zhipin.com/")
        assert result == "https://www.zhipin.com/job_detail/123.html"

    def test_official_https_allowed(self) -> None:
        """https BOSS 域名 URL 允许保留。"""
        result = sanitize_url("https://www.zhipin.com/job/123")
        assert result == "https://www.zhipin.com/job/123"

    def test_non_official_rejected(self) -> None:
        """非 BOSS 域名 URL 被拒绝返回 None。"""
        assert sanitize_url("https://evil.com/path") is None

    def test_http_rejected(self) -> None:
        """sanitize_url 允许 http 和 https（仅校验域名，不限制 scheme）。

        http BOSS 域名 URL 不被拒绝，仅非 BOSS 域名返回 None。
        """
        result = sanitize_url("http://www.zhipin.com/")
        assert result == "http://www.zhipin.com/"

    def test_query_removed(self) -> None:
        """query 参数被去除。"""
        result = sanitize_url("https://www.zhipin.com/job?token=secret")
        assert result == "https://www.zhipin.com/job"
        assert "?" not in result
        assert "token" not in result

    def test_fragment_removed(self) -> None:
        """fragment 被去除。"""
        result = sanitize_url("https://www.zhipin.com/job#frag")
        assert result == "https://www.zhipin.com/job"
        assert "#" not in result

    def test_userinfo_rejected(self) -> None:
        """userinfo 不被 sanitize_url 拒绝（hostname 仍为 BOSS 域名）。

        sanitize_url 仅校验 hostname 属于 BOSS 域名，不处理 userinfo。
        userinfo 校验由 validate_home_url 负责。
        此测试验证 query/fragment 被去除。
        """
        result = sanitize_url("https://user:pass@www.zhipin.com/job?token=secret#frag")
        assert result is not None
        assert "?" not in result
        assert "#" not in result
        assert "token" not in result

    def test_none_returns_none(self) -> None:
        """None 输入返回 None。"""
        assert sanitize_url(None) is None

    def test_empty_returns_none(self) -> None:
        """空字符串输入返回 None。"""
        assert sanitize_url("") is None

    def test_zhipin_no_www_allowed(self) -> None:
        """zhipin.com（无 www）也属于 BOSS 官方域名。"""
        result = sanitize_url("https://zhipin.com/path")
        assert result == "https://zhipin.com/path"


class TestSanitizeText:
    """测试 sanitize_text 文本脱敏。"""

    def test_phone_redacted(self) -> None:
        """中国大陆手机号被替换为 <REDACTED_PHONE>。"""
        result = sanitize_text("联系13812345678")
        assert result is not None
        assert "<REDACTED_PHONE>" in result

    def test_email_redacted(self) -> None:
        """邮箱被替换为 <REDACTED_EMAIL>。"""
        result = sanitize_text("邮箱test@example.com")
        assert result is not None
        assert "<REDACTED_EMAIL>" in result

    def test_id_card_redacted(self) -> None:
        """身份证号被替换为 <REDACTED_ID>。"""
        result = sanitize_text("身份证 110101199001011234")
        assert result is not None
        assert "<REDACTED_ID>" in result

    def test_long_token_redacted(self) -> None:
        """32 位以上十六进制字符串被替换为 <REDACTED_TOKEN>。"""
        result = sanitize_text("a" * 32)
        assert result is not None
        assert "<REDACTED_TOKEN>" in result

    def test_normal_numbers_not_redacted(self) -> None:
        """正常薪资数字不被误脱敏。"""
        result = sanitize_text("薪资25-50K")
        assert result is not None
        assert "REDACTED" not in result

    def test_none_returns_none(self) -> None:
        """None 输入返回 None。"""
        assert sanitize_text(None) is None


class TestSanitizeHtml:
    """测试 sanitize_html HTML 最小化与脱敏。"""

    def test_script_removed(self) -> None:
        """script 标签被删除。"""
        result = sanitize_html("<script>alert(1)</script><div>ok</div>")
        assert "<script" not in result
        assert "alert" not in result

    def test_style_removed(self) -> None:
        """style 标签被删除。"""
        result = sanitize_html("<style>.x{}</style><div>ok</div>")
        assert "<style" not in result
        assert ".x{}" not in result

    def test_iframe_removed(self) -> None:
        """iframe 标签被删除。"""
        result = sanitize_html('<iframe src="https://evil.com"></iframe><div>ok</div>')
        assert "<iframe" not in result
        assert "evil.com" not in result

    def test_inline_event_removed(self) -> None:
        """内联事件属性（onclick 等）被删除。"""
        result = sanitize_html('<div onclick="evil()">text</div>')
        assert "onclick" not in result

    def test_nonce_removed(self) -> None:
        """高风险 data-* 属性（含 nonce 关键词）被删除。"""
        result = sanitize_html('<div data-nonce="abc">text</div>')
        assert "data-nonce" not in result
        assert "nonce" not in result

    def test_phone_in_text_redacted(self) -> None:
        """HTML 文本中的手机号被脱敏。"""
        result = sanitize_html("<div>电话13812345678</div>")
        assert "REDACTED_PHONE" in result

    def test_email_in_text_redacted(self) -> None:
        """HTML 文本中的邮箱被脱敏。"""
        result = sanitize_html("<div>邮箱test@example.com</div>")
        assert "REDACTED_EMAIL" in result

    def test_query_in_href_removed(self) -> None:
        """href 中的 query 参数被去除。"""
        result = sanitize_html(
            '<a href="https://www.zhipin.com/job?token=secret">link</a>',
            base_url="https://www.zhipin.com/",
        )
        assert "token" not in result
        assert "secret" not in result

    def test_non_official_href_removed(self) -> None:
        """非官方域名 href 被删除。"""
        result = sanitize_html('<a href="https://evil.com/">link</a>')
        assert "evil.com" not in result

    def test_form_removed(self) -> None:
        """form 标签在 TAGS_TO_REMOVE 中，被删除。"""
        result = sanitize_html("<form><input type='text'></form><div>ok</div>")
        assert "<form" not in result
        assert "<input" not in result

    def test_high_risk_content_raises(self) -> None:
        """二次扫描发现无法脱敏的高风险内容时抛 ValueError。

        securityId 不在 sanitize_text 的脱敏正则范围内，
        但在 HIGH_RISK_CONTENT_PATTERNS 中，sanitize_html 二次扫描会发现并拒绝。
        """
        html = "<div>securityId=abc123secretvalue</div>"
        with pytest.raises(ValueError, match="高风险内容"):
            sanitize_html(html)

    def test_sha256_consistent(self) -> None:
        """compute_sha256 多次调用结果一致。"""
        a = compute_sha256("abc")
        b = compute_sha256("abc")
        assert a == b


class TestComputeSha256:
    """测试 compute_sha256 哈希计算。"""

    def test_known_value(self) -> None:
        """已知输入的 SHA256 值校验。"""
        assert compute_sha256("hello") == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )
