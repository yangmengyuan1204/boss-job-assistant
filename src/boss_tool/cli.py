"""Typer CLI 入口。

提供命令：
- --help
- doctor         # 健康检查（不访问网络）
- init-db        # 初始化数据库
- show-config    # 显示配置（敏感字段脱敏）
- browser-login  # 启动可见浏览器，人工登录（P1）
- run            # 预留（P0 未实现）
- resume         # 预留（P0 未实现）
- export         # 预留（P0 未实现）

P1.1 修复：
- browser-login 命令循环改为非阻塞轮询（CommandSource + daemon 输入线程）
- 浏览器关闭后无需用户在终端按回车即可退出
- 首页 URL 统一调用 validate_home_url 严格校验
- CLI 输出与日志使用 redact_url 脱敏

预留命令执行时必须输出明确提示，而非异常堆栈。
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import tempfile
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
    PlaywrightNotInstalledError,
    ThreadedCommandSource,
    redact_url,
    validate_home_url,
)
from boss_tool.config import load_config
from boss_tool.enums import StopReason
from boss_tool.logging_config import get_logger, setup_logging
from boss_tool.storage.database import CURRENT_SCHEMA_VERSION, Database

app = typer.Typer(
    name="boss-tool",
    help="BOSS直聘岗位辅助采集与筛选工具 - P1 浏览器基础层",
    no_args_is_help=True,
)

logger = get_logger(__name__)


# ==================== 公共选项 ====================
def _default_config_dir() -> Path:
    """默认配置目录（项目根 config/）。

    路径解析：src/boss_tool/cli.py
        .parent              -> src/boss_tool
        .parent.parent       -> src
        .parent.parent.parent-> 项目根 (boss-job-assistant/)
    """
    # 相对当前文件：src/boss_tool/cli.py → 项目根
    return Path(__file__).resolve().parent.parent.parent / "config"


def _load_config_safe(config_dir: Path | None):
    """加载配置，失败时抛 typer.Exit。"""
    try:
        return load_config(config_dir or _default_config_dir())
    except FileNotFoundError as e:
        typer.echo(f"[ERROR] 配置加载失败: {e}", err=True)
        raise typer.Exit(code=2) from e
    except Exception as e:  # noqa: BLE001 - 配置校验错误统一处理
        typer.echo(f"[ERROR] 配置校验失败: {e}", err=True)
        raise typer.Exit(code=2) from e


def _ensure_logging(cfg):
    log_cfg = cfg["app"].logging
    logs_dir = cfg["app"].logs_dir
    setup_logging(log_cfg, logs_dir, force=True)


# ==================== 命令 ====================
@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """无参数时显示帮助。"""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def doctor(
    config_dir: Path | None = typer.Option(
        None, "--config-dir", "-c", help="配置目录（默认项目根 config/）"
    ),
) -> None:
    """健康检查。不打开浏览器，不访问网络。"""
    typer.echo("=" * 60)
    typer.echo("boss-tool · doctor 健康检查")
    typer.echo("=" * 60)

    failed: list[str] = []

    # 1. Python 版本
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 10)
    typer.echo(
        f"[{'OK' if py_ok else 'FAIL'}] Python 版本: {py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    )
    if not py_ok:
        failed.append("Python 版本需 >= 3.10")

    # 2. 配置加载
    cfg = None
    try:
        cfg = _load_config_safe(config_dir)
        typer.echo("[OK] 配置加载成功")
    except typer.Exit:
        # 配置加载失败 → 直接退出，不再继续后续检查
        typer.echo("[FAIL] 配置加载失败，跳过后续检查", err=True)
        raise

    if cfg is not None:
        app_cfg = cfg["app"]
        # 3. 数据目录可写
        data_dir = Path(app_cfg.data_dir)
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            _check_writable(data_dir / ".doctor_test")
            typer.echo(f"[OK] 数据目录可写: {data_dir}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"数据目录不可写: {e}")
            typer.echo(f"[FAIL] 数据目录不可写: {e}", err=True)

        # 4. 日志目录可写
        logs_dir = Path(app_cfg.logs_dir)
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            _check_writable(logs_dir / ".doctor_test")
            typer.echo(f"[OK] 日志目录可写: {logs_dir}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"日志目录不可写: {e}")
            typer.echo(f"[FAIL] 日志目录不可写: {e}", err=True)

        # 5. 输出目录可写
        output_dir = Path(app_cfg.output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            _check_writable(output_dir / ".doctor_test")
            typer.echo(f"[OK] 输出目录可写: {output_dir}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"输出目录不可写: {e}")
            typer.echo(f"[FAIL] 输出目录不可写: {e}", err=True)

        # 6. user_data 目录
        user_data_dir = Path(app_cfg.user_data_dir)
        try:
            user_data_dir.mkdir(parents=True, exist_ok=True)
            typer.echo(f"[OK] user_data 目录就绪: {user_data_dir}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"user_data 目录不可创建: {e}")
            typer.echo(f"[FAIL] user_data 目录不可创建: {e}", err=True)

        # 7. SQLite 初始化（使用临时文件）
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="doctor_") as tmp:
                tmp_path = tmp.name
            try:
                db = Database(tmp_path, foreign_keys=app_cfg.database.foreign_keys)
                db.initialize()
                ver = db.get_schema_version()
                db.close()
                if ver != CURRENT_SCHEMA_VERSION:
                    failed.append(
                        f"schema_version 不匹配: 期望 {CURRENT_SCHEMA_VERSION}，实际 {ver}"
                    )
                    typer.echo(
                        f"[FAIL] schema_version 不匹配: 期望 {CURRENT_SCHEMA_VERSION}，实际 {ver}",
                        err=True,
                    )
                else:
                    typer.echo(f"[OK] SQLite 初始化成功 schema_version={ver}")
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
        except Exception as e:  # noqa: BLE001
            failed.append(f"SQLite 初始化失败: {e}")
            typer.echo(f"[FAIL] SQLite 初始化失败: {e}", err=True)

    # 8. Playwright 包是否安装（不启动浏览器，不访问网络）
    pw_ok = importlib.util.find_spec("playwright") is not None
    typer.echo(f"[{'OK' if pw_ok else 'WARN'}] Playwright 包已安装: {pw_ok}")
    if not pw_ok:
        # P0 阶段 playwright 未安装不视为失败，但给出明确提示
        typer.echo("       注意：playwright 仅声明依赖，P0 阶段不使用")

    typer.echo("=" * 60)
    if failed:
        typer.echo(f"doctor 完成，存在 {len(failed)} 项失败:")
        for i, msg in enumerate(failed, 1):
            typer.echo(f"  {i}. {msg}")
        raise typer.Exit(code=1)
    typer.echo("doctor 完成，所有检查通过")
    typer.echo(
        "注意：本工具仅减少不必要访问与程序失控风险，不能保证账号不受限制，不得用于规避平台检测。"
    )


@app.command(name="init-db")
def init_db(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
) -> None:
    """初始化 SQLite 数据库（幂等）。"""
    cfg = _load_config_safe(config_dir)
    _ensure_logging(cfg)

    app_cfg = cfg["app"]
    data_dir = Path(app_cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / app_cfg.database.sqlite_path

    db = Database(db_path, foreign_keys=app_cfg.database.foreign_keys)
    db.initialize()
    ver = db.get_schema_version()
    db.close()
    typer.echo(f"[OK] 数据库已初始化: {db_path}")
    typer.echo(f"      schema_version={ver}")


@app.command(name="show-config")
def show_config(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
) -> None:
    """显示当前配置（敏感字段脱敏）。"""
    cfg = _load_config_safe(config_dir)
    app_cfg = cfg["app"]
    runtime_cfg = cfg["runtime"]
    kw_cfg = cfg["keywords"]
    loc_cfg = cfg["location"]
    score_cfg = cfg["scoring"]

    typer.echo("=" * 60)
    typer.echo("boss-tool · show-config")
    typer.echo("=" * 60)
    typer.echo(f"应用名称: {app_cfg.app_name}")
    typer.echo(f"求职者年龄: {app_cfg.candidate_age}")
    typer.echo(f"数据目录: {app_cfg.data_dir}")
    typer.echo(f"日志目录: {app_cfg.logs_dir}")
    typer.echo(f"输出目录: {app_cfg.output_dir}")
    typer.echo(f"用户目录: {app_cfg.user_data_dir}")
    typer.echo("")
    typer.echo("[运行预算]")
    typer.echo(f"  max_search_pages_per_keyword: {runtime_cfg.budget.max_search_pages_per_keyword}")
    typer.echo(f"  max_job_details_per_run: {runtime_cfg.budget.max_job_details_per_run}")
    typer.echo(f"  max_total_pages_per_run: {runtime_cfg.budget.max_total_pages_per_run}")
    typer.echo(f"  max_runtime_minutes: {runtime_cfg.budget.max_runtime_minutes}")
    typer.echo(f"  max_errors_per_run: {runtime_cfg.budget.max_errors_per_run}")
    typer.echo(
        f"  max_consecutive_parse_failures: {runtime_cfg.budget.max_consecutive_parse_failures}"
    )
    typer.echo("[页面间隔]")
    typer.echo(f"  min_seconds: {runtime_cfg.page_interval.min_seconds}")
    typer.echo(f"  max_seconds: {runtime_cfg.page_interval.max_seconds}")
    typer.echo(f"[重访冷却] cooldown_hours: {runtime_cfg.revisit.cooldown_hours}")
    typer.echo("[浏览器]")
    typer.echo(f"  user_data_dir: {runtime_cfg.browser.user_data_dir}")
    typer.echo(f"  headless: {runtime_cfg.browser.headless}  (强制 false)")
    typer.echo(f"  single_context: {runtime_cfg.browser.single_context}  (强制 true)")
    typer.echo(f"  single_account: {runtime_cfg.browser.single_account}  (强制 true)")
    typer.echo("[运行控制]")
    typer.echo(f"  require_user_confirm: {runtime_cfg.run_control.require_user_confirm}")
    typer.echo(f"  allow_unattended: {runtime_cfg.run_control.allow_unattended}  (强制 false)")
    typer.echo(f"  allow_background: {runtime_cfg.run_control.allow_background}  (强制 false)")
    typer.echo("")
    typer.echo(f"[关键词] {kw_cfg.keywords}")
    typer.echo(f"[中心点] {loc_cfg.center_name} ({loc_cfg.city} {loc_cfg.district})")
    typer.echo(f"        经纬度=({loc_cfg.center_longitude}, {loc_cfg.center_latitude})")
    typer.echo(f"        半径={loc_cfg.radius_m}m  服务={loc_cfg.geo_provider}")
    typer.echo(f"[评分权重] {score_cfg.weights} 总和={sum(score_cfg.weights.values())}")
    typer.echo("[数据库]")
    typer.echo(f"  sqlite_path: {app_cfg.database.sqlite_path}")
    typer.echo(f"  foreign_keys: {app_cfg.database.foreign_keys}")
    typer.echo("[日志]")
    typer.echo(f"  level: {app_cfg.logging.level}")
    typer.echo(f"  console_enabled: {app_cfg.logging.console_enabled}")
    typer.echo(f"  file_enabled: {app_cfg.logging.file_enabled}")
    typer.echo(f"  file_name: {app_cfg.logging.file_name}")
    typer.echo("")
    typer.echo("[敏感信息]")
    typer.echo("  本工具不读取或打印 Cookie、API key、验证码等敏感信息。")
    typer.echo("  高德 API key 仅通过环境变量 AMAP_API_KEY 注入，不在配置文件中。")


# ==================== 浏览器登录（P1） ====================
@app.command(name="browser-login")
def browser_login(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
    home_url: str | None = typer.Option(
        None,
        "--home-url",
        help="覆盖首页 URL（仅本地测试或显式覆盖；生产仅允许 BOSS 白名单域名）",
    ),
) -> None:
    """启动可见浏览器，用户手动登录后输入 confirm。

    流程：
    1. 加载配置
    2. 初始化日志
    3. 启动可见浏览器（headless=False）
    4. 打开首页（BOSS 直聘白名单域名）
    5. 用户在浏览器手动完成登录/扫码/验证
    6. 回到终端输入 confirm / quit / status

    P1.1：命令循环改为非阻塞轮询，浏览器关闭后无需用户在终端按回车即可退出。

    安全规则：
    - 不自动处理任何验证码 / 滑块 / 短信
    - 不自动登录、不自动重试
    - confirm 仅代表用户自述已处理完成，不代表程序判断登录成功
    - Ctrl+C 或关闭浏览器均安全退出
    """
    cfg = _load_config_safe(config_dir)
    _ensure_logging(cfg)

    runtime_cfg = cfg["runtime"]
    browser_cfg = runtime_cfg.browser
    run_control_cfg = runtime_cfg.run_control

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

    # 解析首页 URL：--home-url 优先，否则使用配置
    effective_home_url = home_url or browser_cfg.home_url
    # 统一调用 validate_home_url 严格校验（scheme/host/userinfo/port/query/fragment/path）
    try:
        effective_home_url = validate_home_url(effective_home_url)
    except ValueError as e:
        typer.echo(f"[ERROR] home_url 校验失败: {e}", err=True)
        raise typer.Exit(code=2) from e

    # 项目根目录（用于用户目录安全校验）
    project_root = _default_config_dir().parent

    manager = BrowserManager(
        browser_cfg.model_copy(update={"home_url": effective_home_url}),
        project_root=project_root,
    )

    typer.echo("=" * 60)
    typer.echo("boss-tool · browser-login 人工登录会话")
    typer.echo("=" * 60)
    typer.echo(f"用户目录: {manager.user_data_dir}")
    # P1.1：输出脱敏后的 URL（防御性，即使严格校验也保持脱敏习惯）
    typer.echo(f"首页:     {redact_url(effective_home_url)}")
    typer.echo("会话模式: 可见浏览器 / 单账号 / 人工确认")
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
    typer.echo("请在浏览器中手动完成登录、扫码或安全验证。")
    typer.echo("完成后回到终端输入 confirm。")
    typer.echo("输入 quit 可退出。")
    typer.echo("-" * 60)

    # P1.1：使用非阻塞命令循环
    # 生产环境使用 ThreadedCommandSource（daemon 输入线程 + queue.Queue）
    # 测试可通过 _run_command_loop 的 command_source 参数注入 FakeCommandSource
    command_source: CommandSource = ThreadedCommandSource(
        prompt="请输入命令 (confirm/quit/status): "
    )
    command_source.start()

    try:
        _run_command_loop(manager, session, command_source=command_source)
    except KeyboardInterrupt:
        typer.echo("\n[INFO] 收到 Ctrl+C，正在安全退出...")
        manager.close(stop_reason=StopReason.USER_ABORTED)
    except Exception as e:
        typer.echo(f"[ERROR] 会话异常: {type(e).__name__}", err=True)
        manager.close(stop_reason=StopReason.UNKNOWN_ERROR, error_message=str(type(e).__name__))
    finally:
        # 停止命令源（不强制终止线程，线程为 daemon 会自动结束）
        command_source.stop()
        # 确保资源已释放
        if manager.is_running:
            manager.close(stop_reason=StopReason.USER_ABORTED)
        final = manager.session
        if final is not None:
            typer.echo("-" * 60)
            typer.echo(f"会话状态: {final.state}")
            typer.echo(f"用户确认: {final.user_confirmed}")
            typer.echo(f"用户关闭: {final.browser_closed_by_user}")
            typer.echo(f"停止原因: {final.stop_reason}")
            typer.echo(f"关闭来源: {final.close_source}")
            if final.error_message:
                typer.echo(f"异常:     {final.error_message}")
            typer.echo(
                "注意：本工具仅减少不必要访问与程序失控风险，"
                "不能保证账号不受限制，不得用于规避平台检测。"
            )


def _run_command_loop(
    manager: BrowserManager,
    session,
    *,
    command_source: CommandSource | None = None,
    poll_interval: float = 0.2,
) -> None:
    """非阻塞命令循环（P1.1 重构）。

    主线程每隔 poll_interval 秒检查：
    1. manager.is_running（浏览器/context 是否还在）
    2. session 是否终态
    3. 命令队列是否有数据

    浏览器或唯一页面被关闭后，主线程立即结束循环，无需用户再次按回车。

    Args:
        manager: BrowserManager 实例
        session: BrowserSession 实例
        command_source: 命令源（生产为 ThreadedCommandSource，测试为 FakeCommandSource）
        poll_interval: 轮询间隔秒数
    """
    if command_source is None:
        # 兜底：未注入则使用 fake（生产路径在 browser_login 中已显式传入）
        command_source = FakeCommandSource()

    while True:
        # 1. 检测浏览器是否已被关闭（无需用户输入即可退出）
        if not manager.is_running:
            typer.echo("[INFO] 浏览器已关闭（由用户或异常触发），会话结束。")
            return

        # 2. 检测 session 是否终态
        if session.state.is_terminal():
            typer.echo("[INFO] 会话已结束。")
            return

        # 3. 轮询命令（阻塞 poll_interval 秒，无命令则继续循环）
        cmd = command_source.poll(timeout=poll_interval)
        if cmd is None:
            # 无命令，继续轮询检测浏览器状态
            continue

        cmd = cmd.strip().lower()

        if not cmd:
            # 空输入不视为确认
            typer.echo("[提示] 空输入无效。请输入 confirm / quit / status。")
            continue

        if cmd == "confirm":
            manager.confirm_user()
            typer.echo("[OK] 已记录用户确认。state=user_confirmed")
            typer.echo("注意：confirm 仅代表用户自述已处理完成，不代表程序判断登录成功。")
            # confirm 后继续保留会话，用户可继续操作或 quit 退出
            continue

        if cmd == "quit":
            typer.echo("[INFO] 用户主动退出，正在安全关闭...")
            manager.close(stop_reason=StopReason.USER_ABORTED)
            return

        if cmd == "status":
            s = manager.session
            if s is not None:
                typer.echo(f"  session_id:          {s.session_id}")
                typer.echo(f"  state:               {s.state}")
                typer.echo(f"  user_confirmed:      {s.user_confirmed}")
                typer.echo(f"  browser_closed_by_user: {s.browser_closed_by_user}")
                typer.echo(f"  close_source:        {s.close_source}")
                typer.echo(f"  started_at:          {s.started_at}")
                typer.echo(f"  ended_at:            {s.ended_at}")
                typer.echo(f"  stop_reason:         {s.stop_reason}")
                # last_known_url 已脱敏
                typer.echo(f"  last_known_url:      {s.last_known_url}")
            continue

        typer.echo(f"[提示] 未知命令: {cmd!r}。仅接受 confirm / quit / status。")


# ==================== 预留命令（P0 未实现） ====================
def _not_implemented(name: str) -> None:
    """预留命令统一输出。"""
    typer.echo(f"该功能尚未在 P0 实现。 (command: {name})")
    raise typer.Exit(code=0)


@app.command()
def run(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
) -> None:
    """[预留] 启动采集。P0 阶段未实现。"""
    _not_implemented("run")


@app.command()
def resume(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
) -> None:
    """[预留] 恢复中断的采集。P0 阶段未实现。"""
    _not_implemented("resume")


@app.command()
def export(
    config_dir: Path | None = typer.Option(None, "--config-dir", "-c", help="配置目录"),
) -> None:
    """[预留] 导出 Excel。P0 阶段未实现。"""
    _not_implemented("export")


# ==================== 工具函数 ====================
def _check_writable(test_file: Path) -> None:
    """检查目录可写。"""
    test_file.write_text("doctor", encoding="utf-8")
    test_file.unlink()


__all__ = ["app"]
