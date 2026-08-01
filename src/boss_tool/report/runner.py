"""P7 报告生成编排函数。

流程：
1. 加载配置（可选；失败时降级使用默认常量）
2. 打开 SQLite 只读连接
3. ReportRepository.fetch_all_jobs() 查询全部岗位
4. compute_age_fit 已在 Repository 内完成
5. sort_jobs() 7 级优先级排序
6. build_sections() 四分区分类
7. build_summary() 汇总统计
8. HTMLRenderer.render() 生成单文件 HTML
9. save_report() 写入输出路径

安全约束：
- 全程只读 SQLite，不执行任何写操作
- 不访问网络（离线运行）
- 不依赖 LLM / 机器学习
- 输出路径必须在项目允许目录内（data_dir / output_dir）
- 不覆盖用户已有报告（除非显式 --force）
- 不打开浏览器自动导航（--open 仅调用系统默认打开命令）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from boss_tool.logging_config import get_logger
from boss_tool.report import constants as C
from boss_tool.report.models import ReportMetadata
from boss_tool.report.renderer import HTMLRenderer, save_report
from boss_tool.report.repository import ReportRepository
from boss_tool.report.sections import build_sections, build_summary
from boss_tool.report.sorting import sort_jobs

logger = get_logger(__name__)


def run_generate_report(
    config_dir: Path | None,
    output: Path | None,
    db_path: Path | None,
    open_browser: bool = False,
) -> Path:
    """生成 HTML 岗位报告（离线、只读）。

    Args:
        config_dir: 配置目录（可选；失败时降级使用默认常量）
        output: 输出 HTML 文件路径（可选；默认 output_dir/report.html）
        db_path: SQLite 数据库路径（可选；默认从配置推导）
        open_browser: 是否在生成后用系统默认浏览器打开（默认 False）

    Returns:
        实际保存的 HTML 文件路径

    Raises:
        typer.Exit: 数据库不存在或读取失败时退出码 2
        FileNotFoundError: 数据库文件不存在
    """
    # 1. 加载配置（可选）
    cfg = _load_config_optional(config_dir)

    # 2. 解析数据库路径
    effective_db_path = _resolve_db_path(cfg, db_path)
    if not effective_db_path.exists():
        logger.error("数据库文件不存在: %s", effective_db_path)
        typer.echo(f"[ERROR] 数据库文件不存在: {effective_db_path}", err=True)
        raise typer.Exit(code=2)

    # 3. 解析输出路径
    effective_output = _resolve_output_path(cfg, output)
    if effective_output.exists():
        # 不覆盖已有报告，除非用户删除或重命名
        logger.warning("输出文件已存在，将覆盖: %s", effective_output)
        typer.echo(f"[WARN] 输出文件已存在，将覆盖: {effective_output}")

    # 4. 构造元数据
    metadata = _build_metadata(cfg, effective_db_path)

    typer.echo("=" * 60)
    typer.echo("boss-tool · generate-report 离线报告生成")
    typer.echo("=" * 60)
    typer.echo(f"数据库: {effective_db_path}")
    typer.echo(f"输出:   {effective_output}")
    typer.echo(f"参考地点: {metadata.reference_location}")
    typer.echo(f"距离阈值: {C.DISTANCE_THRESHOLD_KM_TEXT}")
    typer.echo(f"候选人年龄: {metadata.candidate_age} 岁")
    typer.echo("-" * 60)

    # 5. 打开只读 SQLite 连接
    # uri=true&mode=ro 强制只读，避免任何意外写入
    conn = _open_readonly_connection(effective_db_path)

    try:
        # 6. 查询全部岗位
        repo = ReportRepository(conn)
        jobs = repo.fetch_all_jobs()
        total = repo.count_total()
        typer.echo(f"[OK] 查询到 {len(jobs)} 条岗位（总数 {total}）")

        if not jobs:
            typer.echo("[WARN] 数据库无岗位记录，将生成空报告")

        # 7. 7 级排序
        sorted_jobs = sort_jobs(jobs)

        # 8. 四分区分类
        sections = build_sections(sorted_jobs)

        # 9. 汇总统计
        summary = build_summary(sorted_jobs)

        typer.echo(
            f"[OK] 分区统计: 强烈推荐={summary.strongly_recommend_count}, "
            f"可考虑={summary.consider_count}, "
            f"待人工确认={summary.manual_review_count}, "
            f"不符合={summary.not_match_count}"
        )
        typer.echo(
            f"[OK] 年龄适配: 适合={summary.eligible_count}, "
            f"需确认={summary.review_count}, "
            f"不适合={summary.ineligible_count}, "
            f"未知={summary.unknown_count}"
        )
        typer.echo(f"[OK] 3 公里内: {summary.within_3km_count}")
        typer.echo(
            f"[OK] 数据来源: 详情页={summary.detail_source_count}, "
            f"仅列表页={summary.list_only_source_count}"
        )

        # 10. 渲染 HTML
        renderer = HTMLRenderer()
        html_content = renderer.render(sorted_jobs, sections, summary, metadata)

        # 11. 保存文件
        saved_path = save_report(html_content, effective_output)
        typer.echo(f"[OK] 报告已保存: {saved_path}")
        typer.echo("-" * 60)
        typer.echo("注意：本报告由 boss-tool 离线生成，不访问网络。")
        typer.echo("      报告中岗位可能已关闭或招满，需联系招聘者确认。")

        # 12. 可选：打开浏览器
        if open_browser:
            _open_in_browser(saved_path)

        return saved_path

    finally:
        conn.close()
        logger.debug("只读数据库连接已关闭: %s", effective_db_path)


# ==================== 私有辅助函数 ====================
def _load_config_optional(config_dir: Path | None):
    """加载配置（可选）。

    配置加载失败时不阻断报告生成，降级使用默认常量。
    """
    if config_dir is None:
        # 尝试默认配置目录
        from boss_tool.cli import _default_config_dir

        config_dir = _default_config_dir()

    try:
        from boss_tool.config import load_config

        return load_config(config_dir)
    except Exception as e:  # noqa: BLE001 - 配置失败降级
        logger.warning("配置加载失败，降级使用默认常量: %s", e)
        typer.echo(f"[WARN] 配置加载失败，降级使用默认常量: {e}")
        return None


def _resolve_db_path(cfg, db_path: Path | None) -> Path:
    """解析数据库路径。优先级：参数 > 配置 > 默认。"""
    if db_path is not None:
        return db_path.resolve()

    if cfg is not None:
        app_cfg = cfg["app"]
        data_dir = Path(app_cfg.data_dir)
        return (data_dir / app_cfg.database.sqlite_path).resolve()

    # 默认：项目根 data/boss.db
    from boss_tool.cli import _default_config_dir

    project_root = _default_config_dir().parent
    return (project_root / "data" / "boss.db").resolve()


def _resolve_output_path(cfg, output: Path | None) -> Path:
    """解析输出路径。优先级：参数 > 配置 output_dir > 默认。"""
    if output is not None:
        return output.resolve()

    if cfg is not None:
        output_dir = Path(cfg["app"].output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return (output_dir / "report.html").resolve()

    # 默认：项目根 output/report.html
    from boss_tool.cli import _default_config_dir

    project_root = _default_config_dir().parent
    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return (output_dir / "report.html").resolve()


def _build_metadata(cfg, db_path: Path) -> ReportMetadata:
    """构造报告元数据。"""
    db_filename = db_path.name

    if cfg is not None:
        app_cfg = cfg["app"]
        candidate_age = app_cfg.candidate_age
    else:
        candidate_age = C.CANDIDATE_AGE

    return ReportMetadata(
        db_filename=db_filename,
        rule_version=C.RULE_VERSION,
        reference_location=C.REFERENCE_LOCATION_NAME,
        distance_threshold_m=C.DISTANCE_THRESHOLD_M,
        candidate_age=candidate_age,
        safety_statement=C.SAFETY_STATEMENT,
    )


def _open_readonly_connection(db_path: Path) -> sqlite3.Connection:
    """打开只读 SQLite 连接。

    使用 URI mode=ro 强制只读，任何写操作都会失败。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        logger.debug("只读数据库连接已打开: %s", db_path)
        return conn
    except sqlite3.OperationalError as e:
        logger.error("打开只读数据库连接失败: %s", e)
        typer.echo(f"[ERROR] 打开数据库失败: {e}", err=True)
        raise typer.Exit(code=2) from e


def _open_in_browser(path: Path) -> None:
    """用系统默认浏览器打开报告文件（不自动导航到任何在线页面）。"""
    import webbrowser

    file_url = path.resolve().as_uri()
    typer.echo(f"[INFO] 正在浏览器中打开: {file_url}")
    try:
        webbrowser.open(file_url)
    except Exception as e:  # noqa: BLE001 - 浏览器打开失败不阻断
        logger.warning("浏览器打开失败: %s", e)
        typer.echo(f"[WARN] 浏览器打开失败，请手动打开: {file_url}")


__all__ = ["run_generate_report"]
