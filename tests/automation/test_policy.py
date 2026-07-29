"""policy 模块测试：路径围栏、命令白名单、shell 注入拒绝、网络/BOSS 门禁。

覆盖规格第十节：1/2/3/4/12/13 以及 risk_level / requires_human 门禁。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automation.policy import (
    PolicyViolation,
    audit_task,
    is_command_allowed,
    resolve_safe_path,
)
from automation.schemas import AutomationConfig, PlannerOutput


@pytest.fixture
def cfg() -> AutomationConfig:
    return AutomationConfig(
        project_goal="demo",
        allowed_paths=["src", "tests", "automation"],
        denied_paths=[".git", ".env", "browser_profiles", "cookies", "node_modules", ".venv"],
        allowed_commands=[
            "python",
            "pytest",
            "ruff",
            "mypy",
            "git status",
            "git diff",
            "git add",
            "git commit",
        ],
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """模拟项目根目录，预置 allowed 子目录。"""
    for sub in ("src", "tests", "automation"):
        (tmp_path / sub).mkdir()
    return tmp_path


# ==================== 路径围栏 ====================
class TestPathFence:
    def test_valid_relative_path_accepted(self, cfg: AutomationConfig, root: Path) -> None:
        p = resolve_safe_path("tests/automation/_demo.txt", cfg, root)
        assert p is not None
        assert p.parent.name == "automation"

    def test_traversal_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        assert resolve_safe_path("tests/../../etc/passwd", cfg, root) is None

    def test_double_dot_in_middle_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        assert resolve_safe_path("src/../.git/config", cfg, root) is None

    @pytest.mark.parametrize(
        "abs_path", ["C:\\Windows\\system32\\x", "/etc/passwd", "\\\\server\\share\\x"]
    )
    def test_absolute_path_rejected(self, cfg: AutomationConfig, root: Path, abs_path: str) -> None:
        assert resolve_safe_path(abs_path, cfg, root) is None

    def test_denied_path_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        assert resolve_safe_path(".git/config", cfg, root) is None
        assert resolve_safe_path(".env", cfg, root) is None
        assert resolve_safe_path("browser_profiles/x", cfg, root) is None

    def test_path_outside_allowed_roots_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        # browser_profiles 在 denied，但即便不在 denied，也不在 allowed_paths 内
        (root / "other").mkdir()
        assert resolve_safe_path("other/x", cfg, root) is None

    def test_path_must_stay_under_project_root(self, cfg: AutomationConfig, root: Path) -> None:
        # 即使 allowed_paths 含 src，src/../../../ 逃逸也要拒绝
        assert resolve_safe_path("src/../../../outside", cfg, root) is None


# ==================== 命令白名单 ====================
class TestCommandWhitelist:
    def test_whitelisted_with_args_accepted(self, cfg: AutomationConfig) -> None:
        assert is_command_allowed(["pytest", "-q", "tests/automation"], cfg) is True

    def test_plain_whitelisted_accepted(self, cfg: AutomationConfig) -> None:
        assert is_command_allowed(["ruff", "check", "src"], cfg) is True

    def test_not_whitelisted_rejected(self, cfg: AutomationConfig) -> None:
        assert is_command_allowed(["rm", "-rf", "/"], cfg) is False
        assert is_command_allowed(["curl", "https://x"], cfg) is False

    def test_git_push_not_whitelisted_rejected(self, cfg: AutomationConfig) -> None:
        # 只允许 git status/diff/add/commit，不允许 git push/reset/clean
        assert is_command_allowed(["git", "push"], cfg) is False
        assert is_command_allowed(["git", "reset", "--hard"], cfg) is False
        assert is_command_allowed(["git", "clean", "-fd"], cfg) is False

    def test_git_status_accepted(self, cfg: AutomationConfig) -> None:
        assert is_command_allowed(["git", "status"], cfg) is True
        assert is_command_allowed(["git", "status", "--short"], cfg) is True

    @pytest.mark.parametrize(
        "argv",
        [
            ["pytest", "|", "grep", "x"],
            ["pytest", ">", "out.txt"],
            ["pytest", ">>", "out.txt"],
            ["pytest", "<", "in.txt"],
            ["pytest", ";", "rm", "-rf", "/"],
            ["echo", "$(rm -rf /)"],
            ["echo", "`whoami`"],
            ["pytest", "&&", "echo", "x"],
            ["pytest", "||", "echo", "x"],
            ["pytest\nrm -rf /"],
        ],
    )
    def test_shell_metachars_rejected(self, cfg: AutomationConfig, argv: list[str]) -> None:
        assert is_command_allowed(argv, cfg) is False

    def test_empty_argv_rejected(self, cfg: AutomationConfig) -> None:
        assert is_command_allowed([], cfg) is False


# ==================== 任务级审计 ====================
def _make_task(
    *,
    files_allowed: list[str],
    commands: list[list[str]],
    risk_level: str = "low",
    requires_network: bool = False,
    requires_human: bool = False,
) -> PlannerOutput:
    return PlannerOutput.model_validate(
        {
            "status": "continue",
            "task_id": "T-1",
            "summary": "s",
            "reason": "r",
            "files_allowed": files_allowed,
            "steps": ["s1"],
            "commands": [{"argv": c, "timeout_seconds": 60, "purpose": "p"} for c in commands],
            "acceptance_criteria": ["c1"],
            "risk_level": risk_level,
            "requires_network": requires_network,
            "requires_human": requires_human,
        }
    )


class TestTaskAudit:
    def test_clean_task_passes(self, cfg: AutomationConfig, root: Path) -> None:
        task = _make_task(files_allowed=["tests/automation/_demo.txt"], commands=[["pytest", "-q"]])
        violations = audit_task(task, cfg, root)
        assert violations == []

    def test_high_risk_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        task = _make_task(
            files_allowed=["tests/automation/x"], commands=[["pytest"]], risk_level="high"
        )
        violations = audit_task(task, cfg, root)
        assert any("risk" in v.lower() for v in violations)

    def test_requires_human_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        task = _make_task(
            files_allowed=["tests/automation/x"], commands=[["pytest"]], requires_human=True
        )
        violations = audit_task(task, cfg, root)
        assert any("human" in v.lower() for v in violations)

    def test_network_task_rejected_when_disabled(self, cfg: AutomationConfig, root: Path) -> None:
        # cfg.network_access 默认 False
        task = _make_task(
            files_allowed=["tests/automation/x"], commands=[["pytest"]], requires_network=True
        )
        violations = audit_task(task, cfg, root)
        assert any("network" in v.lower() for v in violations)

    def test_network_task_allowed_when_enabled(self, root: Path) -> None:
        cfg = AutomationConfig(
            project_goal="demo",
            allowed_paths=["src", "tests", "automation"],
            denied_paths=[".git"],
            allowed_commands=["pytest"],
            network_access=True,
        )
        task = _make_task(
            files_allowed=["tests/automation/x"], commands=[["pytest"]], requires_network=True
        )
        assert audit_task(task, cfg, root) == []

    def test_real_boss_domain_rejected_when_disabled(
        self, cfg: AutomationConfig, root: Path
    ) -> None:
        # live_boss_access 默认 False；命令引用 zhipin.com 应被拒
        task = _make_task(
            files_allowed=["tests/automation/x"],
            commands=[["pytest"], ["python", "-c", "open('https://www.zhipin.com/')"]],
        )
        violations = audit_task(task, cfg, root)
        assert any("boss" in v.lower() or "zhipin" in v.lower() for v in violations)

    def test_file_outside_fence_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        task = _make_task(files_allowed=[".env"], commands=[["pytest"]])
        violations = audit_task(task, cfg, root)
        assert any("path" in v.lower() or "file" in v.lower() for v in violations)

    def test_non_whitelisted_command_rejected(self, cfg: AutomationConfig, root: Path) -> None:
        task = _make_task(files_allowed=["tests/automation/x"], commands=[["rm", "-rf", "/"]])
        violations = audit_task(task, cfg, root)
        assert any("command" in v.lower() for v in violations)

    def test_policy_violation_is_exception(self) -> None:
        assert issubclass(PolicyViolation, Exception)
