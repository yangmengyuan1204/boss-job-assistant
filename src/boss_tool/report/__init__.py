"""P7 最终 HTML 岗位报告模块。

提供：
- ReportRepository: 只读查询层，JOIN job_list 与 job_detail
- ReportJob / ReportSummary / ReportMetadata: 报告数据模型
- CandidateAgeFit: 60 岁适配纯函数
- 多级排序与四分区分类
- HTMLRenderer: 安全的单文件 HTML 渲染（html.escape + sanitize_url）
- run_generate_report: 编排函数

安全约束：
- 不修改数据库（只读 SELECT）
- 不访问网络（离线运行）
- 不依赖 LLM / 机器学习
- 所有动态内容 html.escape 转义
- 所有链接复用 P2 sanitize_url 防御性校验
- 无外部 JS/CSS/CDN/字体/图片
"""

from __future__ import annotations

from boss_tool.report.age_fit import CandidateAgeFit, compute_age_fit
from boss_tool.report.models import (
    ReportJob,
    ReportMetadata,
    ReportSection,
    ReportSectionType,
    ReportSummary,
)
from boss_tool.report.repository import ReportRepository
from boss_tool.report.runner import run_generate_report
from boss_tool.report.sections import classify_section
from boss_tool.report.sorting import sort_jobs

__all__ = [
    "CandidateAgeFit",
    "compute_age_fit",
    "ReportJob",
    "ReportMetadata",
    "ReportSection",
    "ReportSectionType",
    "ReportSummary",
    "ReportRepository",
    "classify_section",
    "sort_jobs",
    "run_generate_report",
]
