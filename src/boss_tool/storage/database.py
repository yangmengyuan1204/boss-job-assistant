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

迁移流程：
1. 打开连接
2. 只创建 schema_version 表
3. 查询已应用版本
4. 按版本顺序执行尚未应用的迁移
5. 每个迁移与其 schema_version 写入必须位于同一事务中
6. 迁移失败时回滚，不能留下"版本已记录但表没建完"的状态
7. 重复执行 initialize 必须幂等
8. 已有数据不得损坏
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from boss_tool.logging_config import get_logger

logger = get_logger(__name__)

# 明确的迁移函数类型
Migration = Callable[[sqlite3.Connection], None]

# 当前 schema 版本（必须与 MIGRATIONS 中最高版本一致）
CURRENT_SCHEMA_VERSION = 4


# ==================== V1 Schema DDL ====================
# 所有 V1 表结构与索引集中定义，由 migration_v1_initial 统一创建。
# 项目尚未正式发布数据库，因此 CHECK 约束直接加在 V1 结构中。

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
    job_status              TEXT NOT NULL DEFAULT 'unknown',
    -- CHECK 约束（P0.1 新增）
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max),
    CHECK (age_min IS NULL OR age_max IS NULL OR age_min <= age_max),
    CHECK (physical_intensity_score IS NULL OR (physical_intensity_score BETWEEN 0 AND 100)),
    CHECK (distance_m IS NULL OR distance_m >= 0),
    CHECK (detail_visit_count >= 0),
    CHECK (visited_jobs IN (0, 1)),
    CHECK (is_exact_65_cap IN (0, 1)),
    CHECK (age_needs_review IN (0, 1)),
    CHECK (physical_needs_review IN (0, 1)),
    CHECK (list_stage_passed IN (0, 1)),
    CHECK (parse_ok IN (0, 1)),
    CHECK (manual_reviewed IN (0, 1)),
    CHECK (within_3km IS NULL OR within_3km IN (0, 1)),
    CHECK (accepts_candidate_age IS NULL OR accepts_candidate_age IN (0, 1)),
    CHECK (sitting_allowed IS NULL OR sitting_allowed IN (0, 1)),
    CHECK (prolonged_standing IS NULL OR prolonged_standing IN (0, 1)),
    CHECK (patrol_required IS NULL OR patrol_required IN (0, 1)),
    CHECK (stair_climbing_required IS NULL OR stair_climbing_required IN (0, 1)),
    CHECK (lifting_required IS NULL OR lifting_required IN (0, 1)),
    CHECK (garbage_transport_required IS NULL OR garbage_transport_required IN (0, 1)),
    CHECK (outdoor_work IS NULL OR outdoor_work IN (0, 1)),
    CHECK (high_temperature_exposure IS NULL OR high_temperature_exposure IN (0, 1)),
    CHECK (night_shift_required IS NULL OR night_shift_required IN (0, 1)),
    CHECK (active_within_3d IS NULL OR active_within_3d IN (0, 1))
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
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
    CHECK (parse_ok IN (0, 1)),
    CHECK (manual_reviewed IN (0, 1)),
    CHECK (visited_jobs IN (0, 1)),
    CHECK (list_stage_passed IN (0, 1)),
    CHECK (detail_visit_count >= 0)
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
    hiring_closed                INTEGER DEFAULT 0,
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
    note                         TEXT,
    -- CHECK 约束（P0.1 新增）
    CHECK (page_count >= 0),
    CHECK (detail_page_count >= 0),
    CHECK (search_page_count >= 0),
    CHECK (cache_hit_count >= 0),
    CHECK (duplicate_skip_count >= 0),
    CHECK (list_filter_skip_count >= 0),
    CHECK (run_duration_seconds >= 0),
    CHECK (consecutive_errors >= 0),
    CHECK (account_warning_detected IN (0, 1)),
    CHECK (stopped_by_safety_rule IN (0, 1)),
    CHECK (user_aborted IN (0, 1)),
    CHECK (run_completed IN (0, 1))
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


