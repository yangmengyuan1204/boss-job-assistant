"""P7 HTML 报告渲染器。

安全策略：
- 所有动态文本使用 html.escape(text, quote=True) 转义
- 所有属性值使用 html.escape(val, quote=True) 转义
- 所有链接复用 P2 sanitize_url() 防御性校验
- 合法链接 <a href="..." target="_blank" rel="noopener noreferrer">
- 无安全链接时显示「链接不可用」
- UTF-8 编码，无外部 JS/CSS/CDN/字体/图片
- 内联 CSS + 内联简单 JS（筛选功能）
- 不允许 innerHTML 插入未转义数据
- 不允许模板注入
- 不使用模板引擎依赖
- 描述摘要策略：默认最多 300 字符，保留换行语义，超长显示省略号，
  使用 <details><summary> 展开完整已脱敏描述
- 最终安全扫描：发现 Cookie/Token/securityId/Authorization/手机号/邮箱时脱敏
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from boss_tool.logging_config import get_logger
from boss_tool.parsers.sanitization import sanitize_text, sanitize_url
from boss_tool.report import constants as C
from boss_tool.report.models import (
    ReportJob,
    ReportMetadata,
    ReportSection,
    ReportSummary,
)

logger = get_logger(__name__)


class HTMLRenderer:
    """单文件 HTML 报告渲染器。

    所有动态内容均经过 html.escape 转义，所有链接均经过 sanitize_url 校验。
    生成的 HTML 为单文件，内联 CSS 与 JS，无外部资源依赖。

    Usage:
        renderer = HTMLRenderer()
        html_content = renderer.render(jobs, sections, summary, metadata)
        Path("report.html").write_text(html_content, encoding="utf-8")
    """

    def render(
        self,
        jobs: list[ReportJob],
        sections: list[ReportSection],
        summary: ReportSummary,
        metadata: ReportMetadata,
    ) -> str:
        """渲染完整 HTML 报告。

        Args:
            jobs: 全部岗位列表（已排序）
            sections: 四个分区列表
            summary: 汇总统计
            metadata: 报告元数据

        Returns:
            完整 HTML 字符串（单文件，UTF-8）
        """
        parts: list[str] = []
        parts.append(self._render_doctype_and_head(metadata))
        parts.append(self._render_body_open())
        parts.append(self._render_header(metadata))
        parts.append(self._render_summary_cards(summary))
        parts.append(self._render_filter_controls())
        parts.append(self._render_sections(sections))
        parts.append(self._render_footer(metadata))
        parts.append(self._render_script())
        parts.append("</body>\n</html>\n")

        html_content = "".join(parts)

        # 最终安全扫描：对动态内容再次脱敏（防御性）
        html_content = self._final_safety_scan(html_content)

        return html_content

    # ==================== HTML 头部 ====================
    def _render_doctype_and_head(self, metadata: ReportMetadata) -> str:
        """渲染 DOCTYPE 与 <head>（含内联 CSS）。"""
        title = self._escape_text("BOSS 直聘岗位推荐报告")
        generated_at = self._format_datetime(metadata.generated_at)

        return (
            "<!DOCTYPE html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            f"<title>{title}</title>\n"
            f"{self._render_inline_css()}\n"
            "</head>\n"
            f"<!-- 生成时间: {generated_at} -->\n"
        )

    def _render_inline_css(self) -> str:
        """渲染内联 CSS（无外部资源）。"""
        return """<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    background: #f5f6fa;
    color: #2c3e50;
    line-height: 1.6;
    padding: 20px;
}
.container { max-width: 1200px; margin: 0 auto; }
.report-header {
    background: linear-gradient(135deg, #1a5276, #2980b9);
    color: #fff;
    padding: 24px 28px;
    border-radius: 10px;
    margin-bottom: 20px;
}
.report-header h1 { font-size: 24px; margin-bottom: 8px; }
.report-header .meta { font-size: 13px; opacity: 0.9; }
.report-header .meta div { margin: 4px 0; }
.summary-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
}
.card {
    background: #fff;
    padding: 16px;
    border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-left: 4px solid #2980b9;
}
.card .label { font-size: 12px; color: #7f8c8d; margin-bottom: 4px; }
.card .value { font-size: 22px; font-weight: bold; color: #2c3e50; }
.card.green { border-left-color: #27ae60; }
.card.blue { border-left-color: #2980b9; }
.card.yellow { border-left-color: #f39c12; }
.card.red { border-left-color: #c0392b; }
.filter-bar {
    background: #fff;
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 20px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
}
.filter-bar input[type="text"] {
    padding: 8px 12px;
    border: 1px solid #dcdde1;
    border-radius: 4px;
    font-size: 14px;
    flex: 1;
    min-width: 200px;
}
.filter-bar select {
    padding: 8px 12px;
    border: 1px solid #dcdde1;
    border-radius: 4px;
    font-size: 14px;
    background: #fff;
}
.filter-bar button {
    padding: 8px 16px;
    background: #2980b9;
    color: #fff;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
}
.filter-bar button:hover { background: #21618c; }
.section {
    background: #fff;
    border-radius: 8px;
    margin-bottom: 20px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.section-header {
    padding: 14px 20px;
    color: #fff;
    font-size: 16px;
    font-weight: bold;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.section-header .count {
    background: rgba(255,255,255,0.25);
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 13px;
}
.job-card {
    border-bottom: 1px solid #ecf0f1;
    padding: 16px 20px;
    transition: background 0.2s;
}
.job-card:hover { background: #f8f9fa; }
.job-card.hidden { display: none; }
.job-title {
    font-size: 16px;
    font-weight: bold;
    color: #2c3e50;
    margin-bottom: 6px;
}
.job-title a {
    color: #2980b9;
    text-decoration: none;
    margin-left: 8px;
    font-size: 13px;
    font-weight: normal;
}
.job-title a:hover { text-decoration: underline; }
.job-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    font-size: 13px;
    color: #7f8c8d;
    margin-bottom: 8px;
}
.job-meta .salary { color: #e74c3c; font-weight: bold; }
.job-meta .tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 12px;
    background: #ecf0f1;
    color: #2c3e50;
}
.job-meta .tag.green { background: #d5f5e3; color: #1e8449; }
.job-meta .tag.blue { background: #d6eaf8; color: #21618c; }
.job-meta .tag.yellow { background: #fcf3cf; color: #b7950b; }
.job-meta .tag.red { background: #fadbd8; color: #cb4335; }
.job-desc {
    font-size: 13px;
    color: #34495e;
    margin-top: 8px;
    white-space: pre-wrap;
}
.job-desc summary {
    cursor: pointer;
    color: #2980b9;
    font-size: 13px;
    margin-bottom: 4px;
}
.job-explain {
    margin-top: 8px;
    padding: 8px 12px;
    background: #f8f9fa;
    border-left: 3px solid #bdc3c7;
    font-size: 12px;
    color: #566573;
    border-radius: 0 4px 4px 0;
}
.job-explain ul { margin-left: 16px; }
.job-explain li { margin: 2px 0; }
.job-warnings {
    margin-top: 6px;
    padding: 6px 10px;
    background: #fef9e7;
    border-left: 3px solid #f39c12;
    font-size: 12px;
    color: #b7950b;
    border-radius: 0 4px 4px 0;
}
.footer {
    text-align: center;
    padding: 20px;
    color: #95a5a6;
    font-size: 12px;
    margin-top: 20px;
}
.empty-section {
    padding: 20px;
    text-align: center;
    color: #95a5a6;
    font-size: 14px;
}
@media (max-width: 768px) {
    .summary-cards { grid-template-columns: repeat(2, 1fr); }
    .filter-bar { flex-direction: column; align-items: stretch; }
}
</style>"""

    # ==================== HTML 主体 ====================
    def _render_body_open(self) -> str:
        return '<body>\n<div class="container">\n'

    def _render_header(self, metadata: ReportMetadata) -> str:
        """渲染报告头部。"""
        generated_at = self._format_datetime(metadata.generated_at)
        db_filename = self._escape_text(metadata.db_filename or "未指定")
        rule_version = self._escape_text(metadata.rule_version)
        reference_location = self._escape_text(metadata.reference_location)
        distance_km = self._escape_text(C.DISTANCE_THRESHOLD_KM_TEXT)
        candidate_age = metadata.candidate_age

        return (
            '<header class="report-header">\n'
            f"  <h1>{self._escape_text('BOSS 直聘岗位推荐报告')}</h1>\n"
            '  <div class="meta">\n'
            f"    <div>生成时间：{generated_at}</div>\n"
            f"    <div>数据库：{db_filename}</div>\n"
            f"    <div>规则版本：{rule_version}</div>\n"
            f"    <div>参考地点：{reference_location}</div>\n"
            f"    <div>距离阈值：{distance_km}</div>\n"
            f"    <div>候选人年龄：{candidate_age} 岁</div>\n"
            "  </div>\n"
            "</header>\n"
        )

    def _render_summary_cards(self, summary: ReportSummary) -> str:
        """渲染汇总统计卡片。"""
        cards = [
            ("岗位总数", summary.total, ""),
            ("强烈推荐", summary.strongly_recommend_count, "green"),
            ("可考虑", summary.consider_count, "blue"),
            ("待人工确认", summary.manual_review_count, "yellow"),
            ("不符合", summary.not_match_count, "red"),
            ("适合 60 岁", summary.eligible_count, "green"),
            ("3 公里内", summary.within_3km_count, "blue"),
            ("有详情数据", summary.detail_source_count, ""),
        ]

        parts: list[str] = ['<div class="summary-cards">\n']
        for label, value, color_class in cards:
            escaped_label = self._escape_text(label)
            color_cls = f" {color_class}" if color_class else ""
            parts.append(
                f'  <div class="card{color_cls}">\n'
                f'    <div class="label">{escaped_label}</div>\n'
                f'    <div class="value">{value}</div>\n'
                "  </div>\n"
            )
        parts.append("</div>\n")
        return "".join(parts)

    def _render_filter_controls(self) -> str:
        """渲染筛选控件（内联 JS 处理筛选）。"""
        return (
            '<div class="filter-bar">\n'
            '  <input type="text" id="filter-input" placeholder="输入关键词筛选岗位（标题/公司/地点）..."\n'
            '         oninput="filterJobs()">\n'
            '  <select id="section-filter" onchange="filterJobs()">\n'
            '    <option value="all">全部分区</option>\n'
            '    <option value="strongly_recommend">强烈推荐</option>\n'
            '    <option value="consider">可考虑</option>\n'
            '    <option value="manual_review">待人工确认</option>\n'
            '    <option value="not_match">不符合</option>\n'
            "  </select>\n"
            '  <button onclick="resetFilter()">重置</button>\n'
            "</div>\n"
        )

    def _render_sections(self, sections: list[ReportSection]) -> str:
        """渲染四个分区。"""
        parts: list[str] = []
        for section in sections:
            parts.append(self._render_section(section))
        return "".join(parts)

    def _render_section(self, section: ReportSection) -> str:
        """渲染单个分区。"""
        # section_type 在 use_enum_values=True 时为字符串
        section_type = section.section_type
        if hasattr(section_type, "value"):
            section_type = section_type.value

        title = self._escape_text(section.title)
        color = self._escape_attr(section.color)
        count = section.count

        parts: list[str] = [
            f'<section class="section" data-section-type="{section_type}">\n'
            f'  <div class="section-header" style="background:{color}">\n'
            f"    <span>{title}</span>\n"
            f'    <span class="count">{count} 个岗位</span>\n'
            "  </div>\n"
        ]

        if not section.jobs:
            parts.append('  <div class="empty-section">该分区暂无岗位</div>\n')
        else:
            for job in section.jobs:
                parts.append(self._render_job_card(job))

        parts.append("</section>\n")
        return "".join(parts)

    def _render_job_card(self, job: ReportJob) -> str:
        """渲染单条岗位卡片。"""
        # 标题与链接
        title = self._escape_text(job.title or "（无标题）")
        job_url_html = self._render_link(job.job_url, "查看原岗位")

        # 元信息标签
        meta_parts: list[str] = []
        if job.salary:
            meta_parts.append(f'<span class="salary">{self._escape_text(job.salary)}</span>')
        if job.company:
            meta_parts.append(f"<span>公司：{self._escape_text(job.company)}</span>")
        if job.location:
            meta_parts.append(f"<span>地点：{self._escape_text(job.location)}</span>")
        if job.experience:
            meta_parts.append(f"<span>经验：{self._escape_text(job.experience)}</span>")
        if job.education:
            meta_parts.append(f"<span>学历：{self._escape_text(job.education)}</span>")
        if job.job_category:
            cat_cn = C.JOB_CATEGORY_CN.get(job.job_category, job.job_category)
            meta_parts.append(f'<span class="tag">{self._escape_text(cat_cn)}</span>')
        if job.recommend_level:
            level_cn = C.RECOMMEND_LEVEL_CN.get(job.recommend_level, job.recommend_level)
            color_cls = self._level_color_class(job.recommend_level)
            meta_parts.append(f'<span class="tag {color_cls}">{self._escape_text(level_cn)}</span>')
        if job.distance_meter is not None:
            distance_km = job.distance_meter / 1000.0
            meta_parts.append(f"<span>距离：{distance_km:.2f} 公里</span>")
        else:
            meta_parts.append('<span class="tag">距离：未知</span>')
        if job.recruiter_active:
            active_cn = (
                C.ACTIVITY_LEVEL_CN.get(job.recruiter_active_level or "", job.recruiter_active)
                if job.recruiter_active_level
                else job.recruiter_active
            )
            meta_parts.append(f"<span>招聘者：{self._escape_text(active_cn)}</span>")

        # 年龄适配标签
        age_fit_str = self._get_age_fit_str(job)
        age_fit_cn = C.AGE_FIT_CN.get(age_fit_str, age_fit_str)
        age_color_cls = self._age_fit_color_class(age_fit_str)
        meta_parts.append(
            f'<span class="tag {age_color_cls}">{self._escape_text(age_fit_cn)}</span>'
        )

        # 数据来源
        data_source_cn = C.DATA_SOURCE_CN.get(job.data_source, job.data_source)
        meta_parts.append(f'<span class="tag">{self._escape_text(data_source_cn)}</span>')

        meta_html = "\n    ".join(meta_parts)

        # 描述
        desc_html = self._render_description(job.description)

        # 解释与警告
        explain_html = self._render_explanations(job)
        warnings_html = self._render_warnings(job)

        return (
            f'<div class="job-card" data-search-text="{self._escape_attr(self._search_text(job))}">\n'
            f'  <div class="job-title">{title}{job_url_html}</div>\n'
            '  <div class="job-meta">\n'
            f"    {meta_html}\n"
            "  </div>\n"
            f"  {desc_html}\n"
            f"  {explain_html}\n"
            f"  {warnings_html}\n"
            "</div>\n"
        )

    def _render_link(self, url: str | None, text: str) -> str:
        """渲染安全链接。

        复用 P2 sanitize_url 防御性校验。
        合法链接 <a href="..." target="_blank" rel="noopener noreferrer">
        无安全链接时显示「链接不可用」
        """
        safe_url = sanitize_url(url)
        if not safe_url:
            return f'<span style="color:#95a5a6;font-size:13px;">{self._escape_text("链接不可用")}</span>'
        escaped_url = self._escape_attr(safe_url)
        escaped_text = self._escape_text(text)
        return (
            f'<a href="{escaped_url}" target="_blank" rel="noopener noreferrer">{escaped_text}</a>'
        )

    def _render_description(self, description: str | None) -> str:
        """渲染岗位描述（带摘要与展开）。"""
        if not description:
            return ""

        # 对描述再次脱敏（防御性）
        sanitized = sanitize_text(description) or ""

        # 摘要：截断到 300 字符
        if len(sanitized) <= C.DESCRIPTION_SUMMARY_MAX_LENGTH:
            # 短描述直接显示
            escaped = self._escape_text(sanitized)
            return f'<div class="job-desc">{escaped}</div>'

        # 长描述：摘要 + 展开
        summary_text = sanitized[: C.DESCRIPTION_SUMMARY_MAX_LENGTH] + "..."
        escaped_summary = self._escape_text(summary_text)
        escaped_full = self._escape_text(sanitized)
        return (
            '<details class="job-desc">\n'
            f"  <summary>{escaped_summary}</summary>\n"
            f"  {escaped_full}\n"
            "</details>\n"
        )

    def _render_explanations(self, job: ReportJob) -> str:
        """渲染规则解释。"""
        lines: list[str] = []
        if job.explanations:
            lines.append('<div class="job-explain">')
            lines.append("  <ul>")
            for exp in job.explanations:
                lines.append(f"    <li>{self._escape_text(exp)}</li>")
            lines.append("  </ul>")
            lines.append("</div>")
        return "\n".join(lines)

    def _render_warnings(self, job: ReportJob) -> str:
        """渲染警告。"""
        if not job.warnings:
            return ""
        lines: list[str] = ['<div class="job-warnings">']
        for w in job.warnings:
            lines.append(f"  <div>{self._escape_text(w)}</div>")
        lines.append("</div>")
        return "\n".join(lines)

    def _render_footer(self, metadata: ReportMetadata) -> str:
        """渲染页脚（含安全声明）。"""
        safety = self._escape_text(metadata.safety_statement or C.SAFETY_STATEMENT)
        return (
            '<footer class="footer">\n'
            f"  <div>{safety}</div>\n"
            '  <div style="margin-top:8px;">本报告由 boss-tool 离线生成，不访问网络</div>\n'
            "</footer>\n"
            "</div>\n"
        )

    def _render_script(self) -> str:
        """渲染内联 JS（筛选功能）。"""
        return """<script>
function filterJobs() {
    var input = document.getElementById('filter-input').value.toLowerCase();
    var sectionFilter = document.getElementById('section-filter').value;
    var cards = document.querySelectorAll('.job-card');
    cards.forEach(function(card) {
        var searchText = card.getAttribute('data-search-text') || '';
        searchText = searchText.toLowerCase();
        var section = card.closest('.section');
        var sectionType = section.getAttribute('data-section-type') || '';
        var matchText = input === '' || searchText.indexOf(input) >= 0;
        var matchSection = sectionFilter === 'all' || sectionType === sectionFilter;
        if (matchText && matchSection) {
            card.classList.remove('hidden');
        } else {
            card.classList.add('hidden');
        }
    });
    // 隐藏空分区
    var sections = document.querySelectorAll('.section');
    sections.forEach(function(s) {
        var visible = s.querySelectorAll('.job-card:not(.hidden)').length;
        var header = s.querySelector('.section-header');
        var empty = s.querySelector('.empty-section');
        if (sectionFilter !== 'all') {
            var sectionType = s.getAttribute('data-section-type') || '';
            if (sectionType !== sectionFilter) {
                s.style.display = 'none';
                return;
            }
        }
        s.style.display = '';
    });
}
function resetFilter() {
    document.getElementById('filter-input').value = '';
    document.getElementById('section-filter').value = 'all';
    filterJobs();
}
</script>"""

    # ==================== 辅助方法 ====================
    @staticmethod
    def _escape_text(text: str | None) -> str:
        """转义动态文本（html.escape）。"""
        if text is None:
            return ""
        return html.escape(str(text), quote=True)

    @staticmethod
    def _escape_attr(value: str | None) -> str:
        """转义属性值（html.escape with quote=True）。"""
        if value is None:
            return ""
        return html.escape(str(value), quote=True)

    @staticmethod
    def _format_datetime(dt: datetime) -> str:
        """格式化日期时间为本地可读格式。"""
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _get_age_fit_str(job: ReportJob) -> str:
        """获取年龄适配字符串值。"""
        fit = job.candidate_age_fit
        if hasattr(fit, "value"):
            return fit.value
        return str(fit)

    @staticmethod
    def _level_color_class(level: str | None) -> str:
        """推荐等级对应的颜色 CSS 类。"""
        if level == "A":
            return "green"
        if level == "B":
            return "blue"
        if level == "C":
            return "yellow"
        if level == "D":
            return "red"
        return ""

    @staticmethod
    def _age_fit_color_class(age_fit: str) -> str:
        """年龄适配对应的颜色 CSS 类。"""
        if age_fit == "eligible":
            return "green"
        if age_fit == "review":
            return "yellow"
        if age_fit == "ineligible":
            return "red"
        return "blue"

    @staticmethod
    def _search_text(job: ReportJob) -> str:
        """构造搜索文本（用于 JS 筛选）。"""
        parts: list[str] = []
        if job.title:
            parts.append(job.title)
        if job.company:
            parts.append(job.company)
        if job.location:
            parts.append(job.location)
        if job.job_category:
            parts.append(job.job_category)
        return " ".join(parts)

    def _final_safety_scan(self, html_content: str) -> str:
        """最终安全扫描：对动态内容再次脱敏。

        扫描已生成 HTML 中的高风险内容（Cookie/Token/securityId/Authorization/
        手机号/邮箱），命中则替换为占位符。

        注意：此扫描针对动态内容，内联 CSS/JS 中的合法关键字不受影响
        （sanitize_text 仅匹配特定格式的手机号/邮箱/token，不匹配普通英文单词）。
        """
        # 复用 P2 sanitize_text 对整体内容脱敏
        # sanitize_text 会替换手机号/邮箱/身份证/长 token
        return sanitize_text(html_content) or html_content


def render_report(
    jobs: list[ReportJob],
    sections: list[ReportSection],
    summary: ReportSummary,
    metadata: ReportMetadata,
) -> str:
    """渲染 HTML 报告（便捷函数）。

    Args:
        jobs: 全部岗位列表（已排序）
        sections: 四个分区列表
        summary: 汇总统计
        metadata: 报告元数据

    Returns:
        完整 HTML 字符串
    """
    renderer = HTMLRenderer()
    return renderer.render(jobs, sections, summary, metadata)


def save_report(html_content: str, output_path: Path) -> Path:
    """保存 HTML 报告到文件。

    Args:
        html_content: HTML 字符串
        output_path: 输出路径

    Returns:
        实际保存路径
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    logger.info("报告已保存: %s", output_path)
    return output_path


__all__ = [
    "HTMLRenderer",
    "render_report",
    "save_report",
]
