"""SQLite 数据库初始化与迁移。

要求：
- 首次启动可自动初始化
- 使用参数化 SQL，禁止拼接用户输入
- 打开 foreign_keys
- 使用事务
- 数据库初始化可重复执行
- 重复初始化不得破坏已有数据
- 提供 schema_version
- migration 函数结构清晰，方便后续升级
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from boss_tool.logging_config import get_logger

logger = get_logger(__name__)

# 当前 schema 版本
CURRENT_SCHEMA_VERSION = 1


# ==================== DDL ====================
SCHEMA_V1_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id                  TEXT PRIMARY KEY,
    job_url                 TEXT NOT NULL,
    job_title               TEXT NOT NULL,
    company_name            TEXT NOT NULL,
    salary_raw              TEXT,
    salary_min              INTEGER,
    salary_max              INTEGER,
    salary_unit             TEXT,
    salary_months           INTEGER,
    experience              TEXT,
    degree                  TEXT,
    job_tags                TEXT,        -- JSON
    job_desc_full           TEXT,
    job_desc_summary        TEXT,
    address_raw             TEXT,
    address_std             TEXT,
    district                TEXT,
    longitude               REAL,
    latitude                REAL,
    distance_m              REAL,
    within_3km              INTEGER,     -- 0/1/null
    publish_time_raw        TEXT,
    job_active_state        TEXT NOT NULL DEFAULT 'unknown',
    likely_still_hiring     TEXT NOT NULL DEFAULT 'uncertain',
    is_exact_65_cap         INTEGER NOT NULL DEFAULT 0,    -- v0.3 新增
    age_target_category     TEXT,                          -- v0.3 新增
    age_match_category      TEXT,                          -- v0.3 新增
    accepts_candidate_age   INTEGER,                       -- v0.3 新增
    age_match_reason        TEXT,
    age_rule_id             TEXT,
    boundary_risk           TEXT,
    age_confidence          TEXT,
    age_needs_review        INTEGER NOT NULL DEFAULT 0,
    age_evidence_raw        TEXT,
    age_min                 INTEGER,
    age_max                 INTEGER,
    physical_intensity_category TEXT,
    physical_intensity_score   INTEGER,
    physical_intensity_evidence TEXT,
    sitting_allowed          INTEGER,
    prolonged_standing       INTEGER,
    patrol_required          INTEGER,
    walking_intensity        TEXT,
    stair_climbing_required  INTEGER,
    lifting_required         INTEGER,
    lifting_weight_text      TEXT,
    garbage_transport_required INTEGER,
    outdoor_work             INTEGER,
    high_temperature_exposure INTEGER,
    work_area_text           TEXT,
    shift_type               TEXT,
    night_shift_required     INTEGER,
    working_hours_text       TEXT,
    rest_schedule_text       TEXT,
    physical_needs_review    INTEGER NOT NULL DEFAULT 0,
    recruiter_name           TEXT,
    recruiter_title          TEXT,
    activity_raw             TEXT,
    activity_category        TEXT,
    active_within_3d         INTEGER,
    -- 缓存与去重 [v0.3 补充]
    visited_jobs            INTEGER NOT NULL DEFAULT 0,
    last_detail_visit_at    TEXT,
    detail_content_hash     TEXT,
    skip_reason              TEXT,
    revisit_allowed_at       TEXT,
    list_stage_passed        INTEGER NOT NULL DEFAULT 0,
    detail_visit_count      INTEGER NOT NULL DEFAULT 0,
    -- 采集元
    source_page             TEXT,
    parse_ok                INTEGER NOT NULL DEFAULT 1,
    missing_fields          TEXT,
    error_reason            TEXT,
    manual_reviewed         INTEGER NOT NULL DEFAULT 0,
    manual_review_note      TEXT,
    -- 评分与优先级
    score                   REAL,
    score_breakdown         TEXT,
    priority_rank           INTEGER,
    recommended_bucket      TEXT,
    first_seen_at           TEXT NOT NULL,
    last_collected_at       TEXT NOT NULL,
    job_status              TEXT NOT NULL DEFAULT 'unknown'
);
"""

