"""P3/P4 collect-current-page 命令核心逻辑。

流程：
1. 加载配置，启动 P1 BrowserManager
2. 用户手动登录、手动搜索
3. 命令循环：collect / detail / status / quit
   - collect: 读取当前搜索结果列表页 HTML → P2 parse_list_page_with_diagnostics
     → JobListRecord → SQLite 三态 UPSERT (NEW/UPDATED/UNCHANGED)
     → 诊断安全摘要 + 脱敏日志
   - detail: 仅采集用户已手动打开的当前岗位详情页
     → P4 parse_detail_page_with_diagnostics
     → JobDetailRecord → SQLite 三态 UPSERT
     → 诊断安全摘要 + 脱敏日志
   - status: 输出会话与页面状态
   - quit: 安全关闭

严格限制：
- 不自动搜索、不自动翻页、不自动点击岗位、不自动进入详情页
- 仅读取用户手动导航到的当前页面
- collect: 页面类型必须为 SEARCH_LIST，否则拒绝采集
- detail: 页面类型必须为 JOB_DETAIL，否则拒绝采集
- detail 不调用 page.goto / locator.click / 任何自动导航

P3.1：
- 接入 P2 Diagnostics（parse_list_page_with_diagnostics），不再调用无 diagnostics 版本
- 三态 UPSERT：NEW / UPDATED / UNCHANGED，重复数量不再恒为 0
- 异常隔离：Parser/Diagnostics/Pydantic/SQLite/日志异常不崩溃命令循环
- Diagnostics 安全摘要：仅含计数与字段名，不含页面原文

P4：
- 新增 detail 命令：仅采集用户已手动打开的当前岗位详情页
- 详情页识别：URL 安全校验 + 页面类型 JOB_DETAIL + 稳定核心结构
- 详情 UPSERT：NEW / UPDATED / UNCHANGED，列表字段确定性序列化比较
- 描述安全处理：sanitize_text + 最大长度截断 + 固定警告代码
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from boss_tool.browser import (
    BrowserAlreadyRunningError,
    BrowserManager,
    BrowserStartFailedError,
    ChromiumNotInstalledError,
    CommandSource,
    FakeCommandSource,
    HomePageOpenFailedError,
    PageObserver,
    PlaywrightNotInstalledError,
    ThreadedCommandSource,
    redact_url,
    validate_home_url,
)
from boss_tool.config import RuntimeConfig
from boss_tool.enums import StopReason
from boss_tool.logging_config import get_logger
from boss_tool.models.job_detail import (
    DESCRIPTION_TRUNCATED_CODE,
    JobDetailRecord,
)
from boss_tool.models.job_list import (
    DiagnosticsSummary,
    JobListRecord,
    build_diagnostics_summary,
)
from boss_tool.models.observed_page import PageType
from boss_tool.parsers.detail_page import parse_detail_page_with_diagnostics
from boss_tool.parsers.list_page import parse_list_page_with_diagnostics
from boss_tool.parsers.sanitization import sanitize_url
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobDetailRepository, JobListRepository

logger = get_logger(__name__)


# ==================== 异常类型分类（不输出敏感原文） ====================
# 异常发生时只输出类型名，不输出可能含页面原文的异常消息
_SAFE_ERROR_TYPES = {
    "ParserError": "解析器异常",
    "DiagnosticsError": "诊断处理异常",
    "ValidationError": "模型转换异常",
    "DatabaseError": "数据库写入异常",
    "OperationalError": "数据库操作异常",
    "IntegrityError": "数据库约束异常",
    "OSError": "日志写入异常",
}


def _safe_error_type(exc: Exception) -> str:
    """返回安全的错误类型描述，不输出异常消息（可能含页面原文）。"""
    type_name = type(exc).__name__
    return _SAFE_ERROR_TYPES.get(type_name, type_name)


def run_collect_current_page(
    cfg: RuntimeConfig,
    *,
    home_url: str | None,
    db_path: Path,
    log_path: Path,
    page_no: int | None,
    project_root: Path,
    command_source: CommandSource | None = None,
) -> None:
    """collect-current-page 命令核心逻辑。

    Args:
        cfg: 已加载的 RuntimeConfig
        home_url: 覆盖首页 URL（可选）
        db_path: SQLite 数据库文件路径
        log_path: P3 采集日志文件路径
        page_no: 采集页码（人工指定，可选）
        project_root: 项目根目录
        command_source: 命令源（测试注入，生产为 ThreadedCommandSource）
    """
    browser_cfg = cfg.browser
    run_control_cfg = cfg.run_control

    # 校验 run_control 红线
    if not run_control_cfg.require_user_confirm:
        typer.echo("[ERROR] 配置错误：require_user_confirm 必须为 true", err=True)
        raise typer.Exit(code=2)
    if run_control_cfg.allow_unattended:
        typer.echo("[ERROR] 配置错误：allow_unattended 必须为 false", err=True)
        raise typer.Exit(code=2)
    if run_control_cfg.allow_background:
        typer.echo("[ERROR] 配置错误：allow_background 必须为 false", err=True)
        raise typer.Exit(code=2)

    # 解析首页 URL
    effective_home_url = home_url or browser_cfg.home_url
    try:
        effective_home_url = validate_home_url(effective_home_url)
    except ValueError as e:
        typer.echo(f"[ERROR] home_url 校验失败: {e}", err=True)
        raise typer.Exit(code=2) from e

    # 初始化数据库
    db = Database(db_path, foreign_keys=True)
    db.initialize()
    typer.echo(f"数据库: {db_path} (schema v{db.get_schema_version()})")

    manager = BrowserManager(
        browser_cfg.model_copy(update={"home_url": effective_home_url}),
        project_root=project_root,
    )

    typer.echo("=" * 60)
    typer.echo("boss-tool · collect-current-page 搜索结果列表采集")
    typer.echo("=" * 60)
    typer.echo(f"用户目录: {manager.user_data_dir}")
    typer.echo(f"首页:     {redact_url(effective_home_url)}")
    typer.echo(f"数据库:   {db_path}")
    typer.echo(f"日志:     {log_path}")
    typer.echo(f"页码:     {page_no or '未指定'}")
    typer.echo("会话模式: 可见浏览器 / 单账号 / 人工确认 / 仅采集当前页")
    typer.echo("-" * 60)

    # 启动浏览器
    try:
        session = manager.start()
    except PlaywrightNotInstalledError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(code=3) from e
    except ChromiumNotInstalledError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(code=4) from e
    except BrowserStartFailedError as e:
        typer.echo(f"[ERROR] 浏览器启动失败: {e}", err=True)
        raise typer.Exit(code=5) from e
    except HomePageOpenFailedError as e:
        typer.echo(f"[ERROR] 首页打开失败: {e}", err=True)
        manager.close(stop_reason=StopReason.UNKNOWN_ERROR)
        raise typer.Exit(code=6) from e
    except BrowserAlreadyRunningError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(code=7) from e

    typer.echo("浏览器已启动并打开首页。")
    typer.echo("请在浏览器中手动完成登录，然后手动搜索到目标岗位列表页。")
    typer.echo("完成后回到终端使用命令：collect / detail / status / quit")
    typer.echo("-" * 60)

    # 命令源
    if command_source is None:
        command_source = ThreadedCommandSource(prompt="请输入命令 (collect/detail/status/quit): ")
    command_source.start()

    try:
        _run_collect_loop(
            manager,
            session,
            db=db,
            log_path=log_path,
            page_no=page_no,
            command_source=command_source,
        )
    except KeyboardInterrupt:
        typer.echo("\n[INFO] 收到 Ctrl+C，正在安全退出...")
        manager.close(stop_reason=StopReason.USER_ABORTED)
    except Exception as e:
        typer.echo(f"[ERROR] 会话异常: {type(e).__name__}", err=True)
        manager.close(stop_reason=StopReason.UNKNOWN_ERROR, error_message=str(type(e).__name__))
    finally:
        command_source.stop()
        if manager.is_running:
            manager.close(stop_reason=StopReason.USER_ABORTED)
        db.close()
        final = manager.session
        if final is not None:
            typer.echo("-" * 60)
            typer.echo(f"会话状态: {final.state}")
            typer.echo(f"停止原因: {final.stop_reason}")
            typer.echo(f"关闭来源: {final.close_source}")
            typer.echo(
                "注意：本工具仅减少不必要访问与程序失控风险，"
                "不能保证账号不受限制，不得用于规避平台检测。"
            )


def _run_collect_loop(
    manager: BrowserManager,
    session,
    *,
    db: Database,
    log_path: Path,
    page_no: int | None,
    command_source: CommandSource,
    poll_interval: float = 0.2,
) -> None:
    """collect-current-page 命令循环（非阻塞轮询）。"""
    if command_source is None:
        command_source = FakeCommandSource()

    while True:
        # 浏览器关闭检测
        if not manager.is_running:
            typer.echo("[INFO] 浏览器已关闭（由用户或异常触发），会话结束。")
            return
        if session.state.is_terminal():
            typer.echo("[INFO] 会话已结束。")
            return

        cmd = command_source.poll(timeout=poll_interval)
        if cmd is None:
            continue

        cmd = cmd.strip()
        cmd_lower = cmd.lower()

        if not cmd:
            typer.echo("[提示] 空输入无效。请输入 collect / detail / status / quit。")
            continue

        if cmd_lower == "quit":
            typer.echo("[INFO] 用户主动退出，正在安全关闭...")
            manager.close(stop_reason=StopReason.USER_ABORTED)
            return

        if cmd_lower == "status":
            _handle_status(manager)
            continue

        if cmd_lower == "collect":
            _handle_collect(manager, db=db, log_path=log_path, page_no=page_no)
            continue

        if cmd_lower == "detail":
            _handle_detail(manager, db=db, log_path=log_path)
            continue

        typer.echo(f"[提示] 未知命令: {cmd!r}。仅接受 collect / detail / status / quit。")


def _get_observer(manager: BrowserManager) -> PageObserver | None:
    """从 manager 获取当前页面的 PageObserver。"""
    page = manager._page  # noqa: SLF001  # 访问内部页面引用用于只读侦察
    if page is None:
        typer.echo("[ERROR] 当前没有活动页面。")
        return None
    return PageObserver(page)


def _handle_status(manager: BrowserManager) -> None:
    """status 命令：输出会话与页面状态（脱敏）。"""
    s = manager.session
    if s is not None:
        typer.echo(f"  session_id:    {s.session_id}")
        typer.echo(f"  state:         {s.state}")
        typer.echo(f"  user_confirmed: {s.user_confirmed}")

    observer = _get_observer(manager)
    if observer is None:
        return

    detection = observer.detect_type()
    typer.echo(f"  当前 URL:      {observer.get_current_url()}")
    typer.echo(f"  页面类型:      {detection.page_type.value}")
    typer.echo(f"  置信度:        {detection.confidence:.2f}")

    if detection.page_type == PageType.SEARCH_LIST:
        typer.echo("  采集就绪:      可以执行 collect")
    elif detection.page_type == PageType.JOB_DETAIL:
        typer.echo("  采集就绪:      可以执行 detail")
    else:
        typer.echo(
            f"  采集就绪:      当前页面非搜索结果页/详情页({detection.page_type.value})，无法采集"
        )


def _handle_collect(
    manager: BrowserManager,
    *,
    db: Database,
    log_path: Path,
    page_no: int | None,
) -> None:
    """collect 命令：读取当前页面 → 解析(含诊断) → SQLite 三态 UPSERT。

    P3.1：
    - 使用 parse_list_page_with_diagnostics，不再调用无 diagnostics 版本
    - 三态 UPSERT：NEW / UPDATED / UNCHANGED
    - 异常隔离：Parser/Diagnostics/Pydantic/SQLite/日志异常不崩溃命令循环
    """
    started_at = datetime.now()

    observer = _get_observer(manager)
    if observer is None:
        return

    # 1. 检测页面类型
    try:
        detection = observer.detect_type()
    except Exception as exc:  # noqa: BLE001 - 页面类型检测异常需隔离
        typer.echo(f"[ERROR] 页面类型检测异常: {_safe_error_type(exc)}", err=True)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url="",
            page_type="unknown",
            error_type=_safe_error_type(exc),
        )
        return

    typer.echo(f"页面类型: {detection.page_type.value}")

    if detection.page_type != PageType.SEARCH_LIST:
        typer.echo(
            f"[ERROR] 当前页面不是职位搜索结果页({detection.page_type.value})，"
            "无法采集。请手动导航到搜索结果页后重试。"
        )
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url=observer.get_current_url() or "",
            page_type=detection.page_type.value,
            error_type="页面类型不匹配",
            error_detail=detection.page_type.value,
        )
        return

    # 2. 读取页面 HTML 与 URL（URL 脱敏）
    try:
        raw_html = manager._page.content()  # noqa: SLF001
        raw_url = manager._page.url  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - 浏览器/页面读取异常需隔离
        typer.echo(f"[ERROR] 页面读取异常: {_safe_error_type(exc)}", err=True)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url="",
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
        )
        return

    redacted_url = redact_url(raw_url) or ""

    # 3. 调用 P2 parse_list_page_with_diagnostics 解析（含诊断）
    try:
        cards, diagnostics = parse_list_page_with_diagnostics(raw_html, base_url=raw_url)
    except Exception as exc:  # noqa: BLE001 - Parser/Diagnostics 异常需隔离
        typer.echo(f"[ERROR] 解析异常: {_safe_error_type(exc)}", err=True)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
        )
        return

    typer.echo(f"发现职位: {len(cards)}")

    # 构建诊断安全摘要
    try:
        diag_summary = build_diagnostics_summary(diagnostics)
    except Exception as exc:  # noqa: BLE001 - 诊断摘要构建异常需隔离
        typer.echo(f"[ERROR] 诊断摘要异常: {_safe_error_type(exc)}", err=True)
        diag_summary = DiagnosticsSummary()

    # 输出诊断摘要（仅计数与字段名，不含页面原文）
    _output_diagnostics_summary(diag_summary)

    if not cards:
        typer.echo("[WARN] 未解析到任何岗位卡片。页面结构可能已变化或选择器失效。")
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            job_count=0,
            error_type="未解析到任何岗位卡片",
            diag_summary=diag_summary,
        )
        return

    # 4. 转换 ObservedJobCard → JobListRecord（from_observed_card 内含 URL 二次防御）
    try:
        collected_at = datetime.now()
        records = [
            JobListRecord.from_observed_card(card, page_no=page_no, collected_at=collected_at)
            for card in cards
        ]
    except Exception as exc:  # noqa: BLE001 - Pydantic 模型转换异常需隔离
        typer.echo(f"[ERROR] 模型转换异常: {_safe_error_type(exc)}", err=True)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            job_count=len(cards),
            error_type=_safe_error_type(exc),
            diag_summary=diag_summary,
        )
        return

    # 5. SQLite 三态 UPSERT
    try:
        with db.transaction() as conn:
            repo = JobListRepository(conn)
            result = repo.bulk_upsert_job_list(records)
    except Exception as exc:  # noqa: BLE001 - SQLite 写入异常需隔离
        typer.echo(f"[ERROR] 数据库写入异常: {_safe_error_type(exc)}", err=True)
        _write_collect_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            job_count=len(records),
            error_type=_safe_error_type(exc),
            diag_summary=diag_summary,
        )
        return

    ended_at = datetime.now()
    duration = (ended_at - started_at).total_seconds()

    # 6. 输出三态统计摘要
    typer.echo(f"新增:     {result.new_count}")
    typer.echo(f"更新:     {result.updated_count}")
    typer.echo(f"重复:     {result.unchanged_count}")
    typer.echo("数据库:   job_list")
    typer.echo(f"耗时:     {duration:.1f}s")
    typer.echo("完成。")

    # 7. 写入日志（含诊断安全摘要）
    try:
        _write_collect_log(
            log_path,
            started_at,
            ended_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            job_count=len(records),
            new_count=result.new_count,
            updated_count=result.updated_count,
            unchanged_count=result.unchanged_count,
            diag_summary=diag_summary,
        )
    except Exception as exc:  # noqa: BLE001 - 日志写入异常需隔离，不泄露敏感内容
        typer.echo(f"[ERROR] 日志写入异常: {_safe_error_type(exc)}", err=True)


def _output_diagnostics_summary(summary: DiagnosticsSummary) -> None:
    """输出诊断安全摘要到终端（仅计数与字段名）。"""
    typer.echo(f"诊断警告: {summary.warning_count}")
    if summary.missing_required_fields or summary.missing_field_counts:
        typer.echo("缺失字段:")
        # 全部缺失的必填字段（标注为全缺失）
        for name in summary.missing_required_fields:
            typer.echo(f"  {name}: 全部缺失")
        # 部分缺失的字段及数量
        for name, count in sorted(summary.missing_field_counts.items()):
            typer.echo(f"  {name}: {count}")
    if summary.selector_miss_count > 0:
        typer.echo(f"选择器未命中: {summary.selector_miss_count}")
    if summary.suggest_manual_review:
        typer.echo("[WARN] 建议人工复查 fixture 或选择器。")


def _write_collect_log(
    log_path: Path,
    started_at: datetime,
    ended_at: datetime,
    *,
    redacted_url: str,
    page_type: str,
    job_count: int,
    new_count: int,
    updated_count: int,
    unchanged_count: int,
    diag_summary: DiagnosticsSummary | None = None,
) -> None:
    """写入 P3 采集日志（追加模式，含诊断安全摘要）。

    P3.1：
    - 三态统计：new / updated / unchanged
    - 诊断安全摘要：仅计数与字段名，不含页面原文
    - 不记录：Cookie、手机号、Email、Token、SecurityId、Authorization、
      userinfo、URL query/fragment、完整原始 HTML、原始 DOM 片段、完整匹配文本
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    duration = (ended_at - started_at).total_seconds()

    lines = [
        f"========== 采集记录 {started_at.isoformat()} ==========",
        f"开始时间:   {started_at.isoformat()}",
        f"结束时间:   {ended_at.isoformat()}",
        f"耗时:       {duration:.1f}s",
        f"脱敏URL:    {redacted_url}",
        f"页面类型:   {page_type}",
        f"岗位数量:   {job_count}",
        f"新增:       {new_count}",
        f"更新:       {updated_count}",
        f"重复:       {unchanged_count}",
    ]

    # 诊断安全摘要（仅字段名与计数，不含页面原文）
    if diag_summary is not None:
        lines.append(f"diagnostics_warning_count: {diag_summary.warning_count}")
        lines.append(f"missing_field_count: {sum(diag_summary.missing_field_counts.values())}")
        # missing_fields_summary: 字段名=缺失数（不含文本样本）
        if diag_summary.missing_required_fields or diag_summary.missing_field_counts:
            parts = []
            for name in diag_summary.missing_required_fields:
                parts.append(f"{name}=all_missing")
            for name, count in sorted(diag_summary.missing_field_counts.items()):
                parts.append(f"{name}={count}")
            lines.append(f"missing_fields_summary: {', '.join(parts)}")
        else:
            lines.append("missing_fields_summary: (none)")
        lines.append(f"selector_miss_count: {diag_summary.selector_miss_count}")
        lines.append(f"fallback_count: {diag_summary.fallback_count}")

    lines.append("")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(
        "collect-current-page 完成: job_count=%d new=%d updated=%d unchanged=%d duration=%.1fs",
        job_count,
        new_count,
        updated_count,
        unchanged_count,
        duration,
    )


