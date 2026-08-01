"""P6 岗位筛选规则引擎模块。

子模块：
- constants: 规则常量（评分权重、关键字、阈值）
- models: RuleResult 等数据模型
- scoring: 评分与推荐等级计算
- engine: RuleEngine 入口（岗位分类、年龄提取、招聘者活跃、劳动强度、评分）

设计原则：
- 可解释、可测试、确定性的规则引擎
- 不接入 LLM / 机器学习
- 每条 explanation 必须是固定文本，不得生成随机描述
- 不破坏 GeoRepository / JobRepository / JobListRepository / JobDetailRepository
"""

from __future__ import annotations

__all__: list[str] = []
