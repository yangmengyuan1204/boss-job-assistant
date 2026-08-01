"""P6 规则引擎常量。

所有评分权重、关键字、阈值集中定义，便于测试与调整。
不得在 engine / scoring 中硬编码魔法数字。
"""

from __future__ import annotations

# ==================== 评分上限 ====================
MAX_SCORE: int = 100

# ==================== 距离评分（米）====================
DISTANCE_3KM_M: float = 3000.0  # <=3000 -> +30
DISTANCE_5KM_M: float = 5000.0  # 3000< <=5000 -> +10
# >5000 -> 0

SCORE_DISTANCE_WITHIN_3KM: int = 30
SCORE_DISTANCE_3_TO_5KM: int = 10
SCORE_DISTANCE_OVER_5KM: int = 0

# ==================== 岗位类型评分 ====================
# (类别, 分数)
JOB_CATEGORY_SCORES: dict[str, int] = {
    "保洁": 30,
    "保安": 25,
    "门卫": 25,
    "宿管": 20,
    "绿化": 20,
    "环卫": 15,
}
# 其他 -> 0

# ==================== 招聘者活跃评分 ====================
SCORE_RECRUITER_ACTIVE_3D: int = 20
SCORE_RECRUITER_ACTIVE_7D: int = 10
SCORE_RECRUITER_ACTIVE_OTHER: int = 0

# ==================== 薪资评分 ====================
SALARY_THRESHOLD_HIGH: int = 4000  # >=4000 -> +10
SALARY_THRESHOLD_LOW: int = 3000  # >=3000 -> +5
SCORE_SALARY_HIGH: int = 10
SCORE_SALARY_LOW: int = 5
SCORE_SALARY_OTHER: int = 0

# ==================== 推荐等级阈值 ====================
RECOMMEND_LEVEL_A_THRESHOLD: int = 85  # >=85 -> A
RECOMMEND_LEVEL_B_THRESHOLD: int = 70  # 70~84 -> B
RECOMMEND_LEVEL_C_THRESHOLD: int = 50  # 50~69 -> C
# <50 -> D

# ==================== 岗位分类关键字 ====================
# 按优先级：保洁 > 保安 > 门卫 > 宿管 > 绿化 > 环卫 > 其他
# 同一岗位命中多个类别时取首个（优先级高者）
JOB_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "保洁": ("保洁", "清洁", "清扫", "卫生员"),
    "保安": ("保安", "安保", "秩序维护"),
    "门卫": ("门卫", "门岗", "值班室"),
    "宿管": ("宿管", "宿舍管理", "寝室管理"),
    "绿化": ("绿化", "园林", "园艺", "植保"),
    "环卫": ("环卫", "清运", "垃圾处理"),
}

JOB_CATEGORY_OTHER: str = "其他"

# ==================== 年龄提取 ====================
# 匹配 "N岁以下" 文本，N 为整数
# 枚举值与 AgeStatus 对应
AGE_LIMIT_PATTERNS: dict[str, tuple[int, ...]] = {
    "limit_45": (45,),
    "limit_50": (50,),
    "limit_55": (55,),
    "limit_60": (60,),
    "limit_65": (65,),
}

# 无年龄限制文本
AGE_NO_LIMIT_KEYWORDS: tuple[str, ...] = (
    "年龄不限",
    "无年龄要求",
    "不限年龄",
)

# ==================== 招聘者活跃解析 ====================
# 3日内活跃
RECRUITER_ACTIVE_3D_KEYWORDS: tuple[str, ...] = (
    "今日活跃",
    "刚刚活跃",
    "今天活跃",
    "3日内活跃",
    "近3天活跃",
    "昨日活跃",
    "昨天活跃",
)
# 7日内活跃（本周）
RECRUITER_ACTIVE_7D_KEYWORDS: tuple[str, ...] = (
    "本周活跃",
    "7日内活跃",
    "近7天活跃",
    "近一周活跃",
)
# 其他/不活跃
# 非上述关键词均归为 other