def _write_collect_log_safe(
    log_path: Path,
    started_at: datetime,
    *,
    redacted_url: str,
    page_type: str,
    job_count: int = 0,
    error_type: str,
    error_detail: str | None = None,
    diag_summary: DiagnosticsSummary | None = None,
) -> None:
    """异常路径的安全日志写入。

    只记录错误类型与（可选）脱敏后的细节，不输出异常消息原文
    （异常消息可能含页面原文）。
    """
    ended_at = datetime.now()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    duration = (ended_at - started_at).total_seconds()

    lines = [
        f"========== 采集记录 {started_at.isoformat()} ==========",
        f"开始时间:   {started_at.isoformat()}",
        f"结束时间:   {ended_at.isoformat()}",
        f"耗时:       {duration:.1f}s",
        f"脱敏URL:    {redacted_url}",
        f"页面类型:   {page_type}",
        f"岗位数量:   {job_count}",
        "新增:       0",
        "更新:       0",
        "重复:       0",
        f"异常类型:   {error_type}",
    ]
    if error_detail is not None:
        lines.append(f"异常细节:   {error_detail}")
    if diag_summary is not None:
        lines.append(f"diagnostics_warning_count: {diag_summary.warning_count}")
        lines.append(f"missing_field_count: {sum(diag_summary.missing_field_counts.values())}")
        lines.append(f"selector_miss_count: {diag_summary.selector_miss_count}")
        lines.append(f"fallback_count: {diag_summary.fallback_count}")
    lines.append("")

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        # 日志写入失败本身不得导致崩溃；仅记录到 logger
        logger.warning("采集日志写入失败，已忽略")


