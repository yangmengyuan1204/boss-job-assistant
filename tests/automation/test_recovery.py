"""恢复与日志脱敏测试。

覆盖规格第十节：
- 14：日志脱敏有效（API Key / Authorization / Cookie / 手机号 / 邮箱 / 身份证）
- 9：断点恢复不会重复执行已完成任务
- 10：连续失败触发熔断
- 15：Git 工作区不干净时按配置停止
- 16：审核失败时不会提交代码
- 11：dry-run 不会修改文件或运行真实命令
"""

from __future__ import annotations

import json
from pathlib import Path

from automation.logger import RunLogger, sanitize


# ==================== 日志脱敏 ====================
class TestSanitize:
    def test_plain_text_unchanged(self) -> None:
        assert sanitize("运行 pytest 通过") == "运行 pytest 通过"

    def test_openai_api_key_redacted(self) -> None:
        s = "OPENAI_API_KEY=sk-proj-AbCd1234567890XYz endpoint called"
        out = sanitize(s)
        assert "sk-proj-AbCd1234567890XYz" not in out
        assert "[REDACTED]" in out

    def test_authorization_header_redacted(self) -> None:
        s = "Authorization: Bearer eyJhbGciOi supersecret"
        out = sanitize(s)
        assert "eyJhbGciOi" not in out
        assert "Bearer" not in out or "[REDACTED]" in out

    def test_cookie_redacted(self) -> None:
        s = "Cookie: session=abc123; token=xyz"
        out = sanitize(s)
        assert "abc123" not in out
        assert "xyz" not in out

    def test_phone_redacted(self) -> None:
        s = "联系人电话 13812345678 请勿记录"
        out = sanitize(s)
        assert "13812345678" not in out
        assert "[REDACTED]" in out

    def test_email_redacted(self) -> None:
        s = "发送到 user@example.com 处理"
        out = sanitize(s)
        assert "user@example.com" not in out
        assert "[REDACTED]" in out

    def test_id_card_redacted(self) -> None:
        s = "身份证号 110101199003071234 已验证"
        out = sanitize(s)
        assert "110101199003071234" not in out
        assert "[REDACTED]" in out

    def test_password_redacted(self) -> None:
        s = "password=hunter2 token=secretval"
        out = sanitize(s)
        assert "hunter2" not in out
        assert "secretval" not in out

    def test_combined_sensitive(self) -> None:
        s = "key sk-123 user@a.b 13900001111 110101199003071234 Authorization: Bearer Z"
        out = sanitize(s)
        assert "sk-123" not in out
        assert "user@a.b" not in out
        assert "13900001111" not in out
        assert "110101199003071234" not in out
        assert "Bearer Z" not in out


# ==================== RunLogger 结构化日志 ====================
class TestRunLogger:
    def test_events_jsonl_appended(self, tmp_path: Path) -> None:
        logger = RunLogger(run_dir=tmp_path)
        logger.log_event("plan", iteration=1, summary="x")
        logger.log_event("execute", iteration=1, ok=True)
        lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        e0 = json.loads(lines[0])
        assert e0["event"] == "plan"
        assert e0["iteration"] == 1

    def test_saves_artifacts(self, tmp_path: Path) -> None:
        logger = RunLogger(run_dir=tmp_path)
        logger.save_planner_request({"prompt": "p"})
        logger.save_planner_response({"status": "continue"})
        logger.save_reviewer_request({"r": 1})
        logger.save_reviewer_response({"decision": "continue"})
        logger.save_command_results([{"argv": ["pytest"], "exit_code": 0}])
        logger.save_diff("--- a\n+++ b\n")
        logger.write_final_report("# Final\nok")
        assert (tmp_path / "planner_request.json").exists()
        assert (tmp_path / "planner_response.json").exists()
        assert (tmp_path / "reviewer_request.json").exists()
        assert (tmp_path / "reviewer_response.json").exists()
        assert (tmp_path / "command_results.json").exists()
        assert (tmp_path / "diff.patch").exists()
        assert (tmp_path / "final_report.md").read_text(encoding="utf-8").startswith("# Final")

    def test_events_are_sanitized(self, tmp_path: Path) -> None:
        logger = RunLogger(run_dir=tmp_path)
        logger.log_event("plan", detail="Authorization: Bearer secretXYZ")
        raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert "secretXYZ" not in raw

    def test_planner_response_sanitized(self, tmp_path: Path) -> None:
        logger = RunLogger(run_dir=tmp_path)
        logger.save_planner_response({"reason": "call sk-AbCdE123456 done"})
        raw = (tmp_path / "planner_response.json").read_text(encoding="utf-8")
        assert "sk-AbCdE123456" not in raw


