"""P2 parse-fixture 命令核心逻辑。

不启动浏览器、不访问网络、不执行 JavaScript、不读取外部资源。
只解析本地 HTML 文件，输出 JSON 或结构化终端摘要。
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from bs4 import BeautifulSoup

from boss_tool.models.observed_page import PageType
from boss_tool.parsers.detail_page import parse_detail_page
from boss_tool.parsers.diagnostics import build_detail_diagnostics, build_list_diagnostics
from boss_tool.parsers.list_page import parse_list_page
from boss_tool.parsers.page_types import detect_page_type


def run_parse_fixture(
    fixture_path: Path,
    *,
    output_json: bool = False,
    show_diagnostics: bool = False,
    force_page_type: str | None = None,
) -> None:
    """parse-fixture 命令核心逻辑。

    Args:
        fixture_path: 本地 HTML fixture 文件路径
        output_json: 是否输出 JSON
        show_diagnostics: 是否输出诊断信息
        force_page_type: 强制指定页面类型（覆盖自动识别）
    """
    if not fixture_path.exists():
        typer.echo(f"[ERROR] 文件不存在: {fixture_path}", err=True)
        raise typer.Exit(code=1)

    if not fixture_path.is_file():
        typer.echo(f"[ERROR] 不是文件: {fixture_path}", err=True)
        raise typer.Exit(code=2)

    # 读取本地 HTML（不访问网络）
    html = fixture_path.read_text(encoding="utf-8")

    # 解析
    soup = BeautifulSoup(html, "lxml")

    # 页面类型识别
    if force_page_type:
        try:
            page_type = PageType(force_page_type)
        except ValueError as e:
            typer.echo(f"[ERROR] 无效的页面类型: {force_page_type}", err=True)
            raise typer.Exit(code=3) from e
    else:
        detection = detect_page_type(soup, url=None)
        page_type = detection.page_type

    result = _parse_by_type(soup, html, page_type)

    if output_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_summary(result, show_diagnostics)


def _parse_by_type(soup: BeautifulSoup, html: str, page_type: PageType) -> dict:
    """按页面类型解析并返回结果 dict。"""
    base = {
        "fixture": str(soup.source.name) if hasattr(soup, "source") and soup.source else "inline",
        "page_type": page_type.value,
    }

    if page_type == PageType.SEARCH_LIST:
        cards = parse_list_page(html)
        diagnostics = build_list_diagnostics(soup, cards)
        base["card_count"] = len(cards)
        base["cards"] = [c.model_dump() for c in cards]
        base["diagnostics"] = diagnostics.model_dump()
        return base

    if page_type == PageType.JOB_DETAIL:
        detail, field_hits = parse_detail_page(html)
        diagnostics = build_detail_diagnostics(soup, detail, field_hits)
        base["detail"] = detail.model_dump()
        base["diagnostics"] = diagnostics.model_dump()
        return base

    if page_type == PageType.EMPTY_RESULTS:
        base["message"] = "空结果页，无岗位卡片"
        return base

    # 其他页面类型（login/verification/error/unknown/home）
    base["message"] = f"页面类型 {page_type.value} 不需要解析岗位字段"
    return base


def _print_summary(result: dict, show_diagnostics: bool) -> None:
    """打印结构化终端摘要。"""
    typer.echo("=" * 60)
    typer.echo("parse-fixture 解析结果")
    typer.echo("=" * 60)
    typer.echo(f"  fixture:   {result.get('fixture', 'unknown')}")
    typer.echo(f"  页面类型:  {result.get('page_type', 'unknown')}")

    if "card_count" in result:
        typer.echo(f"  卡片数量:  {result['card_count']}")
        cards = result.get("cards", [])
        for i, card in enumerate(cards[:5]):  # 仅显示前5个
            typer.echo(f"  --- 卡片 {i} ---")
            typer.echo(f"    岗位名: {card.get('job_name')}")
            typer.echo(f"    岗位URL: {card.get('job_url')}")
            typer.echo(f"    薪资:   {card.get('salary_text')}")
            typer.echo(f"    地区:   {card.get('area_text')}")
            typer.echo(f"    公司:   {card.get('company_name')}")
            if card.get("warnings"):
                typer.echo(f"    警告:   {card['warnings']}")
        if len(cards) > 5:
            typer.echo(f"  ... 共 {len(cards)} 个卡片（仅显示前5个）")

    if "detail" in result:
        detail = result["detail"]
        typer.echo("  --- 详情 ---")
        typer.echo(f"    岗位名: {detail.get('job_name')}")
        typer.echo(f"    薪资:   {detail.get('salary_text')}")
        typer.echo(f"    位置:   {detail.get('location_text')}")
        typer.echo(f"    地址:   {detail.get('address_text')}")
        desc = detail.get("description")
        if desc:
            typer.echo(f"    描述:   {desc[:100]}...")

    if show_diagnostics and "diagnostics" in result:
        diag = result["diagnostics"]
        typer.echo("-" * 40)
        typer.echo("  诊断信息:")
        typer.echo(f"    选择器版本:       {diag.get('selector_version')}")
        typer.echo(f"    解析成功:         {diag.get('parser_success')}")
        typer.echo(f"    建议人工复查:     {diag.get('suggest_manual_review')}")
        root_matches = diag.get("root_matches", {})
        if root_matches:
            typer.echo("    根选择器命中:")
            for k, v in root_matches.items():
                typer.echo(f"      {k}: {v}")
        field_matches = diag.get("field_matches", {})
        if field_matches:
            typer.echo("    字段命中:")
            for k, v in field_matches.items():
                typer.echo(f"      {k}: {v}")
        missing = diag.get("missing_required_fields", [])
        if missing:
            typer.echo(f"    缺失必填字段: {missing}")
        ambiguous = diag.get("ambiguous_fields", [])
        if ambiguous:
            typer.echo(f"    歧义字段:     {ambiguous}")
        warnings = diag.get("warnings", [])
        if warnings:
            typer.echo("    警告:")
            for w in warnings:
                typer.echo(f"      - {w}")

    typer.echo("=" * 60)


__all__ = ["run_parse_fixture"]
