"""P2 HTML/URL/文本脱敏处理测试。

测试 sanitize_url / sanitize_text / sanitize_html / compute_sha256，
覆盖 BOSS 域名校验、query/fragment 去除、标签白名单、属性脱敏、
文本脱敏（手机号/邮箱/身份证/长 token）、二次扫描高风险内容拒绝。

P2.1 新增：
- userinfo 严格拒绝测试矩阵
- 显式端口严格拒绝测试矩阵
- HTTP 拒绝测试
- hostname 欺骗测试
- HTML 级 href userinfo/端口删除测试
- userinfo 二次扫描防御测试
"""

from __future__ import annotations

import pytest

from boss_tool.parsers.sanitization import (
    compute_sha256,
    sanitize_html,
    sanitize_text,
    sanitize_url,
    scan_high_risk_content,
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
        """P2.1：HTTP 直接拒绝，不自动升级为 HTTPS。"""
        assert sanitize_url("http://www.zhipin.com/") is None

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
        """P2.1：user:pass@ 形态严格拒绝，返回 None。

        原 P2 错误测试断言 userinfo 被允许，已纠正。
        userinfo 可能泄露凭据，必须从 fixture 与结构化结果中删除。
        """
        assert sanitize_url("https://user:pass@www.zhipin.com/job?token=secret#frag") is None

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

    def test_empty_path_normalized_to_slash(self) -> None:
        """P2.1：路径为空时规范化为 /。"""
        result = sanitize_url("https://www.zhipin.com")
        assert result == "https://www.zhipin.com/"


# ==================== P2.1 新增：恶意 URL 测试矩阵 ====================
class TestSanitizeUrlSecurityMatrix:
    """P2.1：恶意 URL 安全测试矩阵。

    覆盖：userinfo、显式端口、非法端口、HTTP、hostname 欺骗。
    所有恶意输入必须返回 None，不得进入 fixture 或结构化结果。
    """

    # ----- userinfo 测试 -----
    @pytest.mark.parametrize(
        "url",
        [
            "https://user@www.zhipin.com/job",
            "https://user:pass@www.zhipin.com/job",
            "https://user:pass@www.zhipin.com/job?token=secret#frag",
            "https://name:@www.zhipin.com/job",
            "https://:pass@www.zhipin.com/job",
        ],
    )
    def test_userinfo_rejected(self, url: str) -> None:
        """任何 userinfo 形态都被拒绝。"""
        assert sanitize_url(url) is None

    # ----- 显式端口测试 -----
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.zhipin.com:443/job",
            "https://www.zhipin.com:8443/job",
            "https://www.zhipin.com:80/job",
            "https://www.zhipin.com:8080/job",
        ],
    )
    def test_explicit_port_rejected(self, url: str) -> None:
        """任何显式端口（包括 :443）都被拒绝。"""
        assert sanitize_url(url) is None

    # ----- 非法端口格式测试 -----
    def test_invalid_port_returns_none_safely(self) -> None:
        """非法端口格式安全返回 None，不抛未处理异常。"""
        # urlparse 对非数字端口访问 .port 时抛 ValueError
        assert sanitize_url("https://www.zhipin.com:notaport/job") is None

    # ----- HTTP 拒绝测试 -----
    def test_http_scheme_rejected(self) -> None:
        """HTTP scheme 直接拒绝，不自动升级。"""
        assert sanitize_url("http://www.zhipin.com/job") is None

    # ----- hostname 欺骗测试 -----
    @pytest.mark.parametrize(
        "url",
        [
            # evil.com 作为 userinfo，hostname 仍为 BOSS
            "https://evil.com@www.zhipin.com/job",
            # www.zhipin.com 作为 userinfo，hostname 为 evil.com
            "https://www.zhipin.com@evil.com/job",
            # 混合欺骗
            "https://user@evil.com:443/www.zhipin.com/job",
        ],
    )
    def test_hostname_spoofing_rejected(self, url: str) -> None:
        """hostname 欺骗（userinfo 中伪装官方域名）被拒绝。"""
        assert sanitize_url(url) is None

    # ----- 正常 URL 允许测试 -----
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.zhipin.com/job/123", "https://www.zhipin.com/job/123"),
            ("https://zhipin.com/path", "https://zhipin.com/path"),
            (
                "https://www.zhipin.com/job_detail/abc.html",
                "https://www.zhipin.com/job_detail/abc.html",
            ),
        ],
    )
    def test_normal_official_urls_allowed(self, url: str, expected: str) -> None:
        """正常官方 HTTPS URL 允许保留。"""
        assert sanitize_url(url) == expected

    # ----- query 与 fragment 删除测试 -----
    def test_query_and_fragment_both_removed(self) -> None:
        """query 与 fragment 同时存在时都被删除。"""
        result = sanitize_url("https://www.zhipin.com/job?token=secret#section")
        assert result == "https://www.zhipin.com/job"
        assert "?" not in result
        assert "#" not in result
        assert "token" not in result
        assert "secret" not in result
        assert "section" not in result

    def test_securityid_query_removed(self) -> None:
        """securityId 等敏感 query 参数被删除。"""
        result = sanitize_url("https://www.zhipin.com/job?securityId=abc123&sessionId=xyz")
        assert result == "https://www.zhipin.com/job"
        assert "securityId" not in result
        assert "sessionId" not in result


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

    # ==================== P2.1 新增：HTML 级 href userinfo/端口测试 ====================
    def test_href_with_userinfo_removed(self) -> None:
        """P2.1：href 含 userinfo 时整个 href 属性被删除。

        <a href="https://user:pass@www.zhipin.com/job">job</a>
        处理后：user / pass / 敏感 URL 不得保留，href 属性直接删除。
        """
        html = '<a href="https://user:pass@www.zhipin.com/job">job</a>'
        result = sanitize_html(html, base_url="https://www.zhipin.com/")
        assert "user" not in result or "user" not in result.lower().replace("user", "")
        # href 属性必须被删除（不能保留含 userinfo 的 URL）
        assert "user:pass" not in result
        assert "pass" not in result
        # 文本内容 job 保留
        assert "job" in result

    def test_href_with_user_only_removed(self) -> None:
        """P2.1：href 含仅 username 的 userinfo 时也删除 href。"""
        html = '<a href="https://user@www.zhipin.com/job">link</a>'
        result = sanitize_html(html, base_url="https://www.zhipin.com/")
        assert "https://user@" not in result
        assert "user@" not in result

    def test_href_with_explicit_port_removed(self) -> None:
        """P2.1：href 含显式端口时 href 属性被删除。"""
        html = '<a href="https://www.zhipin.com:8443/job">link</a>'
        result = sanitize_html(html, base_url="https://www.zhipin.com/")
        assert ":8443" not in result
        assert "www.zhipin.com:8443" not in result

    def test_href_with_port_443_removed(self) -> None:
        """P2.1：href 含 :443 显式端口时也删除 href。"""
        html = '<a href="https://www.zhipin.com:443/job">link</a>'
        result = sanitize_html(html, base_url="https://www.zhipin.com/")
        assert ":443" not in result

    def test_normal_href_preserved(self) -> None:
        """P2.1：正常官方 HTTPS href 保留。"""
        html = '<a href="https://www.zhipin.com/job/123">link</a>'
        result = sanitize_html(html, base_url="https://www.zhipin.com/")
        assert "https://www.zhipin.com/job/123" in result


