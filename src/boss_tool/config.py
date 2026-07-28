"""配置加载与校验。

使用 YAML + Pydantic v2 实现类型安全的配置。
要求：
- 缺失必填配置时给出清晰错误
- 配置类型错误时拒绝启动
- 不允许静默忽略未知关键字段
- 敏感信息不得写进默认配置
- 账号风险最小化相关字段必须为指定值（强制可见浏览器、单账号、单上下文等）
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ==================== 常量 ====================
# 允许的 BOSS 直聘域名白名单。
# 浏览器只允许打开这些域名下的页面，禁止打开其他任意外部域名。
ALLOWED_HOME_HOSTS: frozenset[str] = frozenset(
    {
        "www.zhipin.com",
        "zhipin.com",
    }
)


# ==================== 子配置 ====================
class BudgetConfig(BaseModel):
    """运行预算配置。

    仅用于控制负载与防止程序失控，不代表"安全阈值"。
    不得在代码或文档中宣称"不会封号"。
    """

    model_config = ConfigDict(extra="forbid")

    max_search_pages_per_keyword: int = Field(..., gt=0, description="每个关键词最多搜索页数")
    max_job_details_per_run: int = Field(..., gt=0, description="本次运行最多访问详情页数")
    max_total_pages_per_run: int = Field(..., gt=0, description="本次运行最多访问页面总数")
    max_runtime_minutes: int = Field(..., gt=0, description="本次运行最长时长（分钟）")
    max_errors_per_run: int = Field(..., gt=0, description="本次运行最多错误数")
    max_consecutive_parse_failures: int = Field(..., gt=0, description="连续解析失败上限")


class PageIntervalConfig(BaseModel):
    """页面间停顿配置。"""

    model_config = ConfigDict(extra="forbid")

    min_seconds: int = Field(..., gt=0, description="页面间最短停顿秒数")
    max_seconds: int = Field(..., gt=0, description="页面间最长停顿秒数")

    @model_validator(mode="after")
    def _validate_range(self) -> PageIntervalConfig:
        if self.min_seconds > self.max_seconds:
            raise ValueError(
                f"min_seconds ({self.min_seconds}) 不能大于 max_seconds ({self.max_seconds})"
            )
        return self


class RevisitConfig(BaseModel):
    """重访问冷却配置。"""

    model_config = ConfigDict(extra="forbid")

    cooldown_hours: int = Field(..., gt=0, description="同岗位详情页重访问冷却小时数")


class BrowserConfig(BaseModel):
    """浏览器运行方式配置。

    红线（强制值）：
    - headless 必须为 False（强制可见浏览器）
    - single_context 必须为 True
    - single_account 必须为 True
    - home_url 必须为 BOSS 直聘白名单域名（https://www.zhipin.com/ 或 https://zhipin.com/）
    """

    model_config = ConfigDict(extra="forbid")

    user_data_dir: str = Field(..., min_length=1, description="持久化用户目录")
    home_url: str = Field(..., min_length=1, description="浏览器首页 URL（仅 BOSS 直聘白名单域名）")
    headless: bool = Field(..., description="必须为 false")
    single_context: bool = Field(..., description="必须为 true")
    single_account: bool = Field(..., description="必须为 true")

    @field_validator("home_url")
    @classmethod
    def _validate_home_url(cls, v: str) -> str:
        """校验首页 URL 格式与域名白名单。

        要求：
        - 必须为 http/https 协议
        - host 必须在 ALLOWED_HOME_HOSTS 白名单内
        - 不允许 localhost 或 IP 形式
        """
        if not v or not v.strip():
            raise ValueError("home_url 不能为空")
        v = v.strip()
        try:
            parsed = urlparse(v)
        except Exception as e:
            raise ValueError(f"home_url 解析失败: {v}") from e
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"home_url 必须为 http/https 协议，当前为 {parsed.scheme!r} (url={v})")
        if not parsed.netloc:
            raise ValueError(f"home_url 缺少 host: {v}")
        # 取 host（去掉端口）
        host = parsed.hostname or ""
        host = host.lower()
        if host not in ALLOWED_HOME_HOSTS:
            raise ValueError(
                f"home_url host {host!r} 不在白名单 {sorted(ALLOWED_HOME_HOSTS)} 中，"
                "仅允许 BOSS 直聘域名（www.zhipin.com / zhipin.com）"
            )
        return v

    @field_validator("headless")
    @classmethod
    def _validate_headless(cls, v: bool) -> bool:
        if v:
            raise ValueError("headless 必须为 false（强制可见浏览器，不得后台静默运行）")
        return v

    @field_validator("single_context")
    @classmethod
    def _validate_single_context(cls, v: bool) -> bool:
        if not v:
            raise ValueError("single_context 必须为 true（单浏览器上下文）")
        return v

    @field_validator("single_account")
    @classmethod
    def _validate_single_account(cls, v: bool) -> bool:
        if not v:
            raise ValueError("single_account 必须为 true（单账号）")
        return v


class RunControlConfig(BaseModel):
    """运行控制配置。

    红线（强制值）：
    - require_user_confirm 必须为 True
    - allow_unattended 必须为 False
    - allow_background 必须为 False
    """

    model_config = ConfigDict(extra="forbid")

    require_user_confirm: bool = Field(..., description="必须为 true")
    allow_unattended: bool = Field(..., description="必须为 false")
    allow_background: bool = Field(..., description="必须为 false")

    @field_validator("require_user_confirm")
    @classmethod
    def _validate_require_user_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("require_user_confirm 必须为 true（人工确认后启动）")
        return v

    @field_validator("allow_unattended")
    @classmethod
    def _validate_allow_unattended(cls, v: bool) -> bool:
        if v:
            raise ValueError("allow_unattended 必须为 false（不得无人值守运行）")
        return v

    @field_validator("allow_background")
    @classmethod
    def _validate_allow_background(cls, v: bool) -> bool:
        if v:
            raise ValueError("allow_background 必须为 false（不得后台静默运行）")
        return v


class RuntimeConfig(BaseModel):
    """运行时配置：预算、停顿、冷却、浏览器、运行控制。"""

    model_config = ConfigDict(extra="forbid")

    budget: BudgetConfig
    page_interval: PageIntervalConfig
    revisit: RevisitConfig
    browser: BrowserConfig
    run_control: RunControlConfig


class DatabaseConfig(BaseModel):
    """数据库配置。"""

    model_config = ConfigDict(extra="forbid")

    sqlite_path: str = Field(..., min_length=1, description="SQLite 文件路径（相对 data_dir）")
    foreign_keys: bool = Field(default=True, description="启动时打开 PRAGMA foreign_keys")
    strict_init: bool = Field(default=True, description="初始化失败是否抛出异常")


class LoggingConfig(BaseModel):
    """日志配置。"""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(..., description="日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）")
    console_enabled: bool = Field(default=True)
    file_enabled: bool = Field(default=True)
    file_name: str = Field(..., min_length=1, description="文件日志文件名")
    max_bytes: int = Field(..., gt=0, description="单文件最大字节数")
    backup_count: int = Field(..., ge=0, description="保留备份数")
    format: str = Field(..., min_length=1, description="日志格式")
    date_format: str = Field(..., min_length=1, description="日期格式")

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        up = v.upper()
        if up not in allowed:
            raise ValueError(f"level 必须为 {allowed} 之一，当前为 {v}")
        return up


class LocationConfig(BaseModel):
    """中心点与地理范围配置。"""

    model_config = ConfigDict(extra="forbid")

    city: str = Field(..., min_length=1)
    district: str = Field(..., min_length=1)
    center_name: str = Field(..., min_length=1)
    center_address: str = Field(..., min_length=1)
    center_longitude: float = Field(..., ge=-180.0, le=180.0)
    center_latitude: float = Field(..., ge=-90.0, le=90.0)
    radius_m: int = Field(..., gt=0, description="半径（米）")
    geo_provider: str = Field(..., min_length=1, description="地图服务（仅声明，未实现）")


class KeywordConfig(BaseModel):
    """关键词配置。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(..., min_length=1, description="关键词列表，至少 1 个")
    candidate_age: int = Field(..., gt=0, le=150, description="求职者当前年龄")

    @field_validator("keywords")
    @classmethod
    def _validate_keywords(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k and k.strip()]
        if not cleaned:
            raise ValueError("keywords 不能为空")
        return cleaned


