"""P7 HTML 渲染器安全测试。

覆盖：
- XSS 防御：动态文本 html.escape 转义
- 脚本注入防御：<script> 标签转义
- 属性注入防御：属性值转义
- URL 安全校验：sanitize_url 拒绝非 BOSS 域名
- URL 安全校验：HTTP 拒绝
- URL 安全校验：javascript: 拒绝
- 无外部资源：无 CDN/外部 JS/CSS
- 单文件 HTML：内联 CSS/JS
- 描述长度截断
- 手机号/邮箱脱敏
- 空数据渲染不崩溃
- 完整报告渲染
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from boss_tool.report.age_fit import CandidateAgeFit
from boss_tool.report.models import (
    ReportJob,
    ReportMetadata,
    ReportSection,
    ReportSectionType,
    ReportSummary,
)
from boss_tool.report.renderer import HTMLRenderer, render_report, save_report


def _make_job(
    job_id: str = "job-001",
    title: str | None = "小区保洁",
    company: str | None = "示例物业",
    description: str | None = "岗位描述",
    job_url: str | None = "https://www.zhipin.com/job_detail/123.html",
) -> ReportJob:
    """构造测试用 ReportJob。"""
    return ReportJob(
        job_id=job_id,
        title=title,
        company=company,
        description=description,
        job_url=job_url,
        candidate_age_fit=CandidateAgeFit.ELIGIBLE,
        within_3km=True,
        recommend_level="A",
        score=90,
    )


def _make_metadata() -> ReportMetadata:
    """构造测试用 ReportMetadata。"""
    return ReportMetadata(
        db_filename="test.db",
        rule_version="P6 v1",
        reference_location="杭州市拱墅区锦园小区",
        candidate_age=60,
        safety_statement="安全声明",
    )


def _render_basic(jobs: list[ReportJob] | None = None) -> str:
    """渲染基本报告。"""
    if jobs is None:
        jobs = [_make_job()]
    sections = [
        ReportSection(
            section_type=ReportSectionType.STRONGLY_RECOMMEND,
            title="强烈推荐",
            color="#27ae60",
            jobs=jobs,
        ),
        ReportSection(section_type=ReportSectionType.CONSIDER, title="可考虑", color="#2980b9"),
        ReportSection(
            section_type=ReportSectionType.MANUAL_REVIEW, title="待人工确认", color="#f39c12"
        ),
        ReportSection(section_type=ReportSectionType.NOT_MATCH, title="不符合", color="#c0392b"),
    ]
    summary = ReportSummary(total=len(jobs), strongly_recommend_count=len(jobs))
    metadata = _make_metadata()
    renderer = HTMLRenderer()
    return renderer.render(jobs, sections, summary, metadata)


# ==================== XSS 防御 ====================
class TestXSSDefense:
    """XSS 防御测试。"""

    def test_script_tag_escaped_in_title(self) -> None:
        """标题中的 <script> 标签被转义。"""
        job = _make_job(title="<script>alert('xss')</script>")
        html = _render_basic([job])
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_script_tag_escaped_in_company(self) -> None:
        """公司名中的 <script> 标签被转义。"""
        job = _make_job(company="<script>alert('xss')</script>")
        html = _render_basic([job])
        assert "<script>alert" not in html

    def test_script_tag_escaped_in_description(self) -> None:
        """描述中的 <script> 标签被转义。"""
        job = _make_job(description="<script>alert('xss')</script>")
        html = _render_basic([job])
        assert "<script>alert" not in html

    def test_img_onerror_escaped(self) -> None:
        """img onerror 属性被转义。

        html.escape 将 < > 转义为 &lt; &gt;，使浏览器不会将其解析为标签。
        转义后的文本中 "onerror=alert" 作为纯文本出现是安全的（不可执行）。
        真正的安全检查是：原始 <img 标签不出现在 HTML 中。
        """
        job = _make_job(title="<img src=x onerror=alert(1)>")
        html = _render_basic([job])
        # 原始 <img 标签不应出现（已被 html.escape 转义为 &lt;img）
        assert "<img src=x" not in html
        assert "<img" not in html
        # 转义后的 &lt;img 应出现（作为纯文本展示，不可执行）
        assert "&lt;img" in html

    def test_html_entity_injection_defense(self) -> None:
        """HTML 实体注入防御。"""
        job = _make_job(title="&lt;script&gt;")
        html = _render_basic([job])
        # 应该被再次转义为 &amp;lt;
        assert "&amp;lt;" in html


class TestAttributeInjection:
    """属性注入防御。"""

    def test_quote_in_attribute_escaped(self) -> None:
        """属性值中的引号被转义。"""
        job = _make_job(
            title="正常", job_url='https://www.zhipin.com/job_detail/123.html" onclick="alert(1)'
        )
        html = _render_basic([job])
        # onclick 不应出现在 href 属性中
        assert 'onclick="alert' not in html


class TestURLSanitization:
    """URL 安全校验。"""

    def test_http_url_rejected(self) -> None:
        """HTTP URL 被拒绝。"""
        job = _make_job(job_url="http://www.zhipin.com/job_detail/123.html")
        html = _render_basic([job])
        assert "链接不可用" in html

    def test_non_boss_host_rejected(self) -> None:
        """非 BOSS 域名被拒绝。"""
        job = _make_job(job_url="https://evil.com/job_detail/123.html")
        html = _render_basic([job])
        assert "链接不可用" in html

    def test_javascript_url_rejected(self) -> None:
        """javascript: URL 被拒绝。"""
        job = _make_job(job_url="javascript:alert(1)")
        html = _render_basic([job])
        assert "链接不可用" in html

    def test_valid_boss_url_kept(self) -> None:
        """合法 BOSS URL 保留。"""
        job = _make_job(job_url="https://www.zhipin.com/job_detail/123.html")
        html = _render_basic([job])
        assert 'href="https://www.zhipin.com/job_detail/123.html"' in html

    def test_url_with_query_stripped(self) -> None:
        """URL query 被移除。"""
        job = _make_job(job_url="https://www.zhipin.com/job_detail/123.html?token=secret")
        html = _render_basic([job])
        assert "token=secret" not in html

    def test_url_with_fragment_stripped(self) -> None:
        """URL fragment 被移除。"""
        job = _make_job(job_url="https://www.zhipin.com/job_detail/123.html#section")
        html = _render_basic([job])
        assert "#section" not in html.split('href="')[1].split('"')[0] if 'href="' in html else True

    def test_link_has_noopener_noreferrer(self) -> None:
        """链接包含 rel="noopener noreferrer"。"""
        job = _make_job(job_url="https://www.zhipin.com/job_detail/123.html")
        html = _render_basic([job])
        assert 'rel="noopener noreferrer"' in html

    def test_link_has_target_blank(self) -> None:
        """链接包含 target="_blank"。"""
        job = _make_job(job_url="https://www.zhipin.com/job_detail/123.html")
        html = _render_basic([job])
        assert 'target="_blank"' in html


class TestNoExternalResources:
    """无外部资源测试。"""

    def test_no_external_css(self) -> None:
        """无外部 CSS 链接。"""
        html = _render_basic()
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("link"):
            assert link.get("rel") != ["stylesheet"], f"发现外部 CSS: {link}"

    def test_no_external_js(self) -> None:
        """无外部 JS 引用。"""
        html = _render_basic()
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            src = script.get("src")
            assert src is None, f"发现外部 JS: {src}"

    def test_no_external_images(self) -> None:
        """无外部图片。"""
        html = _render_basic()
        soup = BeautifulSoup(html, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src")
            assert src is None or src.startswith("data:"), f"发现外部图片: {src}"

    def test_inline_css_present(self) -> None:
        """有内联 CSS。"""
        html = _render_basic()
        assert "<style>" in html

    def test_inline_js_present(self) -> None:
        """有内联 JS。"""
        html = _render_basic()
        assert "<script>" in html


class TestSingleFileHTML:
    """单文件 HTML 测试。"""

    def test_has_doctype(self) -> None:
        """有 DOCTYPE 声明。"""
        html = _render_basic()
        assert html.startswith("<!DOCTYPE html>")

    def test_has_html_tag(self) -> None:
        """有 <html> 标签。"""
        html = _render_basic()
        assert "<html" in html
        assert "</html>" in html

    def test_has_meta_charset(self) -> None:
        """有 charset 声明。"""
        html = _render_basic()
        assert 'charset="UTF-8"' in html

    def test_has_viewport(self) -> None:
        """有 viewport 声明。"""
        html = _render_basic()
        assert "viewport" in html


class TestDescriptionHandling:
    """描述处理测试。"""

    def test_short_description_displayed(self) -> None:
        """短描述直接显示。"""
        job = _make_job(description="短描述")
        html = _render_basic([job])
        assert "短描述" in html

    def test_long_description_truncated(self) -> None:
        """长描述被截断。"""
        long_desc = "a" * 500
        job = _make_job(description=long_desc)
        html = _render_basic([job])
        # 应该有 details/summary 用于展开
        assert "<details" in html or "..." in html

    def test_none_description_no_error(self) -> None:
        """None 描述不报错。"""
        job = _make_job(description=None)
        html = _render_basic([job])
        assert "<!DOCTYPE html>" in html


class TestSensitiveDataRedaction:
    """敏感数据脱敏测试。"""

    def test_phone_number_redacted(self) -> None:
        """手机号被脱敏。"""
        job = _make_job(description="联系电话：13812345678")
        html = _render_basic([job])
        assert "13812345678" not in html
        assert "REDACTED_PHONE" in html

    def test_email_redacted(self) -> None:
        """邮箱被脱敏。"""
        job = _make_job(description="邮箱：test@example.com")
        html = _render_basic([job])
        assert "test@example.com" not in html
        assert "REDACTED_EMAIL" in html


class TestEmptyDataRendering:
    """空数据渲染测试。"""

    def test_empty_jobs_no_crash(self) -> None:
        """空岗位列表不崩溃。"""
        html = _render_basic([])
        assert "<!DOCTYPE html>" in html

    def test_empty_section_shows_message(self) -> None:
        """空分区显示提示。"""
        html = _render_basic([])
        assert "该分区暂无岗位" in html

    def test_none_fields_no_crash(self) -> None:
        """所有字段为 None 不崩溃。"""
        job = ReportJob(job_id="empty-job")
        html = _render_basic([job])
        assert "<!DOCTYPE html>" in html


class TestFullReportRendering:
    """完整报告渲染测试。"""

    def test_render_returns_string(self) -> None:
        """render 返回字符串。"""
        html = _render_basic()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_render_contains_required_sections(self) -> None:
        """包含四个分区。"""
        html = _render_basic()
        assert "强烈推荐" in html
        assert "可考虑" in html
        assert "待人工确认" in html
        assert "不符合" in html

    def test_render_contains_summary_cards(self) -> None:
        """包含汇总卡片。"""
        html = _render_basic()
        assert "岗位总数" in html
        assert "强烈推荐" in html

    def test_render_contains_metadata(self) -> None:
        """包含元数据。"""
        html = _render_basic()
        assert "杭州市拱墅区锦园小区" in html
        assert "60" in html  # 候选人年龄

    def test_render_contains_safety_statement(self) -> None:
        """包含安全声明。"""
        html = _render_basic()
        assert "安全声明" in html


class TestSaveReport:
    """save_report 测试。"""

    def test_save_creates_file(self, tmp_path) -> None:
        """保存创建文件。"""
        html = "<html>test</html>"
        output = tmp_path / "report.html"
        saved = save_report(html, output)
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == html

    def test_save_creates_parent_dir(self, tmp_path) -> None:
        """保存创建父目录。"""
        html = "<html>test</html>"
        output = tmp_path / "subdir" / "report.html"
        saved = save_report(html, output)
        assert saved.exists()


class TestRenderReportFunction:
    """render_report 便捷函数测试。"""

    def test_render_report_function(self) -> None:
        """render_report 便捷函数工作正常。"""
        jobs = [_make_job()]
        sections = [
            ReportSection(
                section_type=ReportSectionType.STRONGLY_RECOMMEND,
                title="强烈推荐",
                color="#27ae60",
                jobs=jobs,
            )
        ]
        summary = ReportSummary(total=1, strongly_recommend_count=1)
        metadata = _make_metadata()
        html = render_report(jobs, sections, summary, metadata)
        assert "<!DOCTYPE html>" in html
        assert "小区保洁" in html
