# BOSS直聘岗位辅助采集与筛选工具

> **当前阶段：P0 项目骨架（仅完成基础设施）**

## 项目用途

辅助一名已满 60 岁的求职者，在 BOSS 直聘上筛选杭州市拱墅区建国北路锦园小区 3 公里范围内的招聘岗位（保安 / 门卫 / 宿管 / 保洁 / 绿化 / 环卫）。

工具按以下维度排序与推荐：

- 年龄目标（明确 65 岁以下 > 区间含 60 上限 65 > 理论接受 60 > 边界 60 > 无明确年龄 > 排除 60）
- 距离（≤ 3 公里）
- 劳动强度（low / qualified medium 才进优先推荐）
- 招聘者活跃度与岗位是否仍在招聘
- 班次、工时与休息制度
- 薪资福利

最终输出含 7 个工作表的 Excel。

## 当前阶段（P0）能力

- Python 项目骨架与目录结构
- `pyproject.toml`、`.gitignore`
- 7 个 YAML 配置文件（`config/`）
- 全部枚举与 Pydantic v2 数据模型
- SQLite 数据库初始化（6 张表 + 索引 + schema_version 迁移）
- 基础 Repository（`JobRepository` / `RunLogRepository` / `CollectionMetaRepository` / `GeocodeCacheRepository`）
- 标准库日志（控制台 + RotatingFileHandler + 敏感关键字脱敏）
- Typer CLI：`doctor` / `init-db` / `show-config` 与预留命令 `run` / `resume` / `export`

## 当前阶段未实现

P0 阶段**未实现**以下功能，将在后续阶段补充：

- Playwright 浏览器启动（P1）
- 持久化登录流程（P1）
- 登录状态检测（P1）
- BOSS 直聘真实页面访问（P2 之后）
- CSS/XPath 选择器（P2 之后）
- 页面解析器（P3）
- 年龄规则匹配引擎（P5）
- 劳动强度规则匹配引擎（P5）
- 地理编码 API 调用与距离计算（P4）
- 评分与优先级（P7）
- Excel 7 工作表导出（P8）
- 两阶段采集与运行预算检查（P9）

## 安装方法

```bash
# 进入项目根目录
cd boss-job-assistant

# 安装（开发模式，含 dev 依赖）
pip install -e ".[dev]"

# 或仅安装运行依赖
pip install -e .
```

> 注意：`playwright` 已在 `pyproject.toml` 声明依赖，但 P0 阶段不会启动浏览器。
> 若安装失败，可暂时去掉该依赖再 `pip install -e .`。

## 运行测试方法

```bash
# 运行全部测试
python -m pytest -q

# 运行并查看覆盖率
python -m pytest --cov=src/boss_tool --cov-report=term-missing

# ruff 静态检查
ruff check .

# black 格式检查（不修改）
black --check .
```

## CLI 命令

```bash
# 帮助
python -m boss_tool --help

# 健康检查（不访问网络，不打开浏览器）
python -m boss_tool doctor

# 初始化 SQLite 数据库
python -m boss_tool init-db

# 显示当前配置（敏感字段脱敏）
python -m boss_tool show-config

# 以下命令为 P0 预留，未实现
python -m boss_tool run       # 输出 "该功能尚未在 P0 实现。"
python -m boss_tool resume    # 同上
python -m boss_tool export    # 同上
```

可选参数：`--config-dir / -c` 指定配置目录（默认项目根 `config/`）。

## 数据与账号安全说明

### 账号风险最小化（设计稿 v0.3 第二十一节）

- 仅使用用户本人正常登录的账号
- 强制可见浏览器（`headless=false`）
- 单账号、单浏览器上下文、单线程串行访问
- 人工确认后启动，不得默认无人值守
- 运行预算全部配置化（`max_*_*`），达到上限正常结束
- 异常立即停止（验证码 / 滑块 / 安全页 / 403 / 429 等），不自动恢复
- 两阶段采集：列表页初筛 + 按需访问详情页
- 本地缓存与去重：`visited_jobs` / `detail_content_hash` / `revisit_allowed_at`
- 测试优先使用本地 fixture，不反复访问真实网站

### 永久禁止的功能

本项目**永久不实现**以下功能：

- 浏览器指纹伪造、`navigator.webdriver` 修改
- `playwright-stealth` 等 stealth 插件
- 验证码识别、自动点击滑块
- 代理池、IP 轮换、多账号轮换
- Cookie 跨账号导入导出
- 请求签名逆向、未公开接口调用
- 自动沟通、自动投递、自动收藏、自动重新登录
- 任何反检测或规避平台风控代码

### 重要声明

> **本工具的所有措施只能减少不必要访问和程序失控风险，不能保证账号不会受到限制，也不得用于规避平台检测。**

本项目不得用于规避平台检测。运行预算与停止条件仅为控制负载与防止程序失控，**不代表"安全阈值"**，**不保证账号不受限制**，**不保证不会封号**。

### 数据安全

- `user_data/`（浏览器用户目录）、`logs/`、`output/`、`data/*.db` 已加入 `.gitignore`
- 高德 API key 仅通过环境变量 `AMAP_API_KEY` 注入，不写入配置文件
- 日志系统对 Cookie、API key、验证码等敏感关键字自动脱敏

## 项目结构

```text
boss-job-assistant/
├── pyproject.toml
├── .gitignore
├── README.md
├── config/             # YAML 配置
│   ├── app.yaml
│   ├── keywords.yaml
│   ├── locations.yaml
│   ├── runtime.yaml
│   ├── age_rules.yaml
│   ├── intensity_rules.yaml
│   └── scoring.yaml
├── data/               # SQLite 数据库（.gitignore）
├── logs/               # 日志文件（.gitignore）
├── output/             # Excel 导出（.gitignore）
├── user_data/          # 浏览器用户目录（.gitignore）
├── src/boss_tool/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── logging_config.py
│   ├── enums.py
│   ├── models/
│   ├── storage/
│   ├── browser/        # P1 占位
│   ├── parsers/        # P3 占位
│   ├── services/       # P4-P7 占位
│   └── exporters/      # P8 占位
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_models.py
    ├── test_database.py
    └── test_cli.py
```

## 当前没有实现真实网站采集

P0 阶段**不会**访问 BOSS 直聘，不会启动 Playwright 打开真实页面，不会编写任何 CSS/XPath 选择器。
所有页面相关字段均以 fixture 为准（待 P2 阶段采集）。
