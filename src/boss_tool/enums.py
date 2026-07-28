"""项目全部枚举定义。

按设计稿 v0.3 第六节定义，避免业务代码中散落裸字符串。
所有枚举继承 str 与 Enum，便于序列化为字符串存入 SQLite/JSON。
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    """字符串枚举基类。

    继承 str 便于直接序列化与 SQLite 存储；同时保持枚举语义。
    """

    def __str__(self) -> str:
        # 直接返回枚举值，避免日志中出现 AgeTargetCategory.exact_65_cap 这种形式
        return self.value


# ==================== 年龄目标相关 ====================
class AgeTargetCategory(_StrEnum):
    """年龄目标 6 级分类（v0.3）。"""

    EXACT_65_CAP = "exact_65_cap"
    RANGE_INCLUDES_60_TO_65 = "range_includes_60_to_65"
    ALTERNATIVE_ACCEPTS_60 = "alternative_accepts_60"
    BOUNDARY_60 = "boundary_60"
    NO_EXPLICIT_AGE = "no_explicit_age"
    REJECTS_60 = "rejects_60"


class AgeMatchCategory(_StrEnum):
    """兼容 v0.2 的高层年龄分类（由 age_target_category 映射得出）。"""

    ELIGIBLE = "eligible"
    MANUAL_REVIEW = "manual_review"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class BoundaryRisk(_StrEnum):
    """边界风险等级。"""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(_StrEnum):
    """判定置信度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ==================== 劳动强度相关 ====================
class PhysicalIntensityCategory(_StrEnum):
    """劳动强度 5 级分类。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNSUITABLE = "unsuitable"
    UNKNOWN = "unknown"


class WalkingIntensity(_StrEnum):
    """走动强度。"""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ShiftType(_StrEnum):
    """班次类型。"""

    DAY = "day"
    NIGHT = "night"
    ROTATING = "rotating"
    SPLIT = "split"
    UNKNOWN = "unknown"


# ==================== 招聘者活跃与招聘状态 ====================
class ActivityCategory(_StrEnum):
    """招聘者活跃分类（排序因素，非硬筛）。"""

    ACTIVE_3D = "active_3d"
    ACTIVE_THIS_WEEK = "active_this_week"
    ACTIVE_LONG_AGO = "active_long_ago"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class HiringLikelihood(_StrEnum):
    """仍在招聘可信度（v0.3 新增）。

    仅 closed 硬性排除。
    """

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    CLOSED = "closed"


class JobActiveState(_StrEnum):
    """页面原生岗位状态。"""

    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class JobStatus(_StrEnum):
    """岗位采集状态。"""

    ACTIVE = "active"
    UPDATED = "updated"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


# ==================== 缓存与去重 ====================
class SkipReason(_StrEnum):
    """跳过详情页的原因（v0.3 补充）。"""

    LIST_FILTERED = "list_filtered"
    ALREADY_VISITED = "already_visited"
    CONTENT_UNCHANGED = "content_unchanged"
    CLOSED = "closed"
    AGE_REJECT_AT_LIST = "age_reject_at_list"
    AREA_MISMATCH = "area_mismatch"
    KEYWORD_MISMATCH = "keyword_mismatch"
    OTHER = "other"


# ==================== 安全停止 ====================
class StopReason(_StrEnum):
    """安全停止原因。

    触发任一原因后立即停止采集，不自动恢复。
    """

    COMPLETED = "completed"
    BUDGET_REACHED = "budget_reached"
    USER_ABORTED = "user_aborted"
    BROWSER_CLOSED = "browser_closed"
    BROWSER_CONTEXT_CLOSED = "browser_context_closed"
    CAPTCHA = "captcha"
    SLIDER_VERIFICATION = "slider_verification"
    SMS_VERIFICATION = "sms_verification"
    LOGIN_EXPIRED = "login_expired"
    SECURITY_PAGE = "security_page"
    ACCOUNT_WARNING = "account_warning"
    RATE_LIMITED = "rate_limited"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    REDIRECT_LOOP = "redirect_loop"
    PAGE_STRUCTURE_MISSING = "page_structure_missing"
    CONSECUTIVE_PARSE_FAILURES = "consecutive_parse_failures"
    MAX_ERRORS_REACHED = "max_errors_reached"
    UNKNOWN_ERROR = "unknown_error"


# ==================== 薪资单位 ====================
class SalaryUnit(_StrEnum):
    """薪资单位。"""

    PER_MONTH = "元/月"
    PER_DAY = "元/天"
    PER_HOUR = "元/时"
    PER_YEAR = "元/年"


# ==================== 运行状态 ====================
class RunStatus(_StrEnum):
    """单次运行状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


# ==================== age_target_category → age_match_category 映射 ====================
AGE_TARGET_TO_MATCH: dict[AgeTargetCategory, AgeMatchCategory] = {
    AgeTargetCategory.EXACT_65_CAP: AgeMatchCategory.ELIGIBLE,
    AgeTargetCategory.RANGE_INCLUDES_60_TO_65: AgeMatchCategory.ELIGIBLE,
    AgeTargetCategory.ALTERNATIVE_ACCEPTS_60: AgeMatchCategory.ELIGIBLE,
    AgeTargetCategory.BOUNDARY_60: AgeMatchCategory.MANUAL_REVIEW,
    AgeTargetCategory.NO_EXPLICIT_AGE: AgeMatchCategory.MANUAL_REVIEW,
    AgeTargetCategory.REJECTS_60: AgeMatchCategory.INELIGIBLE,
}


__all__ = [
    "AgeTargetCategory",
    "AgeMatchCategory",
    "BoundaryRisk",
    "Confidence",
    "PhysicalIntensityCategory",
    "WalkingIntensity",
    "ShiftType",
    "ActivityCategory",
    "HiringLikelihood",
    "JobActiveState",
    "JobStatus",
    "SkipReason",
    "StopReason",
    "SalaryUnit",
    "RunStatus",
    "AGE_TARGET_TO_MATCH",
]
