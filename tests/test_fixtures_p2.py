"""P2 fixture 保存与元数据管理测试。

测试 save_fixture / verify_fixture_integrity，
覆盖脱敏 HTML 保存、SHA256 元数据、URL 不含 query、完整性校验、高风险内容拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_tool.fixtures import save_fixture, verify_fixture_integrity
from boss_tool.models.observed_page import PageType


class TestSaveFixture:
    """测试 save_fixture fixture 保存。"""

    def test_save_basic_fixture(
        self,
        list_page_basic_html: str,
        tmp_workspace: Path,
    ) -> None:
        """用 list_page_basic_html 调用 save_fixture，验证 .html 和 .meta.json 都存在。"""
        html_path, meta_path, _meta = save_fixture(
            list_page_basic_html,
            output_dir=tmp_workspace,
            label="basic_test",
            page_type=PageType.SEARCH_LIST,
            source_url="https://www.zhipin.com/web/geek/job",
        )
        assert html_path.exists()
        assert meta_path.exists()
        assert html_path.suffix == ".html"
        assert meta_path.suffix == ".json"

    def test_fixture_sanitized(self, tmp_workspace: Path) -> None:
        """保存的 HTML 不含 script/style。"""
        html = (
            "<script>alert(1)</script>"
            "<style>.x{}</style>"
            '<div class="job-detail"><h1 class="job-name">test</h1></div>'
        )
        html_path, _meta_path, _meta = save_fixture(
            html,
            output_dir=tmp_workspace,
            label="sanitized_test",
            page_type=PageType.JOB_DETAIL,
            source_url="https://www.zhipin.com/job_detail/123.html",
        )
        saved = html_path.read_text(encoding="utf-8")
        assert "<script" not in saved
        assert "<style" not in saved

    def test_meta_has_sha256(
        self,
        list_page_basic_html: str,
        tmp_workspace: Path,
    ) -> None:
        """meta.content_sha256 非空且 64 字符。"""
        _html_path, _meta_path, meta = save_fixture(
            list_page_basic_html,
            output_dir=tmp_workspace,
            label="sha_test",
            page_type=PageType.SEARCH_LIST,
            source_url="https://www.zhipin.com/web/geek/job",
        )
        assert meta.content_sha256
        assert len(meta.content_sha256) == 64

    def test_meta_no_full_url(
        self,
        list_page_basic_html: str,
        tmp_workspace: Path,
    ) -> None:
        """meta 不含完整 URL（source_host 和 source_path 不含 query）。"""
        _html_path, _meta_path, meta = save_fixture(
            list_page_basic_html,
            output_dir=tmp_workspace,
            label="url_test",
            page_type=PageType.SEARCH_LIST,
            source_url="https://www.zhipin.com/web/geek/job?token=secret",
        )
        assert "?" not in meta.source_host
        assert "?" not in meta.source_path
        assert "token" not in meta.source_host
        assert "token" not in meta.source_path

    def test_integrity_verified(
        self,
        list_page_basic_html: str,
        tmp_workspace: Path,
    ) -> None:
        """verify_fixture_integrity 返回 (True, ...)。"""
        html_path, meta_path, _meta = save_fixture(
            list_page_basic_html,
            output_dir=tmp_workspace,
            label="integrity_test",
            page_type=PageType.SEARCH_LIST,
            source_url="https://www.zhipin.com/web/geek/job",
        )
        ok, message = verify_fixture_integrity(html_path, meta_path)
        assert ok is True
        assert message

    def test_high_risk_raises(self, tmp_workspace: Path) -> None:
        """含 securityId 的 HTML 调用 save_fixture 抛 ValueError。"""
        html = "<div>securityId=abc123secretvalue</div>"
        with pytest.raises(ValueError, match="高风险内容"):
            save_fixture(
                html,
                output_dir=tmp_workspace,
                label="high_risk_test",
                page_type=PageType.JOB_DETAIL,
                source_url="https://www.zhipin.com/job_detail/123.html",
            )