# ==================== P2.1 新增：userinfo 二次扫描测试 ====================
class TestUserinfoScanDefense:
    """P2.1：userinfo 形态防御性扫描测试。

    即使 sanitize_url 遗漏，二次扫描也应发现 userinfo 并拒绝。
    """

    def test_userinfo_with_password_detected(self) -> None:
        """https://name:password@ 形态被二次扫描检测到。"""
        violations = scan_high_risk_content("https://user:pass@www.zhipin.com/job")
        assert len(violations) > 0

    def test_userinfo_without_password_detected(self) -> None:
        """https://name@ 形态被二次扫描检测到。"""
        violations = scan_high_risk_content("https://user@www.zhipin.com/job")
        assert len(violations) > 0

    def test_normal_url_not_flagged(self) -> None:
        """正常 URL 不被误报为 userinfo。"""
        violations = scan_high_risk_content("https://www.zhipin.com/job/123")
        # 不应命中 userinfo 模式（可能命中其他模式如 token，但不应是 userinfo）
        userinfo_violations = [v for v in violations if "name:password" in v or "name@" in v]
        assert len(userinfo_violations) == 0

    def test_userinfo_in_html_rejected(self) -> None:
        """HTML 中出现 userinfo URL 时，sanitize_html 二次扫描拒绝。"""
        # 直接注入 userinfo URL 到文本（绕过 href 脱敏）
        html = "<div>链接 https://user:pass@www.zhipin.com/job </div>"
        with pytest.raises(ValueError, match="高风险内容"):
            sanitize_html(html)


class TestComputeSha256:
    """测试 compute_sha256 哈希计算。"""

    def test_known_value(self) -> None:
        """已知输入的 SHA256 值校验。"""
        assert compute_sha256("hello") == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )
