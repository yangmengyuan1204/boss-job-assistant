"""P2 集中式选择器候选定义。

设计原则：
- 所有 CSS 选择器集中管理，不散落在解析代码里
- 每个字段允许多个候选选择器，按优先级尝试
- 选择器分层：页面级 / 卡片级 / 字段级 / 状态页级
- dataclass(frozen=True) 保证不可变

重要声明：
- 这些选择器是基于公开页面结构的候选，BOSS 直聘前端结构可能随时变化
- P2 阶段允许选择器不命中，解析器必须对缺失字段容错
- 不得为了填满字段而猜值
- 这些选择器仅用于读取公开可见 DOM，不调用任何未公开接口
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectorCandidate:
    """单个字段的选择器候选。

    Attributes:
        name: 字段名（与 ObservedJobCard / ObservedJobDetail 字段对应）
        selectors: 候选 CSS 选择器元组，按优先级尝试
        required: 是否必填（缺失记入 missing_required_fields）
        multiple: 是否允许多值（如 benefits/tags）
    """

    name: str
    selectors: tuple[str, ...]
    required: bool = False
    multiple: bool = False


# ==================== 选择器版本 ====================
SELECTOR_VERSION = "p2-v1"

# ==================== 页面级选择器 ====================
# 用于页面类型识别与根容器定位
PAGE_LEVEL_SELECTORS: dict[str, tuple[str, ...]] = {
    # 搜索列表页根容器候选
    "search_list_root": (
        "ul.job-list-box",
        "div.job-list-box",
        "div.search-job-result",
        "ul.job-list",
    ),
    # 单个岗位卡片候选
    "job_card": (
        "li.job-card-wrapper",
        "li.job-card",
        "div.job-card-wrapper",
        "div.job-card",
    ),
    # 详情页主容器候选
    "detail_root": (
        "div.job-detail",
        "div.job-box",
        "div.detail-content",
        "div.job-detail-section",
    ),
    # 空结果页候选
    "empty_results": (
        "div.empty-job-list",
        "div.no-result",
        "div.empty-result",
    ),
    # 登录页候选
    "login_page": (
        "div.login-box",
        "div.sign-wrap",
        "form#loginForm",
        "div.login-form",
    ),
    # 验证页候选（滑块/验证码容器）
    "verification_page": (
        "div.captcha",
        "div.slider",
        "div.verify-wrap",
        "div.nc-container",
        "div.bounce",
    ),
    # 错误页候选
    "error_page": (
        "div.error-page",
        "div.error-wrap",
        "div.not-found",
    ),
    # 首页候选
    "home_page": (
        "div.home-wrap",
        "div.index-banner",
        "main.home-main",
    ),
}


# ==================== 列表页卡片字段级选择器 ====================
LIST_CARD_FIELD_SELECTORS: tuple[SelectorCandidate, ...] = (
    SelectorCandidate(
        name="job_name",
        selectors=(
            "span.job-name",
            "a.job-name",
            "h3.job-name",
            "span.job-title",
        ),
        required=True,
    ),
    SelectorCandidate(
        name="job_url",
        selectors=(
            "a.job-card-left",
            "a.job-name",
            "a[href*='/job_detail/']",
            "a.job-title",
        ),
        required=True,
    ),
    SelectorCandidate(
        name="salary_text",
        selectors=(
            "span.salary",
            "span.red",
            "div.salary",
            "span.job-salary",
        ),
    ),
    SelectorCandidate(
        name="area_text",
        selectors=(
            "span.job-area",
            "span.area",
            "span.job-area-text",
        ),
    ),
    SelectorCandidate(
        name="experience_text",
        selectors=(
            "span.job-experience",
            "span.experience",
            "li.experience",
        ),
    ),
    SelectorCandidate(
        name="education_text",
        selectors=(
            "span.job-edu",
            "span.education",
            "li.education",
        ),
    ),
    SelectorCandidate(
        name="company_name",
        selectors=(
            "span.company-name",
            "a.company-name",
            "h3.company-name",
        ),
    ),
    SelectorCandidate(
        name="company_url",
        selectors=(
            "a.company-name",
            "a[href*='/company/']",
            "a.comp-name",
        ),
    ),
    SelectorCandidate(
        name="company_industry",
        selectors=(
            "span.company-industry",
            "span.industry",
            "div.company-text span",
        ),
    ),
    SelectorCandidate(
        name="company_size",
        selectors=(
            "span.company-size",
            "span.scale",
            "div.company-text span.scale",
        ),
    ),
    SelectorCandidate(
        name="recruiter_name",
        selectors=(
            "span.recruiter-name",
            "span.boss-name",
            "a.recruiter-name",
            "div.recruiter-name",
        ),
    ),
    SelectorCandidate(
        name="recruiter_title",
        selectors=(
            "span.recruiter-title",
            "span.boss-title",
            "div.recruiter-title",
        ),
    ),
    SelectorCandidate(
        name="recruiter_active_text",
        selectors=(
            "span.recruiter-active",
            "span.boss-active",
            "div.recruiter-active",
        ),
    ),
    SelectorCandidate(
        name="benefits",
        selectors=(
            "div.job-tags span",
            "div.tag-list span",
            "span.job-tag",
        ),
        multiple=True,
    ),
    SelectorCandidate(
        name="tags",
        selectors=(
            "div.job-info-tags span",
            "div.tags span",
            "span.info-tag",
        ),
        multiple=True,
    ),
)


# ==================== 详情页字段级选择器 ====================
DETAIL_FIELD_SELECTORS: tuple[SelectorCandidate, ...] = (
    SelectorCandidate(
        name="job_name",
        selectors=(
            "h1.job-name",
            "h1.name",
            "div.job-name h1",
            "div.info-primary h1",
        ),
        required=True,
    ),
    SelectorCandidate(
        name="salary_text",
        selectors=(
            "span.salary",
            "span.red",
            "div.salary",
            "div.info-primary span.salary",
        ),
    ),
    SelectorCandidate(
        name="location_text",
        selectors=(
            "span.job-area",
            "span.location",
            "div.job-area",
            "div.info-primary p",
        ),
    ),
    SelectorCandidate(
        name="experience_text",
        selectors=(
            "span.job-experience",
            "span.experience",
            "li.experience",
            "div.job-tags span.experience",
        ),
    ),
    SelectorCandidate(
        name="education_text",
        selectors=(
            "span.job-edu",
            "span.education",
            "li.education",
            "div.job-tags span.education",
        ),
    ),
    SelectorCandidate(
        name="description",
        selectors=(
            "div.job-detail-section",
            "div.job-sec-text",
            "div.text",
            "div.job-detail-content",
            "div.job-detail",
        ),
    ),
    SelectorCandidate(
        name="address_text",
        selectors=(
            "div.job-addr-text",
            "span.job-address",
            "div.location-address",
        ),
    ),
    SelectorCandidate(
        name="company_name",
        selectors=(
            "a.company-name",
            "h3.company-name",
            "div.company-info a",
        ),
    ),
    SelectorCandidate(
        name="company_industry",
        selectors=(
            "span.company-industry",
            "div.company-text span",
        ),
    ),
    SelectorCandidate(
        name="company_size",
        selectors=(
            "span.company-size",
            "div.company-text span.scale",
        ),
    ),
    SelectorCandidate(
        name="recruiter_name",
        selectors=(
            "span.recruiter-name",
            "a.recruiter-name",
            "div.recruiter-name",
        ),
    ),
    SelectorCandidate(
        name="recruiter_title",
        selectors=(
            "span.recruiter-title",
            "div.recruiter-title",
        ),
    ),
    SelectorCandidate(
        name="recruiter_active_text",
        selectors=(
            "span.recruiter-active",
            "div.recruiter-active",
        ),
    ),
    SelectorCandidate(
        name="publish_or_active_text",
        selectors=(
            "span.publish-time",
            "span.last-modify-time",
            "div.job-detail publishtime",
            "span.active-time",
        ),
    ),
    SelectorCandidate(
        name="benefits",
        selectors=(
            "div.job-tags span",
            "div.tag-list span",
            "div.job-detail-tags span",
        ),
        multiple=True,
    ),
    SelectorCandidate(
        name="tags",
        selectors=(
            "div.job-info-tags span",
            "div.tags span",
        ),
        multiple=True,
    ),
)


# ==================== 便捷查找 ====================
def get_list_card_selector(name: str) -> SelectorCandidate | None:
    """按字段名查找列表页卡片选择器候选。"""
    for s in LIST_CARD_FIELD_SELECTORS:
        if s.name == name:
            return s
    return None


def get_detail_field_selector(name: str) -> SelectorCandidate | None:
    """按字段名查找详情页字段选择器候选。"""
    for s in DETAIL_FIELD_SELECTORS:
        if s.name == name:
            return s
    return None


__all__ = [
    "SelectorCandidate",
    "SELECTOR_VERSION",
    "PAGE_LEVEL_SELECTORS",
    "LIST_CARD_FIELD_SELECTORS",
    "DETAIL_FIELD_SELECTORS",
    "get_list_card_selector",
    "get_detail_field_selector",
]
