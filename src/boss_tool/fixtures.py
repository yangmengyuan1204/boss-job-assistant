"""P2 fixture 保存与元数据管理。

负责：
- 保存脱敏 HTML fixture（含二次扫描）
- 生成并保存 fixture 元数据 JSON
- 计算 content SHA256
- 不保存完整原 URL / query / fragment / 用户身份 / Cookie / profile 路径
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from boss_tool.models.observed_page import FixtureMeta, PageType
from boss_tool.parsers.sanitization import compute_sha256, sanitize_html
from boss_tool.parsers.selectors import SELECTOR_VERSION


def save_fixture(
    raw_html: str,
    *,
    output_dir: Path,
    label: str,
    page_type: PageType,
    source_url: str,
) -> tuple[Path, Path, FixtureMeta]:
    """保存脱敏 HTML fixture 与元数据 JSON。

    流程：
    1. sanitize_html 脱敏（含二次扫描，发现高风险内容则抛 ValueError）
    2. 计算 SHA256
    3. 生成 FixtureMeta（不含完整 URL / query / fragment）
    4. 写入 {label}.html 与 {label}.meta.json

    Args:
        raw_html: 原始页面 HTML（将被脱敏）
        output_dir: 输出目录
        label: fixture 标签（用作文件名）
        page_type: 页面类型
        source_url: 来源 URL（仅提取 host/path，不保存完整 URL）

    Returns:
        (html_path, meta_path, meta): HTML 路径、元数据路径、元数据对象

    Raises:
        ValueError: 脱敏二次扫描发现高风险内容
        OSError: 文件写入失败
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 脱敏（含二次扫描，失败抛 ValueError）
    sanitized_html = sanitize_html(raw_html, base_url=source_url)

    # 2. 计算 SHA256
    content_sha = compute_sha256(sanitized_html)

    # 3. 从 URL 提取 host/path（不保存完整 URL / query / fragment）
    try:
        parsed = urlparse(source_url)
        source_host = parsed.hostname or ""
        source_path = parsed.path or "/"
    except (ValueError, TypeError):
        source_host = ""
        source_path = "/"

    # 4. 构造元数据
    meta = FixtureMeta(
        fixture_version=1,
        captured_at=datetime.now(),
        page_type=page_type,
        source_host=source_host,
        source_path=source_path,
        sanitized=True,
        selector_version=SELECTOR_VERSION,
        notes=[],
        content_sha256=content_sha,
    )

    # 5. 写入文件
    html_path = output_dir / f"{label}.html"
    meta_path = output_dir / f"{label}.meta.json"

    html_path.write_text(sanitized_html, encoding="utf-8")
    meta_path.write_text(
        meta.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )

    return html_path, meta_path, meta


def load_fixture_meta(meta_path: Path) -> FixtureMeta:
    """加载 fixture 元数据 JSON。"""
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return FixtureMeta.model_validate(data)


def verify_fixture_integrity(html_path: Path, meta_path: Path) -> tuple[bool, str]:
    """校验 fixture HTML 与元数据的完整性（SHA256 一致）。

    Returns:
        (ok, message): 是否一致，说明信息
    """
    meta = load_fixture_meta(meta_path)
    actual_sha = compute_sha256(html_path.read_text(encoding="utf-8"))
    if actual_sha == meta.content_sha256:
        return True, "SHA256 一致"
    return False, f"SHA256 不一致: meta={meta.content_sha256[:16]}... actual={actual_sha[:16]}..."


__all__ = [
    "save_fixture",
    "load_fixture_meta",
    "verify_fixture_integrity",
]
