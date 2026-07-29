"""P3 collect-current-page 命令核心逻辑。

流程：
1. 加载配置，启动 P1 BrowserManager
2. 用户手动登录、手动搜索
3. 命令循环：collect / status / quit
   - collect: 读取当前页面 HTML → P2 parse_list_page → JobListRecord → SQLite UPSERT
   - status: 输出会话与页面状态
   - quit: 安全关闭

严格限制：
- 不自动搜索、不自动翻页、不自动点击岗位、不自动进入详情页
- 仅读取用户手动导航到的当前页面
- 页面类型必须为 SEARCH_LIST，否则拒绝采集
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
from boss_tool.models.job_list import JobListRecord
from boss_tool.models.observed_page import PageType
from boss_tool.parsers.list_page import parse_list_page
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobListRepository

logger = get_logger(__name__)


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
    typer.echo("完成后回到终端使用命令：collect / status / quit")
    typer.echo("-" * 60)

    # 命令源
    if command_source is None:
        command_source = ThreadedCommandSource(prompt="请输入命令 (collect/status/quit): ")
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
            typer.echo("[提示] 空输入无效。请输入 collect / status / quit。")
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

        typer.echo(f"[提示] 未知命令: {cmd!r}。仅接受 collect / status / quit。")


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
    else:
        typer.echo(f"  采集就绪:      当前页面非搜索结果页({detection.page_type.value})，无法采集")


def _handle_collect(
    manager: BrowserManager,
    *,
    db: Database,
    log_path: Path,
    page_no: int | None,
) -> None:
    """collect 命令：读取当前页面 → 解析 → SQLite UPSERT。"""
    started_at = datetime.now()

    observer = _get_observer(manager)
    if observer is None:
        return

    # 1. 检测页面类型
    detection = observer.detect_type()
    typer.echo(f"页面类型: {detection.page_type.value}")

    if detection.page_type != PageType.SEARCH_LIST:
        typer.echo(
            f"[ERROR] 当前页面不是职位搜索结果页({detection.page_type.value})，"
            "无法采集。请手动导航到搜索结果页后重试。"
        )
        _write_collect_log(
            log_path,
            started_at,
            datetime.now(),
            redacted_url=observer.get_current_url(),
            page_type=detection.page_type.value,
            job_count=0,
            new_count=0,
            update_count=0,
            error=f"页面类型不匹配: {detection.page_type.value}",
        )
        return

    # 2. 读取页面 HTML
    raw_html = manager._page.content()  # noqa: SLF001
    raw_url = manager._page.url  # noqa: SLF001
    redacted_url = redact_url(raw_url) or ""

    # 3. 调用 P2 parse_list_page 解析
    cards = parse_list_page(raw_html, base_url=raw_url)
    typer.echo(f"发现职位: {len(cards)}")

    if not cards:
        typer.echo("[WARN] 未解析到任何岗位卡片。页面结构可能已变化或选择器失效。")
        _write_collect_log(
            log_path,
            started_at,
            datetime.now(),
            redacted_url=redacted_url,
            page_type=detection.page_type.value,
            job_count=0,
            new_count=0,
            update_count=0,
            error="未解析到任何岗位卡片",
        )
        return

    # 4. 转换 ObservedJobCard → JobListRecord
    collected_at = datetime.now()
    records = [
        JobListRecord.from_observed_card(card, page_no=page_no, collected_at=collected_at)
        for card in cards
    ]

    # 5. SQLite UPSERT
    with db.transaction() as conn:
        repo = JobListRepository(conn)
        new_count, update_count = repo.bulk_upsert_job_list(records)

    ended_at = datetime.now()
    duration = (ended_at - started_at).total_seconds()

    # 6. 输出摘要
    typer.echo(f"新增:     {new_count}")
    typer.echo(f"更新:     {update_count}")
    typer.echo(f"重复:     {len(records) - new_count - update_count}")
    typer.echo("数据库:   job_list")
    typer.echo(f"耗时:     {duration:.1f}s")
    typer.echo("完成。")

    # 7. 写入日志
    _write_collect_log(
        log_path,
        started_at,
        ended_at,
        redacted_url=redacted_url,
        page_type=detection.page_type.value,
        job_count=len(records),
        new_count=new_count,
        update_count=update_count,
    )


def _write_collect_log(
    log_path: Path,
    started_at: datetime,
    ended_at: datetime,
    *,
    redacted_url: str,
    page_type: str,
    job_count: int,
    new_count: int,
    update_count: int,
    error: str | None = None,
) -> None:
    """写入 P3 采集日志（追加模式）。

    记录：开始时间、结束时间、耗时、脱敏 URL、岗位数量、新增/更新数量、异常。
    不记录：Cookie、手机号、Email、Token、SecurityId。
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
        f"更新:       {update_count}",
    ]
    if error:
        lines.append(f"异常:       {error}")
    lines.append("")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(
        "collect-current-page 完成: job_count=%d new=%d update=%d duration=%.1fs",
        job_count,
        new_count,
        update_count,
        duration,
    )
