"""P2 observe-page 命令核心逻辑。

流程：
1. 加载配置
2. 启动 P1 BrowserManager
3. 用户手动登录、手动导航
4. 命令循环：status / inspect / save-fixture / confirm / quit

save-fixture 必须精确输入 SAVE 才保存。
登录页/验证页/未知页（低置信度）禁止保存 fixture。
"""

from __future__ import annotations

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
from boss_tool.fixtures import save_fixture
from boss_tool.logging_config import get_logger
from boss_tool.models.observed_page import PageType
from boss_tool.parsers.page_types import is_save_fixture_allowed

logger = get_logger(__name__)


def run_observe_page(
    cfg: RuntimeConfig,
    *,
    home_url: str | None,
    output_dir: Path,
    label: str,
    project_root: Path,
    command_source: CommandSource | None = None,
) -> None:
    """observe-page 命令核心逻辑。

    Args:
        cfg: 已加载的 RuntimeConfig
        home_url: 覆盖首页 URL（可选）
        output_dir: fixture 输出目录
        label: fixture 标签
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

    manager = BrowserManager(
        browser_cfg.model_copy(update={"home_url": effective_home_url}),
        project_root=project_root,
    )

    typer.echo("=" * 60)
    typer.echo("boss-tool · observe-page 公开页面侦察")
    typer.echo("=" * 60)
    typer.echo(f"用户目录: {manager.user_data_dir}")
    typer.echo(f"首页:     {redact_url(effective_home_url)}")
    typer.echo(f"fixture 输出目录: {output_dir}")
    typer.echo(f"fixture 标签:     {label}")
    typer.echo("会话模式: 可见浏览器 / 单账号 / 人工确认 / 只读侦察")
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
    typer.echo("请在浏览器中手动完成登录，然后手动导航到需要侦察的页面。")
    typer.echo("完成后回到终端使用命令：status / inspect / save-fixture / confirm / quit")
    typer.echo("-" * 60)

    # 命令源
    if command_source is None:
        command_source = ThreadedCommandSource(
            prompt="请输入命令 (status/inspect/save-fixture/confirm/quit): "
        )
    command_source.start()

    try:
        _run_observe_loop(
            manager,
            session,
            command_source=command_source,
            output_dir=output_dir,
            label=label,
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


def _run_observe_loop(
    manager: BrowserManager,
    session,
    *,
    command_source: CommandSource,
    output_dir: Path,
    label: str,
    poll_interval: float = 0.2,
) -> None:
    """observe-page 命令循环（非阻塞轮询）。"""
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
            typer.echo(
                "[提示] 空输入无效。请输入 status / inspect / save-fixture / confirm / quit。"
            )
            continue

        if cmd_lower == "quit":
            typer.echo("[INFO] 用户主动退出，正在安全关闭...")
            manager.close(stop_reason=StopReason.USER_ABORTED)
            return

        if cmd_lower == "confirm":
            manager.confirm_user()
            typer.echo("[OK] 已记录用户确认。")
            typer.echo("注意：confirm 仅代表用户自述已处理完成，不代表程序判断登录成功。")
            continue

        if cmd_lower == "status":
            _handle_status(manager)
            continue

        if cmd_lower == "inspect":
            _handle_inspect(manager)
            continue

        if cmd_lower == "save-fixture":
            _handle_save_fixture(manager, output_dir=output_dir, label=label)
            continue

        typer.echo(
            f"[提示] 未知命令: {cmd!r}。仅接受 status / inspect / save-fixture / confirm / quit。"
        )


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
    typer.echo(f"  页面 title:    {observer.get_current_title()}")
    typer.echo(f"  页面类型候选:  {detection.page_type.value}")
    typer.echo(f"  置信度:        {detection.confidence:.2f}")
    typer.echo(f"  疑似登录页:    {detection.page_type == PageType.LOGIN}")
    typer.echo(f"  疑似验证页:    {detection.page_type == PageType.VERIFICATION}")
    typer.echo(f"  疑似列表页:    {detection.page_type == PageType.SEARCH_LIST}")
    typer.echo(f"  疑似详情页:    {detection.page_type == PageType.JOB_DETAIL}")

    allowed, reason = is_save_fixture_allowed(detection)
    typer.echo(f"  允许保存fixture: {allowed} ({reason})")


def _handle_inspect(manager: BrowserManager) -> None:
    """inspect 命令：只读页面侦察，输出诊断摘要。"""
    observer = _get_observer(manager)
    if observer is None:
        return

    typer.echo("-" * 40)
    typer.echo("执行只读页面侦察（不点击/不滚动/不导航/不刷新）...")
    summary = observer.inspect()
    typer.echo("-" * 40)
    typer.echo(f"  页面类型:        {summary['page_type']}")
    typer.echo(f"  置信度:          {summary['confidence']:.2f}")
    typer.echo(f"  岗位卡片候选数: {summary['card_count']}")
    typer.echo(f"  详情主容器:     {'找到' if summary['detail_root_found'] else '未找到'}")
    if summary["evidence"]:
        typer.echo("  命中证据:")
        for e in summary["evidence"]:
            typer.echo(f"    - {e}")
    if summary["missing_fields"]:
        typer.echo(f"  缺失字段: {summary['missing_fields']}")
    if summary["warnings"]:
        typer.echo("  警告:")
        for w in summary["warnings"]:
            typer.echo(f"    - {w}")
    typer.echo(f"  疑似结构变化:   {summary['structure_changed']}")


def _handle_save_fixture(manager: BrowserManager, *, output_dir: Path, label: str) -> None:
    """save-fixture 命令：保存脱敏 fixture（需精确输入 SAVE）。"""
    observer = _get_observer(manager)
    if observer is None:
        return

    # 检查页面类型是否允许保存
    detection = observer.detect_type()
    allowed, reason = is_save_fixture_allowed(detection)
    if not allowed:
        typer.echo(f"[ERROR] 禁止保存 fixture: {reason}")
        return

    typer.echo("即将保存当前公开页面的最小化脱敏 HTML fixture。")
    typer.echo("输入 SAVE 确认，其他输入取消。")
    # 注意：这里需要从命令源读取下一行输入
    # 在当前架构下，save-fixture 命令处理通过命令循环，二次确认需要特殊处理
    # 这里简化为要求用户在下一轮输入 SAVE，或者直接使用 typer.prompt
    # 为保持非阻塞架构，使用 typer.prompt 阻塞读取确认
    try:
        confirm = typer.prompt("请输入 SAVE 确认保存")
    except (KeyboardInterrupt, EOFError):
        typer.echo("[INFO] 已取消保存。")
        return

    if confirm.strip() != "SAVE":
        typer.echo("[INFO] 输入不是 SAVE，已取消保存。")
        return

    # 保存
    try:
        raw_html = manager._page.content()  # noqa: SLF001
        html_path, meta_path, meta = save_fixture(
            raw_html,
            output_dir=output_dir,
            label=label,
            page_type=detection.page_type,
            source_url=manager._page.url,  # noqa: SLF001
        )
        typer.echo("[OK] fixture 已保存:")
        typer.echo(f"  HTML:     {html_path}")
        typer.echo(f"  元数据:   {meta_path}")
        typer.echo(f"  SHA256:   {meta.content_sha256[:16]}...")
        typer.echo(f"  页面类型: {meta.page_type.value}")
    except ValueError as e:
        typer.echo(f"[ERROR] 保存失败（脱敏二次扫描发现高风险内容）: {e}", err=True)
    except OSError as e:
        typer.echo(f"[ERROR] 文件写入失败: {e}", err=True)


__all__ = ["run_observe_page"]