# ==================== Git 管理器 ====================
from automation.git_manager import (  # noqa: E402
    DryRunGitBackend,
    GitManager,
    SubprocessGitBackend,
)


class _FakeBackend:
    """可编程假后端，用于测试 GitManager 行为。"""

    def __init__(self, *, clean: bool = True, head: str = "abc123") -> None:
        self._clean = clean
        self._head = head
        self.added: list[list[str]] = []
        self.commits: list[str] = []
        self.diff_text = ""

    def is_available(self) -> bool:
        return True

    def is_clean(self, repo: Path) -> bool:
        return self._clean

    def diff(self, repo: Path) -> str:
        return self.diff_text

    def add(self, repo: Path, paths: list[str]) -> None:
        self.added.append(list(paths))

    def commit(self, repo: Path, message: str) -> str:
        self.commits.append(message)
        return "newsha" + str(len(self.commits))

    def head_sha(self, repo: Path) -> str:
        return self._head


class TestGitManager:
    def test_dry_run_backend_reports_clean_and_no_commit(self, tmp_path: Path) -> None:
        gm = GitManager(DryRunGitBackend())
        assert gm.ensure_clean_start(tmp_path, require=True) is True
        # dry-run 下 commit_changes 不应真正提交
        sha = gm.commit_changes(tmp_path, ["src/x.py"], "msg", enabled=True)
        assert sha is None

    def test_ensure_clean_start_blocks_when_dirty(self, tmp_path: Path) -> None:
        gm = GitManager(_FakeBackend(clean=False))
        assert gm.ensure_clean_start(tmp_path, require=True) is False

    def test_ensure_clean_start_skipped_when_not_required(self, tmp_path: Path) -> None:
        gm = GitManager(_FakeBackend(clean=False))
        assert gm.ensure_clean_start(tmp_path, require=False) is True

    def test_commit_disabled_returns_none(self, tmp_path: Path) -> None:
        backend = _FakeBackend()
        gm = GitManager(backend)
        sha = gm.commit_changes(tmp_path, ["src/x.py"], "msg", enabled=False)
        assert sha is None
        assert backend.commits == []

    def test_commit_enabled_adds_and_commits(self, tmp_path: Path) -> None:
        backend = _FakeBackend()
        gm = GitManager(backend)
        sha = gm.commit_changes(tmp_path, ["src/x.py", "tests/y.py"], "P3: x", enabled=True)
        assert sha is not None
        assert backend.added == [["src/x.py", "tests/y.py"]]
        assert backend.commits == ["P3: x"]

    def test_subprocess_backend_available_flag(self) -> None:
        # 不要求系统装了 git，仅验证 is_available 返回布尔且不抛异常
        backend = SubprocessGitBackend()
        assert isinstance(backend.is_available(), bool)


# ==================== Orchestrator 恢复与熔断 ====================
from automation.executor import CommandResult  # noqa: E402
from automation.orchestrator import Orchestrator  # noqa: E402
from automation.planner_client import MockPlanner  # noqa: E402
from automation.reviewer_client import MockReviewer  # noqa: E402
from automation.state_store import StateStore  # noqa: E402


