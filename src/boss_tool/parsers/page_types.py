"""P2 页面类型识别（纯本地，基于 DOM 结构与 URL 路径）。

识别规则：
- 基于公开可见 DOM 结构和 URL 路径
- 不根据 Cookie 判断
- 不读取内部接口状态
- 不声明 100% 准确
- 输出证据和置信度
- 多个类型同时命中时保守返回 UNKNOWN 或最低置信结果
- 不得把"没有登录按钮"简单等价为"已登录"

本模块不依赖 Playwright，仅依赖 BeautifulSoup，便于 parse-fixture 复用。
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from boss_tool.models.observed_page import PageType, PageTypeDetection
from boss_tool.parsers.selectors import PAGE_LEVEL_SELECTORS

# ==================== URL 路径模式（用于辅助识别） ====================
# 注意：这些是基于公开 URL 结构的候选，BOSS 前端结构可能变化
_URL_PATTERNS: dict[PageType, tuple[str, ...]] = {
    PageType.SEARCH_LIST: (
        "/web/geek/job",
        "/web/geek/recommend",
        "/job_list",
        "/web/geek/job-list",
    ),
    PageType.JOB_DETAIL: (
        "/job_detail/",
        "/job-detail/",
        "/web/geek/job-detail",
    ),
    PageType.LOGIN: (
        "/login",
        "/sign",
        "/account/login",
        "/web/common/security-check",
    ),
    PageType.HOME: (
        "/",
        "/index",
    ),
}


def _match_url_path(url: str | None) -> list[tuple[PageType, str, float]]:
    """根据 URL 路径返回候选类型与置信度。"""
    if not url:
        return []
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return []
    path = (parsed.path or "").rstrip("/")
    if not path and parsed.netloc:
        # 根路径（有 host 但 path 为空或 /），可能是首页
        return [(PageType.HOME, f"URL path 为根路径: {parsed.netloc}", 0.4)]
    results: list[tuple[PageType, str, float]] = []
    for page_type, patterns in _URL_PATTERNS.items():
        for pat in patterns:
            if path == pat.rstrip("/") or path.startswith(pat.rstrip("/") + "/"):
                results.append((page_type, f"URL path 命中模式 {pat!r} (path={path})", 0.6))
                break
    return results


def _match_dom(soup: BeautifulSoup) -> list[tuple[PageType, str, float]]:
    """根据 DOM 结构返回候选类型与置信度。"""
    results: list[tuple[PageType, str, float]] = []

    # 列表页：同时存在 list_root 和至少一个 job_card
    list_root_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["search_list_root"])
    card_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["job_card"])
    if list_root_hits > 0 and card_hits > 0:
        results.append(
            (
                PageType.SEARCH_LIST,
                f"DOM 命中列表页根容器({list_root_hits}) 且 岗位卡片({card_hits})",
                0.85,
            )
        )
    elif card_hits > 0:
        results.append((PageType.SEARCH_LIST, f"DOM 命中岗位卡片候选({card_hits})", 0.5))

    # 详情页
    detail_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["detail_root"])
    if detail_hits > 0:
        results.append((PageType.JOB_DETAIL, f"DOM 命中详情页主容器候选({detail_hits})", 0.8))

    # 登录页
    login_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["login_page"])
    if login_hits > 0:
        results.append((PageType.LOGIN, f"DOM 命中登录页候选({login_hits})", 0.8))

    # 验证页
    verify_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["verification_page"])
    if verify_hits > 0:
        results.append((PageType.VERIFICATION, f"DOM 命中验证页候选({verify_hits})", 0.75))

    # 空结果页
    empty_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["empty_results"])
    if empty_hits > 0:
        results.append((PageType.EMPTY_RESULTS, f"DOM 命中空结果页候选({empty_hits})", 0.7))

    # 错误页
    error_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["error_page"])
    if error_hits > 0:
        results.append((PageType.ERROR, f"DOM 命中错误页候选({error_hits})", 0.7))

    # 首页
    home_hits = _count_selector_hits(soup, PAGE_LEVEL_SELECTORS["home_page"])
    if home_hits > 0:
        results.append((PageType.HOME, f"DOM 命中首页候选({home_hits})", 0.6))

    return results


def _count_selector_hits(soup: BeautifulSoup, selectors: tuple[str, ...]) -> int:
    """统计多个候选选择器命中的元素总数（去重）。"""
    seen_ids: set[int] = set()
    total = 0
    for sel in selectors:
        try:
            found = soup.select(sel)
        except Exception:
            continue
        for el in found:
            # 用 id() 去重（同一元素可能被多个选择器命中）
            if id(el) not in seen_ids:
                seen_ids.add(id(el))
                total += 1
    return total


def detect_page_type(
    soup: BeautifulSoup,
    url: str | None = None,
) -> PageTypeDetection:
    """识别页面类型。

    综合 URL 路径与 DOM 结构证据：
    - 收集所有候选（类型, 证据, 置信度）
    - 若只有一种类型命中：返回该类型，置信度取最高
    - 若多种类型同时命中：保守返回 UNKNOWN，置信度取最低，并记录警告
    - 若无任何命中：返回 UNKNOWN，置信度 0.0

    不得把"没有登录按钮"简单等价为"已登录"：本函数仅基于正面证据，
    缺失登录页证据不会推断为其他类型。

    Args:
        soup: BeautifulSoup 解析对象
        url: 当前页面 URL（可选，用于辅助识别）

    Returns:
        PageTypeDetection: 页面类型识别结果
    """
    url_candidates = _match_url_path(url)
    dom_candidates = _match_dom(soup)

    all_candidates = url_candidates + dom_candidates

    if not all_candidates:
        return PageTypeDetection(
            page_type=PageType.UNKNOWN,
            confidence=0.0,
            evidence=[],
            warnings=["无任何页面类型证据命中"],
        )

    # 按类型分组
    by_type: dict[PageType, list[tuple[str, float]]] = {}
    for page_type, evidence, conf in all_candidates:
        by_type.setdefault(page_type, []).append((evidence, conf))

    types_hit = list(by_type.keys())

    if len(types_hit) == 1:
        # 唯一类型命中
        page_type = types_hit[0]
        entries = by_type[page_type]
        best_conf = max(c for _, c in entries)
        evidence = [e for e, _ in entries]
        return PageTypeDetection(
            page_type=page_type,
            confidence=best_conf,
            evidence=evidence,
            warnings=[],
        )

    # 多类型同时命中：保守返回 UNKNOWN
    # 置信度取所有命中类型中最低的最高置信度（保守）
    min_best = min(max(c for _, c in entries) for entries in by_type.values())
    evidence: list[str] = []
    for page_type, entries in by_type.items():
        best = max(c for _, c in entries)
        evidence.append(f"{page_type.value}: 最高置信度 {best:.2f}")
    return PageTypeDetection(
        page_type=PageType.UNKNOWN,
        confidence=min_best,
        evidence=evidence,
        warnings=[
            f"多类型同时命中({len(types_hit)})，保守返回 UNKNOWN，需人工复查或更新选择器候选"
        ],
    )


def is_save_fixture_allowed(detection: PageTypeDetection) -> tuple[bool, str]:
    """判断当前页面类型是否允许保存 fixture。

    登录页、验证页、未知页默认禁止保存 fixture（避免泄露敏感信息）。
    允许保存的页面类型：search_list / job_detail / empty_results / home / error。

    Args:
        detection: 页面类型识别结果

    Returns:
        (allowed, reason): 是否允许保存，原因说明
    """
    pt = detection.page_type
    if pt == PageType.LOGIN:
        return False, "登录页禁止保存 fixture（可能含账号表单/敏感信息）"
    if pt == PageType.VERIFICATION:
        return False, "验证页禁止保存 fixture（可能含验证码/滑块敏感信息）"
    if pt == PageType.UNKNOWN:
        if detection.confidence < 0.5:
            return False, "未知页面且置信度过低，禁止保存 fixture（需先确认页面类型）"
        return True, "未知页面但置信度较高，允许保存（需人工确认内容安全）"
    return True, f"页面类型 {pt.value} 允许保存 fixture"


__all__ = [
    "detect_page_type",
    "is_save_fixture_allowed",
]
