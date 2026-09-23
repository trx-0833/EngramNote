# EngramNote

> AI 驱动的学习笔记管理与知识库 —— 把「资料 → 清洗 → 理解 → 复习」做成一个闭环

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-22.22.2+-green.svg)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-blue.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18+-blue.svg)](https://react.dev)
[![CI](https://github.com/trx-0833/EngramNote/actions/workflows/ci.yml/badge.svg)](https://github.com/trx-0833/EngramNote/actions/workflows/ci.yml)

> **文档校准（2026-09-23）**：本次 README 重写**以代码实测为准**，逐条更正了此前的错误陈述 ——
> 包括**调度算法（实际是 FSRS-5，SM-2 只是可回退路径）**、**检索路数（两路，n-gram 通道已删除）**、
> **RRF 参数（k=1、BM25 权重 0.65）**、**每日复习上限（10）**、
> **配置开关（`APP_ENV` / `LOG_SQL` / `LLM_PROVIDER`，`DEBUG` 已降级为遗留等价开关）**、
> **迁移机制（`init_db()` + `_migrate_sqlite`，不调用 alembic）**、
> **容器化（历史遗留、未验证）**。修订清单见 [docs/open-source-readiness.md](docs/open-source-readiness.md)。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [功能](#功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置](#配置)
- [运行状态与 API](#运行状态与-api)
- [质量门禁](#质量门禁)
- [项目结构](#项目结构)
- [文档](#文档)
- [License](#license)

---

## 它解决什么问题

读一份 PDF、做一遍笔记、过两周忘光 —— 这是大多数人的学习现状，
原因是**清洗、理解、复习三件事散落在三个工具里**，谁也不管下一步。

EngramNote 把这条链路收进同一个自托管应用：

```
上传资料 → 转 Markdown → AI 清洗去噪 → 提取知识卡片与题目
        → 知识图谱 → RAG 问答 → FSRS 间隔重复 → 学习报告
```

三条设计原则：

1. **原文不可篡改**：AI 只产出**旁挂**的清洗副本与卡片，原始文件与其转换结果永远保留；
2. **零外部依赖**：SQLite + 文件系统 broker，不装 PostgreSQL / Redis / 向量库；
3. **大数据模型隔离**：BGE-M3 等嵌入模型只在异步 worker 进程里加载，主进程保持轻量。

---

## 功能

**资料摄入**
- 支持 PDF / 图片 / Office / 音视频 / Markdown；PDF 走 MinerU（云端 API 或本地模型），音视频走 Qwen3-ASR
- 「项目 + 状态旁载」存储结构：`{vault}/{user_id}/{project_slug}/source|output|history|cache`
- 手动放盘：文件直接拷进 `source/` 后点「扫描导入」即可入库

**AI 清洗**
- 逐行规则去噪（**代码块与数学块内不套规则**）+ BGE-M3 向量相似度去重
- 三视图：原始 / 清洗副本 / 行级 Diff；覆盖前自动建版本快照，重复块可逐条恢复

**AI 理解**
- 章节摘要、4 类知识卡片（概念 / 公式 / 问答 / 定义）、自动出题（选择 / 填空 / 简答）
- Markdown 结构感知分段：按表格 / 代码块 / 列表的**原子块**切分，不从结构中间截断

**检索与问答**
- 混合检索 **两路**：SQLite FTS5 词法（BM25）+ `chunks` 表向量，RRF 融合（`rag_rrf_k=1`、BM25 权重 `0.65`）
- 向量与原文**同库同表**（`chunks`），不引入独立向量库
- SSE 流式问答，附引用来源；检索失败时**显式降级**并告知用户，而不是假装回答

**复习与掌握度**
- 调度器 **FSRS-5**（`review_scheduler="fsrs"`，可显式回退 SM-2），含 fuzz 与业务日到期时刻
- 掌握度双因子、薄弱点优先；每日答题上限 `daily_review_limit=10`
- 学习评估：笔记比对 + 盲点检测 + 改进建议；学习目标（daily / weekly）与每日推荐任务
- 复习提醒：浏览器通知 + 可选 SMTP 邮件 + 免打扰时段；Celery Beat 定时刷新

**其他**
- 知识图谱：嵌入相似度 + LLM 双机制推断关系，力导向可视化
- 笔记版本历史（用户编辑 / 自动清洗 / 系统快照）、回收站、批注与选中文本 AI 提问
- LLM 调用治理：重试、限流（按用户 / 供应商 / 总闸）、配额、缓存、成本记账

---

## 技术栈

| 层次 | 技术 | 说明 |
|---|---|---|
| 后端 | FastAPI + SQLAlchemy (async) + SQLite | 零外部数据库依赖（`aiosqlite`） |
| 异步任务 | Celery + 文件系统 broker + Celery Beat | 无需 Redis |
| 嵌入模型 | BGE-M3（`BAAI/bge-m3`） | **只在 Celery worker 进程加载**，不进主进程 |
| 向量与词法 | `chunks` 表 + SQLite **FTS5** | 同库同表；无 Chroma、无独立向量库 |
| 检索融合 | 向量 + BM25 → RRF | 两路（n-gram 通道已删除） |
| 调度算法 | **FSRS-5** | 可回退 SM-2；参数与事件流落在 `review_states` / `review_logs` |
| AI 调用 | DeepSeek / GLM（OpenAI 兼容） | 统一网关：重试 / 限流 / 缓存 / 记账 |
| 文档解析 | MinerU（云端 vlm-http-client 或本地 pipeline） | 保留 LaTeX 公式与表格 |
| 前端 | React 18 + TypeScript + Vite | 学术优雅视觉；React.lazy 路由分包 |
| 图谱可视化 | react-force-graph-2d | 力导向图 |
| 容器化 | Docker / Compose / Nginx | **历史遗留，从未构建验证**，见下文 |

---

## 快速开始

### 环境要求

| 软件 | 版本 | 说明 |
|---|---|---|
| **Python** | 3.10+ | 推荐 conda（脚本会提示 `conda create -n mineru_env python=3.10`） |
| **Node.js** | **22.22.2+** | 版本下界写在 `frontend/.nvmrc` 与 `frontend/package.json` 的 `engines.node`（`check_env.py` 读后者） |
| **Git** | 2.0+ | |
| **pip** | 23+ | |

> **无需安装**：PostgreSQL、Redis、MinIO、独立向量库。

> ⚠️ **Node 版本不是随便写的**：`engines.node` 是 `jsdom@30` 自己的要求
> （`^22.22.2 || ^24.15.0 || >=26.0.0`），而 `jsdom` 是 `vitest` 跑组件测试的环境。
> Node 18/20 上 `npm ci` **只会警告**（EBADENGINE）、装得完，但 `npm test` 会报一句
> 与原因无关的 `Test Files  no tests`。CI 里另有一道 `node -e "require('jsdom')` 守卫
> 让这类失败**指名道姓**（`.github/workflows/ci.yml`）。

### 一键安装与检测

```bash
# 1. 克隆
git clone https://github.com/trx-0833/EngramNote.git
cd EngramNote

# 2. （推荐）创建 conda 环境
conda create -n mineru_env python=3.10
conda activate mineru_env

# 3. 检测并自动修复必需项（依赖 + .env 模板 + BGE-M3 + VAD）
python check_env.py --fix
```

`check_env.py` 共 10 个检测步骤（版本 → 依赖 → `.env` → 模型 → 数据目录），常用参数：

```bash
python check_env.py                    # 仅检测，不修复
python check_env.py --fix              # 修复必需项（含 BGE-M3 ≈2.2GB）
python check_env.py --fix --all        # 追加可选大模型（MinerU ≈7GB + ASR ≈1.2GB）
python check_env.py --download-mineru  # 仅下载 MinerU（本地 PDF 解析用）
python check_env.py --download-asr     # 仅下载 ASR（音视频转写用）
```

> 国内源已内置：pip 用清华源、npm 用淘宝源、模型用 ModelScope。
> 想省 7GB 磁盘就别用本地 MinerU —— 配 `MINERU_API_TOKEN` 后默认走云端 API。

### 配置密钥

```bash
# 检测脚本已从模板生成 .env，手动填入密钥
# Windows: notepad backend\.env      Linux/macOS: nano backend/.env
```

| 变量 | 必需性 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | AI 功能二选一 | 生产推荐。[platform.deepseek.com](https://platform.deepseek.com/) |
| `GLM_API_KEY` | AI 功能二选一 | 有免费额度。[open.bigmodel.cn](https://open.bigmodel.cn/) |
| `JWT_SECRET_KEY` | **看环境** | `APP_ENV=prod`（默认）下**为空则拒绝启动**；`APP_ENV=dev` 下自动生成并持久化到 `data/.jwt-secret`。生成：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `MINERU_API_TOKEN` | 可选 | PDF 云端解析。[mineru.net](https://mineru.net/)；不配则只能用 Markdown 等文本格式 |

> ⚠️ **默认 `APP_ENV=prod`**：直接 `cp backend/.env.example backend/.env` 后不填 `JWT_SECRET_KEY`
> 会**启动失败**（这是有意的安全姿态，不是 bug）。本地开发请显式写 `APP_ENV=dev`。

### 启动

```bash
# Windows
start.bat

# Linux / macOS
chmod +x start.sh && ./start.sh
```

脚本会拉起 3 个进程：后端 API（**8001**）、Celery Worker、前端（**5173**）。

手动启动（3 个终端，命令与脚本一致）：

```bash
# 终端 1：后端 API
cd backend && python -m uvicorn app.main:app --reload --port 8001 --reload-dir app

# 终端 2：Celery Worker（Windows 必须加 --pool=solo）
cd backend && python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo

# 终端 3：前端
cd frontend && npm run dev
```

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:5173 |
| 后端 API | http://localhost:8001 |
| API 文档 | http://localhost:8001/docs（**仅 `APP_ENV=dev` 开放**；prod 下 `/docs`、`/redoc`、`/openapi.json` 全部关闭） |

> **首次上传 PDF** 后 worker 会加载嵌入模型（约 30 秒），之后常驻。

---

## 配置

完整字段见 `backend/app/config.py`（`Settings`），模板见 `backend/.env.example`。
最常用的四个开关：

| 变量 | 默认 | 作用 |
|---|---|---|
| `APP_ENV` | `prod` | `dev`：允许空 JWT 密钥（自动生成）、异常回吐 traceback、开放 `/docs`；`prod`：统一错误信息、关闭文档 |
| `LOG_SQL` | `false` | 是否把 SQL 打进 `data/logs`。⚠️ 含 bcrypt 哈希与卡片正文，**生产不要开** |
| `LLM_PROVIDER` | `auto` | `auto` / `deepseek` / `glm`；`auto` = dev 用 GLM、prod 用 DeepSeek |
| `REVIEW_SCHEDULER` | `fsrs` | `fsrs`（FSRS-5）或 `sm2`（回退） |

其他常改项：`CORS_ORIGINS`（默认本地 5173/3000）、`MAX_UPLOAD_SIZE_MB`（500）、
`DAILY_REVIEW_LIMIT`（10）、`EMBEDDING_MODEL` / `EMBEDDING_MODEL_FALLBACK`、
`VAULT_DIR`、SMTP 与提醒相关（`SMTP_*`、`EMAIL_REMINDER_ENABLED`、`REMINDER_QUIET_HOURS_*`）。

> 环境变量名与默认值以 `config.py` 为准；`.env.example` 只登记了常用的一部分。

---

## 运行状态与 API

| 端点 | 含义 |
|---|---|
| `GET /health` | **存活**探针：只返回应用状态，**刻意不检查依赖**（避免依赖抖动被翻译成重启） |
| `GET /ready` | **就绪**探针：用一次真实业务查询验证「数据库连得上且 schema 就绪」，就绪 200、否则 503；队列深度与索引积压只**报告**、不决定状态码 |

API 统一挂在 `/api` 下，按域分组：`auth` `notes` `upload` `cleaning` `understanding`
`review`（含快速复习）`report` `graph` `folders` `projects` `assessment` `knowledge`
`goals` `tasks` `llm`。错误响应是稳定契约：

```json
{ "detail": "面向用户的中文说明", "error_code": "STABLE_MACHINE_CODE", "request_id": "..." }
```

前端一律按 `error_code` 分流，**不要匹配中文文案**。

---

## 质量门禁

CI 定义在 `.github/workflows/ci.yml`，本地跑同样这几条（命令照抄 CI，不要凭记忆写）：

```bash
# 后端（在 backend/ 下）
python -m ruff check app tests scripts     # 阻断
python scripts/dump_openapi.py --check     # 契约：代码 → openapi.json，阻断
python -m pytest -q                        # 离线测试，网络被 tests/conftest.py 挡住，阻断
python -m ruff format --check app tests    # 建议性（有意为之，理由见 ci.yml:131-147）

# 前端（在 frontend/ 下）
npm ci
npm run lint          # ESLint，阻断
npm run gen:api       # openapi.json → src/api/generated/schema.ts（须零 diff），阻断
npm test              # vitest 单元/组件，阻断
npm run build         # tsc + vite build，阻断
npm run e2e           # Playwright（桩掉 /api），阻断
npm run a11y          # axe-core 可访问性，阻断（2026-09-23 起；此前是建议性）
```

另外两项在 CI 里跑：`security-scan`（`pip-audit` + `npm audit`，**只归档、不阻断**）与
`docker-nginx-config`（部署配置守卫，**阻断** —— 任一条 grep 断言失败即 job 失败）。
逐条理由与当前已知缺口见
[docs/open-source-readiness.md](docs/open-source-readiness.md)。

> **改后端接口后**：必须重跑 `python scripts/dump_openapi.py` 与 `npm run gen:api`，
> 否则前端会按过期契约编译。

---

## 项目结构

```
EngramNote/
├── backend/
│   ├── app/
│   │   ├── api/                # 路由（按域拆分；notes/ 与 api 大文件已模块化）
│   │   ├── models/             # ORM（20 个模型模块 / 21 张表）
│   │   ├── schemas/            # Pydantic 契约
│   │   ├── services/           # 业务逻辑：cleaning / embedding / rag / fsrs / scheduler
│   │   │                       #   mastery / graph / version / goal / notification / mineru / asr
│   │   │   └── llm/            # LLM 网关：gateway / prompts / scenes / client / 记账
│   │   ├── tasks/              # Celery 任务（convert / clean / understand / embedding / reminder）
│   │   ├── core/               # 错误契约、日志、请求上下文
│   │   ├── middleware/         # 限流、错误处理
│   │   ├── database.py         # 建表与运行时迁移（init_db + _migrate_sqlite）
│   │   └── config.py           # 全部配置字段（唯一权威）
│   ├── alembic/                # ⚠️ 历史遗留：启动路径**不调用**（见 docs 说明）
│   ├── scripts/                # 运维与开发脚本（备份/恢复/校验/评测/漂移检查）
│   ├── tests/                  # pytest（默认离线；integration 需显式开启）
│   └── requirements.txt
├── frontend/
│   ├── src/                    # React + TS（api / components / pages / hooks / styles）
│   ├── e2e/                    # Playwright：功能、a11y、全链路（默认不注册）
│   ├── scripts/                # CSS 与文档探针
│   ├── docs/                   # 前端专题文档（见下）
│   └── package.json
├── docs/                       # 架构、决策、整改计划与专题记录
├── check_env.py                # 环境检测与初始化
├── start.bat / start.sh        # 一键启动（3 进程）
├── docker-compose.yml          # ⚠️ 历史遗留，未验证（配套 backend/Dockerfile、
│                               #    frontend/Dockerfile、frontend/nginx.conf）
└── README.md
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/README.md](docs/README.md) | **文档地图：先看这一份** —— 三种状态（活文档 / 历史快照 / 过程记录）、阅读顺序、`docs/` 全量清单 |
| [docs/architecture.md](docs/architecture.md) | 系统架构、数据流、状态机、数据库概览（**重构前快照**，标注见文件头） |
| [docs/overhaul-plan.md](docs/overhaul-plan.md) | 重构全量计划与逐轮执行记录（**最完整的过程台账**） |
| [docs/decisions.md](docs/decisions.md) | 关键取舍归档（F-xx 编号，代码注释回链到此，**只读**） |
| [docs/sqlite-single-writer.md](docs/sqlite-single-writer.md) | 为什么 SQLite 路线下只能跑**一个** worker |
| [docs/security-scan.md](docs/security-scan.md) | 依赖与镜像扫描的当次原始结果与处置口径 |
| [docs/open-source-readiness.md](docs/open-source-readiness.md) | 开源化差距核查：必须修 / 应当修 / 建议新增（带 `文件:行号` 证据） |
| [frontend/docs/](frontend/docs/) | 契约生成与漂移检查、CSS 约定与迁移、e2e 说明、可访问性审计 |
| [docs/journal/](docs/journal/) | 过程记录：代码审查、启动验证、工具链检查的**当次报告**（仅供追溯，不是现状依据） |
| [docs/archive/](docs/archive/) | 历史设计文档（仅供追溯，**不要以它们为准**） |

### 关于容器化（请务必读这一段）

仓库里保留了 `docker-compose.yml` 与 `backend/Dockerfile` / `frontend/Dockerfile` /
`frontend/nginx.conf`，但：

- 本项目**实际以本地进程方式运行**（后端 8001 / 前端 5173）；
- 项目曾因磁盘空间与资源约束**明确放弃容器化与 PostgreSQL/Redis**；
- 这些文件**从未在本项目里构建或运行过**，配置守卫只保证"文件没被改坏"，
  **不代表这条路走得通**。

```bash
# ⚠️ 未验证：不要当作可用的部署路径，要用请先自行验证
docker compose up -d
```

---

## License

MIT License — 详见 [LICENSE](LICENSE)

---

**EngramNote** —— 从「被动阅读」到「主动内化 + 长期记忆」