# ==================== P4: detail 命令（当前详情页采集） ====================
def _handle_detail(
    manager: BrowserManager,
    *,
    db: Database,
    log_path: Path,
) -> None:
    """detail 命令：仅采集用户已手动打开的当前岗位详情页。

    P4 严格限制：
    - 不调用 page.goto / locator.click / 任何自动导航
    - 不接收 URL 参数
    - 不根据数据库 job_url 自动导航
    - 仅读取用户手动打开的当前页面
    - 页面类型必须为 JOB_DETAIL，否则拒绝采集

    流程：
    1. 检测页面类型（必须为 JOB_DETAIL）
    2. 安全校验 URL（sanitize_url，HTTPS + BOSS 官方域名 + 无 userinfo/port/query/fragment）
    3. 读取当前页面 HTML
    4. P4 parse_detail_page_with_diagnostics 解析
    5. 转换 ObservedJobDetail → JobDetailRecord（含描述脱敏与截断）
    6. SQLite 三态 UPSERT（NEW/UPDATED/UNCHANGED）
    7. 输出安全的采集结果摘要（不含描述正文）
    8. 写入采集日志

    异常隔离：页面类型/HTML/Parser/模型转换/Diagnostics/SQLite/日志异常不崩溃
    """
    started_at = datetime.now()

    observer = _get_observer(manager)
    if observer is None:
        return

    # 1. 检测页面类型
    try:
        detection = observer.detect_type()
    except Exception as exc:  # noqa: BLE001 - 页面类型检测异常需隔离
        typer.echo(f"[ERROR] 页面类型检测异常: {_safe_error_type(exc)}", err=True)
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url="",
            page_type="unknown",
            error_type=_safe_error_type(exc),
        )
        return

    typer.echo(f"页面类型: {detection.page_type.value}")

    if detection.page_type != PageType.JOB_DETAIL:
        typer.echo(
            f"[ERROR] 当前页面不是岗位详情页({detection.page_type.value})，"
            "无法采集详情。请手动打开岗位详情页后重试。"
        )
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=observer.get_current_url() or "",
            page_type=detection.page_type.value,
            error_type="页面类型不匹配",
            error_detail=detection.page_type.value,
        )
        return

    # 2. 读取页面 HTML 与 URL
    try:
        raw_html = manager._page.content()  # noqa: SLF001
        raw_url = manager._page.url  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - 浏览器/页面读取异常需隔离
        typer.echo(f"[ERROR] 页面读取异常: {_safe_error_type(exc)}", err=True)
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url="",
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
        )
        return

    # 3. URL 安全校验：sanitize_url 严格校验 HTTPS + BOSS 官方域名 + 无 userinfo/port/query/fragment
    safe_job_url = sanitize_url(raw_url)
    if not safe_job_url:
        typer.echo(
            "[ERROR] 当前 URL 未通过安全校验"
            "（需 HTTPS + BOSS 官方域名 + 无 userinfo/port/query/fragment），"
            "拒绝采集详情。"
        )
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=redact_url(raw_url) or "",
            page_type=detection.page_type.value,
            error_type="URL 安全校验失败",
        )
        return

    redacted_url = redact_url(safe_job_url) or ""

    # 4. P4 parse_detail_page_with_diagnostics 解析（含诊断）
    try:
        detail, diagnostics = parse_detail_page_with_diagnostics(raw_html, base_url=safe_job_url)
    except Exception as exc:  # noqa: BLE001 - Parser/Diagnostics 异常需隔离
        typer.echo(f"[ERROR] 解析异常: {_safe_error_type(exc)}", err=True)
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
        )
        return

    # 解析成功性检查：parse_detail_page_with_diagnostics 在 root 缺失时 parser_success=False
    if not diagnostics.parser_success:
        typer.echo(
            "[ERROR] 详情页核心结构未命中（找不到详情主容器或岗位名称），"
            "拒绝采集详情。建议人工复查页面或选择器。"
        )
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            error_type="详情页核心结构缺失",
            diag_summary=_build_detail_diagnostics_summary(diagnostics),
        )
        return

    # 构建诊断安全摘要
    try:
        diag_summary = _build_detail_diagnostics_summary(diagnostics)
    except Exception as exc:  # noqa: BLE001 - 诊断摘要构建异常需隔离
        typer.echo(f"[ERROR] 诊断摘要异常: {_safe_error_type(exc)}", err=True)
        diag_summary = DiagnosticsSummary()

    # 输出诊断摘要（仅计数与字段名，不含页面原文）
    _output_diagnostics_summary(diag_summary)

    # 5. 转换 ObservedJobDetail → JobDetailRecord（含描述脱敏与截断）
    try:
        collected_at = datetime.now()
        record = JobDetailRecord.from_observed_detail(
            detail,
            job_url=safe_job_url,
            collected_at=collected_at,
        )
    except Exception as exc:  # noqa: BLE001 - Pydantic 模型转换异常需隔离
        typer.echo(f"[ERROR] 模型转换异常: {_safe_error_type(exc)}", err=True)
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
            diag_summary=diag_summary,
        )
        return

    # 描述安全摘要：仅输出是否获取/字符数/是否截断，不输出描述正文
    desc_present = record.description is not None
    desc_len = len(record.description) if record.description else 0
    typer.echo(f"描述获取: {'是' if desc_present else '否'}")
    typer.echo(f"描述字符数: {desc_len}")
    if record.description_truncated:
        typer.echo(f"描述截断: 是 (代码: {DESCRIPTION_TRUNCATED_CODE})")
    else:
        typer.echo("描述截断: 否")

    # 6. SQLite 三态 UPSERT
    try:
        with db.transaction() as conn:
            repo = JobDetailRepository(conn)
            outcome = repo.save_job_detail(record)
    except Exception as exc:  # noqa: BLE001 - SQLite 写入异常需隔离
        typer.echo(f"[ERROR] 数据库写入异常: {_safe_error_type(exc)}", err=True)
        _write_detail_log_safe(
            log_path,
            started_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            error_type=_safe_error_type(exc),
            diag_summary=diag_summary,
        )
        return

    ended_at = datetime.now()
    duration = (ended_at - started_at).total_seconds()

    # 7. 输出三态摘要
    outcome_text = {
        "new": "新增",
        "updated": "更新",
        "unchanged": "重复",
    }.get(outcome.value, outcome.value)
    typer.echo(f"结果:     {outcome_text}")
    typer.echo("数据库:   job_detail")
    typer.echo(f"耗时:     {duration:.1f}s")
    typer.echo("完成。")

    # 8. 写入日志（含诊断安全摘要，不含描述正文）
    try:
        _write_detail_log(
            log_path,
            started_at,
            ended_at,
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            outcome=outcome.value,
            desc_present=desc_present,
            desc_len=desc_len,
            desc_truncated=record.description_truncated,
            diag_summary=diag_summary,
        )
    except Exception as exc:  # noqa: BLE001 - 日志写入异常需隔离，不泄露敏感内容
        typer.echo(f"[ERROR] 日志写入异常: {_safe_error_type(exc)}", err=True)


