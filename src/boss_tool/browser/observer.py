"""P2 页面侦察器（基于 Playwright Page，只读 DOM）。

职责：
- 读取当前公开页面的必要 DOM 信息（脱敏）
- 页面类型识别（基于 DOM 与 URL）
- inspect：只读页面侦察，输出诊断摘要
- 生成脱敏 fixture HTML（不直接保存，由 CLI 调用保存）

严格限制（inspect 不得）：
- 点击 / 滚动 / 导航 / 刷新 / 修改页面 / 注入脚本 / 触发网络请求
- 只允许读取当前已渲染 DOM

不读取：
- Cookie / localStorage / sessionStorage / Authorization
- 完整用户身份信息 / 聊天内容 / 页面全部文本

本模块依赖 Playwright Page 对象，但解析逻辑通过 parsers 模块完成（与 Playwright 解耦）。
为便于测试，接受任何具有 url/title()/content() 方法的 page-like 对象。
"""

from __future__ import annotations

from typing import Any, Protocol

from bs4 import BeautifulSoup

from boss_tool.browser.manager import redact_url
from boss_tool.logging_config import get_logger
from boss_tool.models.observed_page import PageType, PageTypeDetection
from boss_tool.parsers.page_types import detect_page_type, is_save_fixture_allowed
from boss_tool.parsers.sanitization import sanitize_html

logger = get_logger(__name__)

# title 最大长度（截断防止泄露过多）
_TITLE_MAX_LENGTH = 80


class PageLike(Protocol):
    """Playwright Page 的最小协议（便于测试 mock）。"""

    @property
    def url(self) -> str: ...

    def title(self) -> str: ...

    def content(self) -> str: ...


class PageObserver:
    """页面侦察器（只读）。

    不持有 Playwright 引用，只接收 page-like 对象。
    所有方法只读取当前已渲染 DOM，不触发任何副作用。
    """

    def __init__(self, page: PageLike):
        self._page = page

    def get_current_url(self) -> str:
        """获取当前脱敏 URL（仅 scheme://host/path）。"""
        return redact_url(self._page.url) or ""

    def get_current_title(self) -> str:
        """获取当前页面 title（截断 + 文本脱敏）。"""
        try:
            title = self._page.title()
        except Exception as e:
            logger.warning("读取 title 失败: %s", type(e).__name__)
            return "<title-unavailable>"
        # 截断
        if len(title) > _TITLE_MAX_LENGTH:
            title = title[:_TITLE_MAX_LENGTH] + "..."
        return title

    def _get_soup(self) -> BeautifulSoup:
        """获取当前页面的 BeautifulSoup 对象（仅用于解析，不保存原始 HTML）。"""
        html = self._page.content()
        return BeautifulSoup(html, "lxml")

    def detect_type(self) -> PageTypeDetection:
        """识别当前页面类型。"""
        url = self._page.url
        soup = self._get_soup()
        return detect_page_type(soup, url=url)

    def is_save_fixture_allowed(self) -> tuple[bool, str]:
        """判断当前页面是否允许保存 fixture。"""
        detection = self.detect_type()
        return is_save_fixture_allowed(detection)

    def inspect(self) -> dict[str, Any]:
        """执行只读页面侦察，输出结构化诊断摘要。

        只读取当前已渲染 DOM，不点击/滚动/导航/刷新/修改页面/注入脚本/触发网络请求。

        Returns:
            诊断摘要 dict：
            - page_type: 页面类型候选
            - confidence: 置信度
            - evidence: 命中证据
            - warnings: 警告
            - card_count: 岗位卡片候选数量
            - detail_root_found: 详情主容器候选数量
            - field_hits: 字段选择器候选命中情况
            - missing_fields: 缺失字段
            - structure_changed: 是否疑似页面结构变化
        """
        url = self._page.url
        soup = self._get_soup()
        detection = detect_page_type(soup, url=url)

        # 岗位卡片候选数量
        from boss_tool.parsers.list_page import find_job_cards

        card_tags = find_job_cards(soup)
        card_count = len(card_tags)

        # 详情主容器候选
        from boss_tool.parsers.detail_page import find_detail_root

        detail_root = find_detail_root(soup)

        # 字段命中（仅统计列表页或详情页的关键字段）
        field_hits: dict[str, int] = {}
        if detection.page_type == PageType.SEARCH_LIST and card_count > 0:
            from boss_tool.parsers.list_page import parse_single_card

            for idx, card_tag in enumerate(card_tags[:3]):  # 仅抽样前3个
                _, hits = parse_single_card(card_tag, idx)
                for name, count in hits.items():
                    field_hits[name] = field_hits.get(name, 0) + count
        elif detection.page_type == PageType.JOB_DETAIL and detail_root is not None:
            from boss_tool.parsers.detail_page import parse_detail_page

            _, hits = parse_detail_page(self._page.content())
            field_hits = hits

        # 缺失字段
        missing_fields: list[str] = []
        for name, count in field_hits.items():
            if count == 0:
                missing_fields.append(name)

        # 是否疑似页面结构变化
        structure_changed = (
            detection.page_type == PageType.UNKNOWN and detection.confidence < 0.5
        ) or (detection.page_type == PageType.SEARCH_LIST and card_count == 0)

        return {
            "page_type": detection.page_type.value,
            "confidence": detection.confidence,
            "evidence": detection.evidence,
            "warnings": detection.warnings,
            "card_count": card_count,
            "detail_root_found": 1 if detail_root is not None else 0,
            "field_hits": field_hits,
            "missing_fields": missing_fields,
            "structure_changed": structure_changed,
            "current_url": self.get_current_url(),
            "title": self.get_current_title(),
        }

    def read_sanitized_html(self) -> str:
        """读取并脱敏当前页面 HTML（供保存 fixture）。

        注意：调用方必须先调用 is_save_fixture_allowed() 确认页面类型允许保存。
        本方法不自动保存，仅返回脱敏后的 HTML 字符串。
        """
        raw_html = self._page.content()
        return sanitize_html(raw_html, base_url=self._page.url)


__all__ = ["PageObserver", "PageLike"]