SCHEMA_V1_INDICES = """
CREATE INDEX IF NOT EXISTS idx_jobs_within3km  ON jobs(within_3km);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(job_status);
CREATE INDEX IF NOT EXISTS idx_jobs_age_target ON jobs(age_target_category);
CREATE INDEX IF NOT EXISTS idx_jobs_exact65    ON jobs(is_exact_65_cap);
CREATE INDEX IF NOT EXISTS idx_jobs_hiring     ON jobs(likely_still_hiring);
CREATE INDEX IF NOT EXISTS idx_jobs_intensity  ON jobs(physical_intensity_category);
CREATE INDEX IF NOT EXISTS idx_jobs_act_cat    ON jobs(activity_category);
CREATE INDEX IF NOT EXISTS idx_jobs_bucket     ON jobs(recommended_bucket);
CREATE INDEX IF NOT EXISTS idx_jobs_visited    ON jobs(visited_jobs);
CREATE INDEX IF NOT EXISTS idx_jobs_skip       ON jobs(skip_reason);
CREATE INDEX IF NOT EXISTS idx_jobs_revisit    ON jobs(revisit_allowed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_last_col   ON jobs(last_collected_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_url  ON jobs(job_url);
"""

SCHEMA_V1_JOB_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS job_snapshots (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL,
    snapshot_at    TEXT NOT NULL,
    snapshot_json  TEXT NOT NULL,
    change_type    TEXT NOT NULL DEFAULT 'unchanged',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_job  ON job_snapshots(job_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_at   ON job_snapshots(snapshot_at);
"""

SCHEMA_V1_COLLECTION_META = """
CREATE TABLE IF NOT EXISTS collection_meta (
    meta_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL,
    source_page         TEXT NOT NULL,
    collected_at        TEXT NOT NULL,
    parse_ok            INTEGER NOT NULL DEFAULT 1,
    missing_fields      TEXT,
    error_reason        TEXT,
    manual_reviewed     INTEGER NOT NULL DEFAULT 0,
    manual_review_note TEXT,
    visited_jobs        INTEGER NOT NULL DEFAULT 0,
    last_detail_visit_at TEXT,
    detail_content_hash TEXT,
    skip_reason         TEXT,
    revisit_allowed_at  TEXT,
    list_stage_passed   INTEGER NOT NULL DEFAULT 0,
    detail_visit_count  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meta_job ON collection_meta(job_id);
"""

SCHEMA_V1_RUN_LOGS = """
CREATE TABLE IF NOT EXISTS run_logs (
    run_id                       TEXT PRIMARY KEY,
    started_at                   TEXT NOT NULL,
    ended_at                     TEXT,
    status                       TEXT NOT NULL,
    stop_reason                  TEXT,
    -- 招聘状态分布 [v0.3 新增]
    hiring_confirmed             INTEGER DEFAULT 0,
    hiring_likely                INTEGER DEFAULT 0,
    hiring_uncertain             INTEGER DEFAULT 0,
    hiring_closed                 INTEGER DEFAULT 0,
    -- 账号健康记录 [v0.3 补充]
    account_warning_detected     INTEGER NOT NULL DEFAULT 0,
    warning_type                 TEXT,
    warning_text                 TEXT,
    page_count                   INTEGER DEFAULT 0,
    detail_page_count            INTEGER DEFAULT 0,
    search_page_count            INTEGER DEFAULT 0,
    cache_hit_count              INTEGER DEFAULT 0,
    duplicate_skip_count         INTEGER DEFAULT 0,
    list_filter_skip_count       INTEGER DEFAULT 0,
    run_duration_seconds         INTEGER DEFAULT 0,
    consecutive_errors           INTEGER DEFAULT 0,
    stopped_by_safety_rule       INTEGER NOT NULL DEFAULT 0,
    user_aborted                 INTEGER NOT NULL DEFAULT 0,
    last_successful_url          TEXT,
    run_completed                INTEGER NOT NULL DEFAULT 0,
    output_path                  TEXT,
    note                         TEXT
);
"""

SCHEMA_V1_GEOCODE_CACHE = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    query_text      TEXT PRIMARY KEY,
    standardized    TEXT,
    longitude       REAL,
    latitude        REAL,
    district        TEXT,
    fetched_at      TEXT NOT NULL
);
"""

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


# ==================== Database ====================
class Database:
    """SQLite 数据库封装。

    使用标准库 sqlite3，不引入 ORM。
    所有 SQL 均使用参数化。
    """

    def __init__(self, db_path: str | Path, *, foreign_keys: bool = True):
        self.db_path = str(db_path)
        self._foreign_keys = foreign_keys
        self._conn: sqlite3.Connection | None = None

    # ---------- 生命周期 ----------
    def connect(self) -> sqlite3.Connection:
        """打开连接。多次调用幂等。"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            if self._foreign_keys:
                self._conn.execute("PRAGMA foreign_keys = ON;")
            logger.debug("数据库连接已打开: %s", self.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("数据库连接已关闭: %s", self.db_path)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            return self.connect()
        return self._conn

    # ---------- 初始化与迁移 ----------
    def initialize(self) -> None:
        """初始化数据库（幂等）。

        执行所有 schema_version 的迁移；已有数据不会破坏。
        """
        conn = self.connect()
        self._execute_schema(conn, SCHEMA_VERSION_TABLE)
        self._execute_schema(conn, SCHEMA_V1_JOBS)
        self._execute_schema(conn, SCHEMA_V1_INDICES)
        self._execute_schema(conn, SCHEMA_V1_JOB_SNAPSHOTS)
        self._execute_schema(conn, SCHEMA_V1_COLLECTION_META)
        self._execute_schema(conn, SCHEMA_V1_RUN_LOGS)
        self._execute_schema(conn, SCHEMA_V1_GEOCODE_CACHE)
        self._apply_migrations(conn)
        conn.commit()
        logger.info("数据库初始化完成 schema_version=%s", self.get_schema_version())

    def _execute_schema(self, conn: sqlite3.Connection, schema_sql: str) -> None:
        conn.executescript(schema_sql)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """按顺序应用所有未执行的迁移。"""
        for version, migration_fn in _MIGRATIONS.items():
            if self._is_migration_applied(conn, version):
                continue
            migration_fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now_iso()),
            )
            logger.info("已应用迁移 v%s", version)

    def _is_migration_applied(self, conn: sqlite3.Connection, version: int) -> bool:
        row = conn.execute(
            "SELECT version FROM schema_version WHERE version = ?",
            (version,),
        ).fetchone()
        return row is not None

    def get_schema_version(self) -> int:
        conn = self.connection
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        if row is None or row["v"] is None:
            return 0
        return int(row["v"])

    # ---------- 事务 ----------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文管理器。

        异常时自动回滚；正常退出时提交。
        """
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def commit(self) -> None:
        """显式提交当前事务。"""
        if self._conn is not None:
            self._conn.commit()


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


# ==================== 迁移函数 ====================
def _migration_v1_initial(conn: sqlite3.Connection) -> None:
    """v1 初始迁移：表结构已在 initialize 中通过 IF NOT EXISTS 创建。

    此函数保留为占位，未来 v2+ 可在此处添加列添加语句。
    """
    # 占位迁移：确保连接可用
    conn.execute("SELECT 1")


_MIGRATIONS: dict[int, callable] = {  # type: ignore[type-arg]
    1: _migration_v1_initial,
}


__all__ = ["Database", "CURRENT_SCHEMA_VERSION"]