class ScoringConfig(BaseModel):
    """评分权重与排序优先级配置。"""

    model_config = ConfigDict(extra="forbid")

    weights: dict[str, int] = Field(..., description="评分权重")
    age_target_scores: dict[str, int] = Field(..., description="年龄目标评分")
    intensity_bands: list[dict] = Field(..., description="劳动强度评分区间")
    distance_bands: list[dict] = Field(..., description="距离评分区间")
    activity_state_scores: dict[str, int] = Field(...)
    shift_hours_scores: dict[str, int] = Field(...)
    salary_scores: dict[str, int] = Field(...)
    top_recommend_sort_keys: list[str] = Field(...)
    hard_exclusions: list[str] = Field(...)
    highlight_top_n: int = Field(..., gt=0)

    @field_validator("weights")
    @classmethod
    def _validate_weights_sum(cls, v: dict[str, int]) -> dict[str, int]:
        total = sum(v.values())
        if total != 100:
            raise ValueError(f"评分权重总和必须为 100，当前为 {total}")
        return v


# ==================== 顶层配置 ====================
class AppConfig(BaseModel):
    """应用级总配置（由 app.yaml 加载）。

    注意：app.yaml 中 `app` 是字典（含 name/version/data_dir 等），
    此处把内部字段拍平为顶层属性，便于使用。
    """

    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(..., min_length=1, description="应用名称")
    app_version: str = Field(default="0.1.0", description="应用版本")
    data_dir: str = Field(..., min_length=1)
    logs_dir: str = Field(..., min_length=1)
    output_dir: str = Field(..., min_length=1)
    user_data_dir: str = Field(..., min_length=1)
    candidate_age: int = Field(..., gt=0, le=150, description="求职者当前年龄")

    database: DatabaseConfig
    logging: LoggingConfig

    @field_validator("candidate_age")
    @classmethod
    def _validate_candidate_age(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("candidate_age 必须大于 0")
        return v


# ==================== 加载器 ====================
class ConfigLoader:
    """从 YAML 文件加载配置并校验。

    所有未知字段一律拒绝，不静默忽略。
    """

    REQUIRED_FILES = (
        "app.yaml",
        "keywords.yaml",
        "locations.yaml",
        "runtime.yaml",
        "age_rules.yaml",
        "intensity_rules.yaml",
        "scoring.yaml",
    )

    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir)

    def load_all(self) -> dict:
        """加载所有配置文件，返回类型安全的配置字典。

        Returns:
            dict: 包含 app_config、keyword_config、location_config、
                  runtime_config、age_rules_raw、intensity_rules_raw、scoring_config 的字典。

        Raises:
            FileNotFoundError: 配置文件不存在
            pydantic.ValidationError: 配置校验失败
        """
        self._validate_required_files()

        app_data = self._load_yaml("app.yaml")
        keywords_data = self._load_yaml("keywords.yaml")
        locations_data = self._load_yaml("locations.yaml")
        runtime_data = self._load_yaml("runtime.yaml")
        age_rules_data = self._load_yaml("age_rules.yaml")
        intensity_rules_data = self._load_yaml("intensity_rules.yaml")
        scoring_data = self._load_yaml("scoring.yaml")

        # 构造 AppConfig（含 candidate、app）
        app_section = app_data["app"]
        app_cfg = AppConfig(
            app_name=app_section["name"],
            app_version=app_section.get("version", "0.1.0"),
            data_dir=app_section["data_dir"],
            logs_dir=app_section["logs_dir"],
            output_dir=app_section["output_dir"],
            user_data_dir=app_section["user_data_dir"],
            candidate_age=app_data["candidate"]["age"],
            database=app_data["database"],
            logging=app_data["logging"],
        )

        keyword_cfg = KeywordConfig(
            keywords=keywords_data["keywords"],
            candidate_age=keywords_data["candidate"]["age"],
        )

        location_cfg = LocationConfig(**locations_data["location"])

        runtime_cfg = RuntimeConfig(**runtime_data)

        scoring_cfg = ScoringConfig(
            weights=scoring_data["weights"],
            age_target_scores=scoring_data["age_target_scores"],
            intensity_bands=scoring_data["intensity_bands"],
            distance_bands=scoring_data["distance_bands"],
            activity_state_scores=scoring_data["activity_state_scores"],
            shift_hours_scores=scoring_data["shift_hours_scores"],
            salary_scores=scoring_data["salary_scores"],
            top_recommend_sort_keys=scoring_data["top_recommend_sort_keys"],
            hard_exclusions=scoring_data["hard_exclusions"],
            highlight_top_n=scoring_data["highlight_top_n"],
        )

        # age_rules / intensity_rules 仅声明结构，P0 阶段不强制类型校验
        # 仅校验顶层关键字存在
        if "rules" not in age_rules_data:
            raise ValueError("age_rules.yaml 缺少 rules 顶层字段")
        if "prefer" not in intensity_rules_data or "exclude" not in intensity_rules_data:
            raise ValueError("intensity_rules.yaml 缺少 prefer/exclude 字段")

        return {
            "app": app_cfg,
            "keywords": keyword_cfg,
            "location": location_cfg,
            "runtime": runtime_cfg,
            "scoring": scoring_cfg,
            "age_rules_raw": age_rules_data,
            "intensity_rules_raw": intensity_rules_data,
        }

    def _validate_required_files(self) -> None:
        missing = [f for f in self.REQUIRED_FILES if not (self.config_dir / f).exists()]
        if missing:
            raise FileNotFoundError(
                f"配置目录缺少必需文件: {missing} (config_dir={self.config_dir})"
            )

    def _load_yaml(self, filename: str) -> dict:
        path = self.config_dir / filename
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            raise ValueError(f"配置文件为空: {path}")
        if not isinstance(data, dict):
            raise ValueError(f"配置文件根必须是字典: {path}")
        return data


def load_config(config_dir: str | Path) -> dict:
    """便捷加载入口。"""
    return ConfigLoader(config_dir).load_all()


__all__ = [
    "AppConfig",
    "BudgetConfig",
    "BrowserConfig",
    "RunControlConfig",
    "PageIntervalConfig",
    "RevisitConfig",
    "RuntimeConfig",
    "DatabaseConfig",
    "LoggingConfig",
    "LocationConfig",
    "KeywordConfig",
    "ScoringConfig",
    "ConfigLoader",
    "load_config",
    "ALLOWED_HOME_HOSTS",
]
