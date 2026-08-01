"""P7 报告常量定义。

依据用户 P7 指令：
- 不在 constants.py 中硬编码锦园小区经纬度
- 仅复用 P5 已有的参考地点名称、距离阈值、候选年龄
- 报告只展示岗位已经计算好的 distance_meter / within_3km，不重新计算参考点坐标

安全声明：
- 距离阈值仅用于报告展示与分区判断
- 不宣称"安全阈值"或"不会封号"
- 候选年龄仅用于年龄适配判断，不作为绝对硬筛
"""

from __future__ import annotations

# ==================== 参考地点（仅文本，不含坐标）====================
# 复用 P5 location 配置中的参考地点名称
# 不在此处硬编码经纬度；距离判断依赖 job_detail.distance_meter（P5 计算结果）
REFERENCE_LOCATION_NAME: str = "杭州市拱墅区建国北路锦园小区"

# ==================== 距离阈值（米）====================
# 复用 P5 distance.py 的 DISTANCE_3KM_M
# 仅用于报告展示文本与分区条件，不重新计算距离
DISTANCE_THRESHOLD_M: float = 3000.0
DISTANCE_THRESHOLD_KM_TEXT: str = "3 公里"

# ==================== 候选年龄 ====================
# 复用 keywords.yaml 的 candidate_age
# 仅用于年龄适配判断
CANDIDATE_AGE: int = 60

# ==================== 规则版本 ====================
RULE_VERSION: str = "P6 v1"

# ==================== 描述摘要长度上限 ====================
DESCRIPTION_SUMMARY_MAX_LENGTH: int = 300

# ==================== 推荐等级中文映射 ====================
RECOMMEND_LEVEL_CN: dict[str, str] = {
    "A": "强烈推荐",
    "B": "推荐",
    "C": "可考虑",
    "D": "不推荐",
}

# ==================== 招聘者活跃等级中文映射 ====================
ACTIVITY_LEVEL_CN: dict[str, str] = {
    "active_3d": "3 日内活跃",
    "active_this_week": "本周活跃",
    "active_long_ago": "较久未活跃",
    "inactive": "不活跃",
    "unknown": "未知",
}

# ==================== 岗位分类中文映射 ====================
JOB_CATEGORY_CN: dict[str, str] = {
    "保洁": "保洁",
    "保安": "保安",
    "门卫": "门卫",
    "宿管": "宿管",
    "绿化": "绿化",
    "环卫": "环卫",
    "其他": "其他",
}

# ==================== 年龄适配中文映射 ====================
AGE_FIT_CN: dict[str, str] = {
    "eligible": "适合 60 岁",
    "review": "需人工确认",
    "ineligible": "不适合 60 岁",
    "unknown": "年龄未知",
}

# ==================== 数据来源中文映射 ====================
DATA_SOURCE_CN: dict[str, str] = {
    "detail": "详情页",
    "list_only": "仅列表页",
}

# ==================== 分区颜色（CSS）====================
SECTION_COLOR_STRONGLY_RECOMMEND: str = "#27ae60"  # 绿色
SECTION_COLOR_CONSIDER: str = "#2980b9"  # 蓝色
SECTION_COLOR_MANUAL_REVIEW: str = "#f39c12"  # 黄色
SECTION_COLOR_NOT_MATCH: str = "#c0392b"  # 红色

# ==================== 安全声明文本 ====================
SAFETY_STATEMENT: str = (
    "本报告仅作为找工作辅助信息，不保证岗位实时有效；"
    "年龄适配基于文本提取，不保证 100% 准确；"
    "岗位可能已关闭或招满，需联系招聘者确认；"
    "本工具不替用户自动投递或联系招聘者，不承诺永不封号。"
)

__all__ = [
    "REFERENCE_LOCATION_NAME",
    "DISTANCE_THRESHOLD_M",
    "DISTANCE_THRESHOLD_KM_TEXT",
    "CANDIDATE_AGE",
    "RULE_VERSION",
    "DESCRIPTION_SUMMARY_MAX_LENGTH",
    "RECOMMEND_LEVEL_CN",
    "ACTIVITY_LEVEL_CN",
    "JOB_CATEGORY_CN",
    "AGE_FIT_CN",
    "DATA_SOURCE_CN",
    "SECTION_COLOR_STRONGLY_RECOMMEND",
    "SECTION_COLOR_CONSIDER",
    "SECTION_COLOR_MANUAL_REVIEW",
    "SECTION_COLOR_NOT_MATCH",
    "SAFETY_STATEMENT",
]