def _planner_task(task_id: str, status: str = "continue", files: list[str] | None = None) -> dict:
    return {
        "status": status,
        "task_id": task_id,
        "summary": f"任务 {task_id}",
        "reason": "测试",
        "files_allowed": files if files is not None else ["tests/automation/_demo.txt"],
        "steps": ["执行"],
        "commands": [{"argv": ["pytest", "-q"], "timeout_seconds": 60, "purpose": "测试"}],
        "acceptance_criteria": ["通过"],
        "risk_level": "low",
        "requires_network": False,
        "requires_human": False,
    }


class _RecordingExecutor:
    """记录被执行的 task_id，可控制成功/失败。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.executed: list[str] = []
        self.fail = fail
        self.dry_run = False

    def execute_task_commands(self, task) -> list[CommandResult]:
        self.executed.append(task.task_id)
        return [
            CommandResult(
                argv=cmd.argv,
                exit_code=1 if self.fail else 0,
                stdout="" if self.fail else "ok",
                stderr="fail" if self.fail else "",
                timed_out=False,
                dry_run=False,
                duration_seconds=0.0,
                purpose=cmd.purpose,
            )
            for cmd in task.commands
        ]


def _build_orchestrator(
    tmp_path: Path,
    *,
    planner_outputs: list[dict],
    reviewer_decisions: list[dict],
    config_overrides: dict | None = None,
    clean_git: bool = True,
    executor_fail: bool = False,
    pre_state_completed: list[str] | None = None,
    dry_run: bool = True,
) -> tuple[Orchestrator, _RecordingExecutor, _RecordingBackend]:
    from automation.schemas import AutomationConfig

    base = {
        "project_goal": "demo",
        "allowed_paths": ["src", "tests", "automation"],
        "denied_paths": [".git", ".env"],
        "allowed_commands": ["python", "pytest", "ruff", "git status", "git diff"],
        "max_iterations": 5,
        "max_consecutive_failures": 3,
        "require_clean_git_start": True,
        "commit_on_success": True,
        "dry_run": dry_run,
    }
    if config_overrides:
        base.update(config_overrides)
    cfg = AutomationConfig(**base)
    backend = _RecordingBackend(clean=clean_git)
    git_mgr = GitManager(backend)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    store = StateStore(state_dir=state_dir, run_id="run-test")
    # 预置状态（用于恢复测试）
    if pre_state_completed is not None:
        from automation.schemas import RunState

        store.save(
            RunState(
                run_id="run-test",
                start_time="2026-07-29T00:00:00",
                current_iteration=1,
                completed_tasks=pre_state_completed,
            )
        )
    logger = RunLogger(run_dir=tmp_path / "runs" / "run-test")
    ex = _RecordingExecutor(fail=executor_fail)
    orch = Orchestrator(
        config=cfg,
        project_root=tmp_path,
        planner=MockPlanner(planner_outputs),
        reviewer=MockReviewer(reviewer_decisions),
        executor=ex,
        git_manager=git_mgr,
        state_store=store,
        logger=logger,
        run_id="run-test",
    )
    return orch, ex, backend


class _RecordingBackend:
    def __init__(self, *, clean: bool = True) -> None:
        self._clean = clean
        self.commits: list[str] = []
        self.added: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def is_clean(self, repo: Path) -> bool:
        return self._clean

    def diff(self, repo: Path) -> str:
        return ""

    def add(self, repo: Path, paths: list[str]) -> None:
        self.added.append(list(paths))

    def commit(self, repo: Path, message: str) -> str:
        self.commits.append(message)
        return "sha" + str(len(self.commits))

    def head_sha(self, repo: Path) -> str:
        return "head-sha"


class TestOrchestratorRecovery:
    def test_planner_done_stops_immediately(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1", status="done")],
            reviewer_decisions=[],
        )
        state = orch.run()
        assert ex.executed == []
        assert state.current_status == "stopped"

    def test_max_iterations_stops(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task(f"T-{i}") for i in range(10)],
            reviewer_decisions=[
                {
                    "decision": "continue",
                    "reason": "ok",
                    "tests_passed": True,
                    "acceptance_met": True,
                    "next_focus": "",
                    "human_action": "",
                }
            ]
            * 10,
            config_overrides={"max_iterations": 2},
            dry_run=False,
        )
        state = orch.run()
        assert state.current_iteration <= 2

    def test_consecutive_failures_circuit_breaker(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task(f"T-{i}") for i in range(10)],
            reviewer_decisions=[
                {
                    "decision": "rollback",
                    "reason": "失败",
                    "tests_passed": False,
                    "acceptance_met": False,
                    "next_focus": "",
                    "human_action": "",
                }
            ]
            * 10,
            config_overrides={"max_consecutive_failures": 2},
            dry_run=False,
        )
        state = orch.run()
        assert state.consecutive_failures >= 2
        assert state.current_status in ("stopped", "failed")
        assert backend.commits == []

    def test_review_rollback_no_commit(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1"), _planner_task("T-2", status="done")],
            reviewer_decisions=[
                {
                    "decision": "rollback",
                    "reason": "回滚",
                    "tests_passed": False,
                    "acceptance_met": False,
                    "next_focus": "",
                    "human_action": "",
                }
            ],
            dry_run=False,
        )
        orch.run()
        assert backend.commits == []

    def test_review_fail_no_commit(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1"), _planner_task("T-2", status="done")],
            reviewer_decisions=[
                {
                    "decision": "continue",
                    "reason": "测试未过",
                    "tests_passed": False,
                    "acceptance_met": False,
                    "next_focus": "修复",
                    "human_action": "",
                }
            ],
            dry_run=False,
        )
        orch.run()
        assert backend.commits == []

    def test_commit_on_review_pass(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1"), _planner_task("T-2", status="done")],
            reviewer_decisions=[
                {
                    "decision": "continue",
                    "reason": "通过",
                    "tests_passed": True,
                    "acceptance_met": True,
                    "next_focus": "",
                    "human_action": "",
                }
            ],
            dry_run=False,
        )
        state = orch.run()
        assert len(backend.commits) == 1
        assert "T-1" in state.completed_tasks

    def test_policy_violation_stops_no_commit(self, tmp_path: Path) -> None:
        # 越界文件路径
        bad_task = _planner_task("T-1", files=[".env"])
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[bad_task, _planner_task("T-2", status="done")],
            reviewer_decisions=[],
            dry_run=False,
        )
        state = orch.run()
        assert ex.executed == []
        assert backend.commits == []
        assert state.current_status in ("stopped", "failed")

    def test_resume_skips_completed_task(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1"), _planner_task("T-2", status="done")],
            reviewer_decisions=[
                {
                    "decision": "continue",
                    "reason": "ok",
                    "tests_passed": True,
                    "acceptance_met": True,
                    "next_focus": "",
                    "human_action": "",
                }
            ],
            pre_state_completed=["T-1"],
            dry_run=False,
        )
        state = orch.run()
        # T-1 已完成，不应再次执行；只执行 T-2
        assert "T-1" not in ex.executed
        assert state.current_status == "stopped"

    def test_git_not_clean_at_start_stops(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1")],
            reviewer_decisions=[],
            clean_git=False,
            dry_run=False,
        )
        state = orch.run()
        assert ex.executed == []
        assert state.current_status in ("stopped", "failed")
        assert "clean" in state.stop_reason.lower() or "git" in state.stop_reason.lower()

    def test_dry_run_never_commits(self, tmp_path: Path) -> None:
        orch, ex, backend = _build_orchestrator(
            tmp_path,
            planner_outputs=[_planner_task("T-1"), _planner_task("T-2", status="done")],
            reviewer_decisions=[
                {
                    "decision": "continue",
                    "reason": "ok",
                    "tests_passed": True,
                    "acceptance_met": True,
                    "next_focus": "",
                    "human_action": "",
                }
            ],
            dry_run=True,
        )
        orch.run()
        assert backend.commits == []