# ==================== 迁移函数 ====================
def migration_v1_initial(conn: sqlite3.Connection) -> None:
    """V1 初始迁移：创建所有表与索引。

    负责创建：
    - jobs（含所有 CHECK 约束）
    - 所有 jobs 索引（含 uq_jobs_url 唯一索引）
    - job_snapshots
    - collection_meta
    - run_logs（含所有 CHECK 约束）
    - geocode_cache

    所有 DDL 使用 IF NOT EXISTS，保证重复执行幂等。
    """
    conn.executescript(SCHEMA_V1_JOBS)
    conn.executescript(SCHEMA_V1_INDICES)
    conn.executescript(SCHEMA_V1_JOB_SNAPSHOTS)
    conn.executescript(SCHEMA_V1_COLLECTION_META)
    conn.executescript(SCHEMA_V1_RUN_LOGS)
    conn.executescript(SCHEMA_V1_GEOCODE_CACHE)


# ==================== V2 Schema DDL ====================
SCHEMA_V2_JOB_LIST = """
CREATE TABLE IF NOT EXISTS job_list (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT UNIQUE NOT NULL,
    title        TEXT,
    salary       TEXT,
    company      TEXT,
    location     TEXT,
    experience   TEXT,
    education    TEXT,
    job_url      TEXT,
    company_url  TEXT,
    page_no      INTEGER,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_list_job_id  ON job_list(job_id);
CREATE INDEX IF NOT EXISTS idx_job_list_collected ON job_list(collected_at);
"""


def migration_v2_job_list(conn: sqlite3.Connection) -> None:
    """V2 迁移：新增 job_list 表。

    用于 P3 搜索结果列表页采集，存储列表页公开可见字段。
    与 jobs 表独立，不涉及详情页/年龄判断/劳动强度/评分等后续阶段字段。
    job_id 为去重主键，由 derive_job_id() 推导（URL 路径末段或哈希）。
    """
    conn.executescript(SCHEMA_V2_JOB_LIST)


# ==================== V3 Schema DDL ====================
SCHEMA_V3_JOB_DETAIL = """
CREATE TABLE IF NOT EXISTS job_detail (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id            TEXT UNIQUE NOT NULL,
    job_url           TEXT,
    title             TEXT,
    salary            TEXT,
    location          TEXT,
    experience        TEXT,
    education         TEXT,
    employment_type   TEXT,
    description       TEXT,
    company           TEXT,
    company_url       TEXT,
    company_industry  TEXT,
    company_size      TEXT,
    company_stage     TEXT,
    recruiter_name    TEXT,
    recruiter_title   TEXT,
    recruiter_active  TEXT,
    benefits_json     TEXT,
    tags_json         TEXT,
    collected_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_detail_job_id    ON job_detail(job_id);
CREATE INDEX IF NOT EXISTS idx_job_detail_collected ON job_detail(collected_at);
"""


def migration_v3_job_detail(conn: sqlite3.Connection) -> None:
    """V3 迁移：新增 job_detail 表。

    用于 P4 详情页人工触发采集，存储详情页公开可见字段。
    与 job_list 表独立，不破坏已有 job_list 表。
    job_id 为去重主键，与 job_list 同源（复用 P3 derive_job_id 规则）。
    不保存原始 HTML / Cookie / Token / 手机号 / 邮箱 / 聊天记录。
    """
    conn.executescript(SCHEMA_V3_JOB_DETAIL)


