"""Typer CLI 入口。

提供命令：
- --help
- doctor         # 健康检查（不访问网络）
- init-db        # 初始化数据库
- show-config    # 显示配置（敏感字段脱敏）
- run            # 预留（P0 未实现）
- resume         # 预留（P0 未实现）
- export         # 预留（P0 未实现）

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

from boss_tool.config import load_config
from boss_tool.logging_config import get_logger, setup_logging
from boss_tool.storage.database import CURRENT_SCHEMA_VERSION, Database

app = typer.Typer(
    name="boss-tool",
    help="BOSS直聘岗位辅助采集与筛选工具 - P0 项目骨架",
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
        "注意：本工具仅减少不必要访问与程序失控风险，不能保证账号不受限制，"
        "不得用于规避平台检测。"
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