# ==================== 劳动强度关键字 ====================
# 命中任一即增加 warning（不删除岗位）
LABOR_INTENSITY_KEYWORDS: tuple[str, ...] = (
    "重体力",
    "搬运",
    "装卸",
    "高空",
    "夜班",
    "长期站立",
    "高温",
    "流水线",
    "频繁加班",
)

# ==================== 固定解释文本 ====================
# 每条 explanation 必须是固定文本，不得随机生成
EXPLAIN_DISTANCE_WITHIN_3KM: str = "距离在3公里内，加分+30"
EXPLAIN_DISTANCE_3_TO_5KM: str = "距离在3至5公里，加分+10"
EXPLAIN_DISTANCE_OVER_5KM: str = "距离超过5公里，不加分"
EXPLAIN_DISTANCE_NONE: str = "无距离数据，距离项不加分"

EXPLAIN_CATEGORY_PREFIX: str = "岗位分类为"
EXPLAIN_CATEGORY_SCORED: str = "，加分+{score}"
EXPLAIN_CATEGORY_OTHER: str = "岗位分类为其他，不加分"

EXPLAIN_RECRUITER_3D: str = "招聘者3日内活跃，加分+20"
EXPLAIN_RECRUITER_7D: str = "招聘者7日内活跃，加分+10"
EXPLAIN_RECRUITER_OTHER: str = "招聘者活跃度不足或其他，不加分"

EXPLAIN_SALARY_HIGH: str = "薪资达4000+，加分+10"
EXPLAIN_SALARY_LOW: str = "薪资达3000+，加分+5"
EXPLAIN_SALARY_OTHER: str = "薪资未达3000或无法解析，不加分"

EXPLAIN_LABOR_WARNING_PREFIX: str = "检测到劳动强度关键字："
EXPLAIN_AGE_EXTRACTED: str = "年龄要求原文：{text}（状态：{status}）"
EXPLAIN_AGE_NONE: str = "未提取到年龄要求文本"

__all__ = [
    "MAX_SCORE",
    "DISTANCE_3KM_M",
    "DISTANCE_5KM_M",
    "SCORE_DISTANCE_WITHIN_3KM",
    "SCORE_DISTANCE_3_TO_5KM",
    "SCORE_DISTANCE_OVER_5KM",
    "JOB_CATEGORY_SCORES",
    "SCORE_RECRUITER_ACTIVE_3D",
    "SCORE_RECRUITER_ACTIVE_7D",
    "SCORE_RECRUITER_ACTIVE_OTHER",
    "SALARY_THRESHOLD_HIGH",
    "SALARY_THRESHOLD_LOW",
    "SCORE_SALARY_HIGH",
    "SCORE_SALARY_LOW",
    "SCORE_SALARY_OTHER",
    "RECOMMEND_LEVEL_A_THRESHOLD",
    "RECOMMEND_LEVEL_B_THRESHOLD",
    "RECOMMEND_LEVEL_C_THRESHOLD",
    "JOB_CATEGORY_KEYWORDS",
    "JOB_CATEGORY_OTHER",
    "AGE_LIMIT_PATTERNS",
    "AGE_NO_LIMIT_KEYWORDS",
    "RECRUITER_ACTIVE_3D_KEYWORDS",
    "RECRUITER_ACTIVE_7D_KEYWORDS",
    "LABOR_INTENSITY_KEYWORDS",
    "EXPLAIN_DISTANCE_WITHIN_3KM",
    "EXPLAIN_DISTANCE_3_TO_5KM",
    "EXPLAIN_DISTANCE_OVER_5KM",
    "EXPLAIN_DISTANCE_NONE",
    "EXPLAIN_CATEGORY_PREFIX",
    "EXPLAIN_CATEGORY_SCORED",
    "EXPLAIN_CATEGORY_OTHER",
    "EXPLAIN_RECRUITER_3D",
    "EXPLAIN_RECRUITER_7D",
    "EXPLAIN_RECRUITER_OTHER",
    "EXPLAIN_SALARY_HIGH",
    "EXPLAIN_SALARY_LOW",
    "EXPLAIN_SALARY_OTHER",
    "EXPLAIN_LABOR_WARNING_PREFIX",
    "EXPLAIN_AGE_EXTRACTED",
    "EXPLAIN_AGE_NONE",
]