# ==================== V4 Schema DDL ====================
SCHEMA_V4_GEO_CACHE = """
CREATE TABLE IF NOT EXISTS geo_cache (
    address             TEXT PRIMARY KEY,
    normalized_address  TEXT,
    longitude           REAL,
    latitude            REAL,
    provider            TEXT,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (status IN ('success', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_geo_cache_normalized ON geo_cache(normalized_address);
CREATE INDEX IF NOT EXISTS idx_geo_cache_status     ON geo_cache(status);
"""


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """获取表的所有列名（用于迁移时检查列是否已存在）。

    SQLite 不支持 ALTER TABLE ADD COLUMN IF NOT EXISTS，
    需要先检查列是否存在再决定是否 ADD COLUMN。
    """
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def migration_v4_geo(conn: sqlite3.Connection) -> None:
    """V4 迁移：新增 geo_cache 表，并给 job_list / job_detail 增加地理字段列。

    新增表：
    - geo_cache: 地理编码缓存（P5）

    新增列（job_list）：
    - normalized_address TEXT
    - longitude REAL
    - latitude REAL
    - distance_meter REAL
    - within_3km INTEGER

    新增列（job_detail）：
    - 同 job_list

    所有新增列均允许为空，不影响现有 UPSERT 三态判断。
    不破坏 V1-V3 已有表结构与数据。
    """
    # 1. 新增 geo_cache 表
    conn.executescript(SCHEMA_V4_GEO_CACHE)

    # 2. job_list 增加地理列
    job_list_cols = _table_columns(conn, "job_list")
    _add_column_if_missing(conn, "job_list", job_list_cols, "normalized_address", "TEXT")
    _add_column_if_missing(conn, "job_list", job_list_cols, "longitude", "REAL")
    _add_column_if_missing(conn, "job_list", job_list_cols, "latitude", "REAL")
    _add_column_if_missing(conn, "job_list", job_list_cols, "distance_meter", "REAL")
    _add_column_if_missing(conn, "job_list", job_list_cols, "within_3km", "INTEGER")

    # 3. job_detail 增加地理列
    job_detail_cols = _table_columns(conn, "job_detail")
    _add_column_if_missing(conn, "job_detail", job_detail_cols, "normalized_address", "TEXT")
    _add_column_if_missing(conn, "job_detail", job_detail_cols, "longitude", "REAL")
    _add_column_if_missing(conn, "job_detail", job_detail_cols, "latitude", "REAL")
    _add_column_if_missing(conn, "job_detail", job_detail_cols, "distance_meter", "REAL")
    _add_column_if_missing(conn, "job_detail", job_detail_cols, "within_3km", "INTEGER")


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    existing_cols: set[str],
    column: str,
    col_type: str,
) -> None:
    """如果列不存在则 ADD COLUMN（SQLite 不支持 IF NOT EXISTS）。"""
    if column not in existing_cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logger.debug("迁移 V4: 已添加列 %s.%s", table, column)


# 迁移注册表：版本号 -> 迁移函数。
# 新增迁移时在此处追加，版本号必须递增。
MIGRATIONS: dict[int, Migration] = {
    1: migration_v1_initial,
    2: migration_v2_job_list,
    3: migration_v3_job_detail,
    4: migration_v4_geo,
}


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

        流程：
        1. 打开连接
        2. 只创建 schema_version 表（元表，迁移系统自身依赖）
        3. 查询已应用版本
        4. 按版本顺序执行尚未应用的迁移
        5. 每个迁移与其 schema_version 写入位于同一事务中
        6. 迁移失败时回滚该事务

        重复执行不破坏已有数据。
        """
        conn = self.connect()
        # 1. 只创建 schema_version 表（迁移系统自身元表）
        conn.executescript(SCHEMA_VERSION_TABLE)
        conn.commit()
        # 2. 应用尚未执行的迁移
        self._apply_migrations(conn)
        logger.info("数据库初始化完成 schema_version=%s", self.get_schema_version())

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """按顺序应用所有未执行的迁移。

        每个迁移与其 schema_version 写入位于同一事务中：
        - 迁移成功 → 提交事务（schema_version 与表结构同时持久化）
        - 迁移失败 → 回滚事务（schema_version 不写入，表结构也不留存）
        """
        for version, migration_fn in MIGRATIONS.items():
            if self._is_migration_applied(conn, version):
                logger.debug("迁移 v%s 已应用，跳过", version)
                continue
            try:
                # 开启显式事务
                conn.execute("BEGIN")
                migration_fn(conn)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, _now_iso()),
                )
                conn.commit()
                logger.info("已应用迁移 v%s", version)
            except Exception:
                # 迁移失败：回滚事务，不写入 schema_version
                conn.rollback()
                logger.exception("迁移 v%s 失败，已回滚", version)
                raise

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
    return datetime.now().isoformat()


__all__ = [
    "Database",
    "CURRENT_SCHEMA_VERSION",
    "MIGRATIONS",
    "Migration",
    "migration_v1_initial",
    "migration_v2_job_list",
    "migration_v3_job_detail",
    "migration_v4_geo",
]