def _build_detail_diagnostics_summary(diagnostics) -> DiagnosticsSummary:  # noqa: ANN001
    """从详情页 ParseDiagnostics 构建安全摘要。

    复用 P3.1 build_diagnostics_summary 的安全语义。
    详情页 card_count 恒为 0（详情页无卡片概念），仅统计字段命中与缺失。
    """
    # 详情页 field_matches 直接对应字段命中数（0 或 1）
    # build_diagnostics_summary 期望基于 card_count 计算缺失数，
    # 详情页 card_count=0 时公式不适用，因此此处直接构建安全摘要
    field_matches = diagnostics.field_matches or {}
    selector_miss_count = sum(1 for hits in field_matches.values() if hits == 0)
    missing_required_fields = list(diagnostics.missing_required_fields or [])

    return DiagnosticsSummary(
        card_count=0,  # 详情页无卡片概念
        warning_count=len(diagnostics.warnings or []),
        missing_required_fields=missing_required_fields,
        missing_field_counts={},  # 详情页单条记录，不适用部分缺失
        selector_miss_count=selector_miss_count,
        fallback_count=len(diagnostics.ambiguous_fields or []),
        parser_success=diagnostics.parser_success,
        suggest_manual_review=diagnostics.suggest_manual_review,
    )


def _write_detail_log(
    log_path: Path,
    started_at: datetime,
    ended_at: datetime,
    *,
    redacted_url: str,
    page_type: str,
    outcome: str,
    desc_present: bool,
    desc_len: int,
    desc_truncated: bool,
    diag_summary: DiagnosticsSummary | None = None,
) -> None:
    """写入 P4 详情采集日志（追加模式，含诊断安全摘要）。

    不记录：
    - Cookie、手机号、Email、Token、SecurityId、Authorization
    - userinfo、URL query/fragment
    - 完整原始 HTML、原始 DOM 片段、完整匹配文本
    - 描述正文（仅记录是否获取/字符数/是否截断）
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    duration = (ended_at - started_at).total_seconds()

    lines = [
        f"========== 详情采集记录 {started_at.isoformat()} ==========",
        f"开始时间:   {started_at.isoformat()}",
        f"结束时间:   {ended_at.isoformat()}",
        f"耗时:       {duration:.1f}s",
        f"脱敏URL:    {redacted_url}",
        f"页面类型:   {page_type}",
        f"结果:       {outcome}",
        f"描述获取:   {'是' if desc_present else '否'}",
        f"描述字符数: {desc_len}",
        f"描述截断:   {'是' if desc_truncated else '否'}",
    ]

    # 诊断安全摘要（仅字段名与计数，不含页面原文）
    if diag_summary is not None:
        lines.append(f"diagnostics_warning_count: {diag_summary.warning_count}")
        if diag_summary.missing_required_fields:
            parts = [f"{name}=all_missing" for name in diag_summary.missing_required_fields]
            lines.append(f"missing_required_fields: {', '.join(parts)}")
        else:
            lines.append("missing_required_fields: (none)")
        lines.append(f"selector_miss_count: {diag_summary.selector_miss_count}")
        lines.append(f"fallback_count: {diag_summary.fallback_count}")

    lines.append("")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(
        "detail 完成: outcome=%s desc_present=%s desc_len=%d truncated=%s duration=%.1fs",
        outcome,
        desc_present,
        desc_len,
        desc_truncated,
        duration,
    )


def _write_detail_log_safe(
    log_path: Path,
    started_at: datetime,
    *,
    redacted_url: str,
    page_type: str,
    error_type: str,
    error_detail: str | None = None,
    diag_summary: DiagnosticsSummary | None = None,
) -> None:
    """详情采集异常路径的安全日志写入。

    只记录错误类型与（可选）脱敏后的细节，不输出异常消息原文
    （异常消息可能含页面原文）。
    """
    ended_at = datetime.now()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    duration = (ended_at - started_at).total_seconds()

    lines = [
        f"========== 详情采集记录 {started_at.isoformat()} ==========",
        f"开始时间:   {started_at.isoformat()}",
        f"结束时间:   {ended_at.isoformat()}",
        f"耗时:       {duration:.1f}s",
        f"脱敏URL:    {redacted_url}",
        f"页面类型:   {page_type}",
        f"异常类型:   {error_type}",
    ]
    if error_detail is not None:
        lines.append(f"异常细节:   {error_detail}")
    if diag_summary is not None:
        lines.append(f"diagnostics_warning_count: {diag_summary.warning_count}")
        lines.append(f"selector_miss_count: {diag_summary.selector_miss_count}")
        lines.append(f"fallback_count: {diag_summary.fallback_count}")
    lines.append("")

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        # 日志写入失败本身不得导致崩溃；仅记录到 logger
        logger.warning("详情采集日志写入失败，已忽略")
