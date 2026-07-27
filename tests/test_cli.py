"""CLI 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from boss_tool.cli import app


@pytest.fixture
def runner():
    return CliRunner()


class TestHelpCommand:
    def test_help_succeeds(self, runner: CliRunner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "boss-tool" in result.output
        assert "doctor" in result.output
        assert "init-db" in result.output
        assert "show-config" in result.output

    def test_no_args_shows_help(self, runner: CliRunner):
        result = runner.invoke(app, [])
        # typer no_args_is_help=True 时无参数会显示帮助
        # exit code 为 0 或 2 都可接受（typer 版本差异）
        assert result.exit_code in (0, 2)
        assert (
            "Usage" in result.output or "Commands" in result.output or "boss-tool" in result.output
        )


class TestDefaultConfigDir:
    def test_default_config_dir_points_to_project_config(self, real_config_dir: Path):
        """回归测试：默认 config 目录必须指向项目根 config/。

        历史 bug：路径解析多算了一层 .parent，导致默认目录指向
        D:\\A_pachong\\config 而非 D:\\A_pachong\\boss-job-assistant\\config。
        """
        from boss_tool.cli import _default_config_dir

        default_dir = _default_config_dir()
        assert default_dir.exists()
        assert default_dir.is_dir()
        assert (default_dir / "app.yaml").exists()
        # 与真实配置目录一致
        assert default_dir.resolve() == real_config_dir.resolve()


class TestDoctorCommand:
    def test_doctor_runs_without_network(self, runner: CliRunner, real_config_dir: Path):
        # doctor 命令不应访问网络
        result = runner.invoke(app, ["doctor", "--config-dir", str(real_config_dir)])
        # 即使有 warning（playwright 未安装）也不应失败
        assert result.exit_code == 0
        assert "doctor" in result.output
        assert "Python" in result.output
        assert "配置加载" in result.output
        assert "SQLite" in result.output

    def test_doctor_with_missing_config(self, runner: CliRunner, tmp_workspace: Path):
        empty_dir = tmp_workspace / "config"
        empty_dir.mkdir()
        result = runner.invoke(app, ["doctor", "--config-dir", str(empty_dir)])
        # 配置加载失败时 _load_config_safe 调用 typer.Exit(code=2)
        # doctor 捕获后重新抛出，所以最终 exit_code 应为 2
        assert result.exit_code == 2
        assert "配置" in result.output


class TestInitDbCommand:
    def test_init_db_success(self, runner: CliRunner, copied_config_dir: Path, tmp_workspace: Path):
        # 修改 app.yaml 让 data_dir 指向临时目录
        import yaml

        app_path = copied_config_dir / "app.yaml"
        data = yaml.safe_load(app_path.read_text(encoding="utf-8"))
        data["app"]["data_dir"] = str(tmp_workspace / "data")
        data["app"]["logs_dir"] = str(tmp_workspace / "logs")
        data["app"]["output_dir"] = str(tmp_workspace / "output")
        data["app"]["user_data_dir"] = str(tmp_workspace / "user_data")
        app_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        result = runner.invoke(app, ["init-db", "--config-dir", str(copied_config_dir)])
        assert result.exit_code == 0, result.output
        assert "数据库已初始化" in result.output


class TestShowConfigCommand:
    def test_show_config_runs(self, runner: CliRunner, real_config_dir: Path):
        result = runner.invoke(app, ["show-config", "--config-dir", str(real_config_dir)])
        assert result.exit_code == 0
        assert "show-config" in result.output
        assert "boss-tool" in result.output
        assert "headless: False" in result.output
        assert "single_context: True" in result.output
        assert "single_account: True" in result.output
        assert "allow_unattended: False" in result.output

    def test_show_config_does_not_print_api_key(self, runner: CliRunner, real_config_dir: Path):
        result = runner.invoke(app, ["show-config", "--config-dir", str(real_config_dir)])
        assert result.exit_code == 0
        # 不应直接打印 api key
        assert "AMAP_API_KEY" in result.output or "敏感信息" in result.output
        # 但不应出现具体 key 值
        assert "abcd1234" not in result.output


class TestReservedCommands:
    def test_run_command_prints_not_implemented(self, runner: CliRunner, real_config_dir: Path):
        result = runner.invoke(app, ["run", "--config-dir", str(real_config_dir)])
        assert result.exit_code == 0
        assert "尚未在 P0 实现" in result.output

    def test_resume_command_prints_not_implemented(self, runner: CliRunner, real_config_dir: Path):
        result = runner.invoke(app, ["resume", "--config-dir", str(real_config_dir)])
        assert result.exit_code == 0
        assert "尚未在 P0 实现" in result.output

    def test_export_command_prints_not_implemented(self, runner: CliRunner, real_config_dir: Path):
        result = runner.invoke(app, ["export", "--config-dir", str(real_config_dir)])
        assert result.exit_code == 0
        assert "尚未在 P0 实现" in result.output


class TestLoggingSetup:
    def test_setup_logging_no_duplicate_handlers(self, real_config_dir: Path):

        from boss_tool.config import load_config
        from boss_tool.logging_config import setup_logging

        cfg = load_config(real_config_dir)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            logger1 = setup_logging(cfg["app"].logging, tmp, force=True)
            n1 = len(logger1.handlers)
            # 二次调用（不 force）不应重复添加
            logger2 = setup_logging(cfg["app"].logging, tmp, force=False)
            n2 = len(logger2.handlers)
            assert logger1 is logger2
            assert n1 == n2
            # 清理
            for h in list(logger1.handlers):
                logger1.removeHandler(h)
                h.close()
