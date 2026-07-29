"""executor 测试：超时终止、命令拒绝、dry-run 不落盘不执行。

覆盖规格第十节：5（超时命令被终止）、11（dry-run 不修改文件/不运行真实命令）、3/4（命令拒绝）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.executor import CommandResult, Executor
from automation.policy import PolicyViolation
from automation.schemas import AutomationConfig


def _cfg(**overrides) -> AutomationConfig:
    base = {
        "project_goal": "demo",
        "allowed_paths": ["src", "tests", "automation"],
        "denied_paths": [".git", ".env"],
        "allowed_commands": ["python", "pytest", "git status", "git diff"],
    }
    base.update(overrides)
    return AutomationConfig(**base)


# ==================== dry-run ====================
class TestDryRun:
    def test_dry_run_does_not_spawn_subprocess(self, tmp_path: Path, monkeypatch) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=True)
        called = {"n": 0}

        def fake_run(*a, **kw):
            called["n"] += 1
            return subprocess.CompletedProcess(a[0], 0, b"", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = ex.run_command(["python", "-c", "print(1)"], timeout=10, purpose="t")
        assert called["n"] == 0
        assert result.dry_run is True
        assert result.timed_out is False

    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=True)
        target = tmp_path / "SHOULD_NOT_EXIST"
        ex.run_command(["python", "-c", f"open(r'{target}','w').close()"], timeout=10, purpose="t")
        assert not target.exists()

    def test_dry_run_returns_skipped_result(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=True)
        r = ex.run_command(["pytest", "-q"], timeout=30, purpose="t")
        assert isinstance(r, CommandResult)
        assert r.exit_code == 0
        assert r.dry_run is True


# ==================== 命令拒绝 ====================
class TestCommandRejection:
    def test_non_whitelisted_raises(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        with pytest.raises(PolicyViolation):
            ex.run_command(["rm", "-rf", "/"], timeout=10, purpose="t")

    def test_shell_metachar_raises(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        with pytest.raises(PolicyViolation):
            ex.run_command(["pytest", "|", "grep", "x"], timeout=10, purpose="t")

    def test_no_shell_true_used(self, tmp_path: Path, monkeypatch) -> None:
        """执行器永远不得使用 shell=True。"""
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        seen = {}

        def fake_run(argv, **kw):
            seen["shell"] = kw.get("shell", False)
            return subprocess.CompletedProcess(argv, 0, b"ok\n", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ex.run_command(["python", "-c", "print(1)"], timeout=10, purpose="t")
        assert seen["shell"] is False


# ==================== 真实执行 ====================
class TestRealExecution:
    def test_successful_command(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        r = ex.run_command(["python", "-c", "print(2+2)"], timeout=15, purpose="t")
        assert r.exit_code == 0
        assert "4" in r.stdout
        assert r.timed_out is False
        assert r.dry_run is False

    def test_failed_command_exit_code(self, tmp_path: Path) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        # 使用 raise SystemExit 避免 -c 代码中出现 shell 元字符 ';'
        r = ex.run_command(["python", "-c", "raise SystemExit(3)"], timeout=15, purpose="t")
        assert r.exit_code == 3
        assert r.timed_out is False

    def test_timeout_terminates_command(self, tmp_path: Path) -> None:
        # 写一个 sleep 脚本，避免 -c 代码中出现 shell 元字符
        script = tmp_path / "_sleep.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        # 用 "python"（PATH 解析），与规划器实际下发的形式一致
        r = ex.run_command(["python", str(script)], timeout=1, purpose="t")
        assert r.timed_out is True
        # 超时后进程应被终止，退出码为 None 或非 0
        assert r.exit_code != 0 or r.exit_code is None

    def test_cwd_is_project_root(self, tmp_path: Path, monkeypatch) -> None:
        ex = Executor(config=_cfg(), project_root=tmp_path, dry_run=False)
        seen = {}

        def fake_run(argv, **kw):
            seen["cwd"] = kw.get("cwd")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ex.run_command(["python", "-c", "print(1)"], timeout=10, purpose="t")
        assert Path(seen["cwd"]) == tmp_path
