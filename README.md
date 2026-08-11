# EngramNote

> AI 驱动的学习笔记管理与知识库工具 —— 从原始资料到长期记忆的完整学习闭环

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-18+-green.svg)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-blue.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18+-blue.svg)](https://react.dev)

---

## 目录

- [功能概览](#功能概览)
- [V2.0 新增功能](#v20-新增功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
  - [环境要求](#环境要求)
  - [一键安装与检测](#一键安装与检测)
  - [配置 API 密钥](#配置-api-密钥)
  - [启动项目](#启动项目)
- [Docker 部署](#docker-部署)
- [项目结构](#项目结构)
- [核心流程](#核心流程)
- [API 密钥获取指南](#api-密钥获取指南)
- [模型下载说明](#模型下载说明)
- [常见问题](#常见问题)
- [文档](#文档)
- [License](#license)

---

## 功能概览

- **资料摄入**：上传 PDF / 图片 / Office / 音视频 / Markdown，自动转换为 Markdown
- **AI 清洗**：规则去噪 + BGE-M3 向量相似度去重，生成干净的学习副本（三视图：原始 / 清洗 / 行级 Diff 对比）
- **AI 理解**：章节摘要、4 类知识卡片提取（概念 / 公式 / 问答 / 定义）、自动出题（选择 / 填空 / 简答）
- **知识图谱**：嵌入相似度 + LLM 双机制自动推断卡片间关系，力导向图可视化
- **智能问答**：基于知识库的 RAG 问答，附引用来源；V2.0 升级为三路混合检索（向量 + BM25 + n-gram）+ RRF 融合，支持 SSE 流式输出
- **间隔重复**：SM-2 算法调度复习，薄弱点优先，掌握度双因子公式（60% 正确率 + 40% SM-2）
- **学习评估**：笔记比对 + 盲点检测 + 改进建议
- **笔记版本历史**（V2.0）：手动编辑 / 自动清洗 / 系统快照三类版本，支持 diff 对比与一键恢复
- **学习目标与计划**（V2.0）：daily / weekly 目标管理，自动生成每日推荐任务（薄弱点 > 复习 > 新资料）
- **复习到期提醒**（V2.0）：浏览器通知 + 邮件提醒 + 免打扰时段，Celery Beat 每日定时刷新与推送
- **学术优雅界面**：深海军蓝 + 墨金 + 暖米白 + Noto Serif SC 衬线字体 + 微动效

---

## V2.0 新增功能

V2.0 在 V1.x 基础上完成 6 项核心增强，覆盖检索、交互、版本管理、目标驱动与提醒推送：

- **RAG 向量检索修复与混合搜索**
  - 将 BGE-M3 嵌入模型加载隔离到 Celery Worker 进程（`backend/app/tasks/embedding_tasks.py`），彻底解决 FastAPI 主进程段错误
  - `rag_service.py` 重构为三路混合检索：向量召回 + 纯 Python BM25（k1=1.5, b=0.75，零新依赖）+ n-gram
  - 采用 RRF（Reciprocal Rank Fusion）融合多路结果，公式 `score(d) = Σ 1/(k + rank_i(d))`，k=60
  - 降级策略：向量失败 → BM25 + n-gram；全部失败 → LLM 自身知识
- **流式问答输出（SSE）**
  - `llm_service.py` 新增 `chat_stream()`，基于 httpx `stream=True` 实现 token 级流式推送
  - 新端点 `POST /api/understanding/ask/stream`，SSE 事件：`token` / `sources` / `done` / `error`
  - `frontend/src/pages/QA.tsx` 重构为流式渲染，首字到达后实时显示；复用 DeepSeek 缓存（共享 system prompt 前缀）
- **笔记版本历史**
  - 新增 `note_versions` 表（NoteVersion 模型），版本来源 `USER_EDIT`（保留 50 个）/ `AUTO_CLEAN`（保留 10 个）/ `SYSTEM`（保留 50 个）
  - 版本存储路径：`{user_id}/{project_slug}/history/versions/v{N}.md`（Vault 版本区）
  - `version_service.py` 提供创建 / 列表 / 预览 / diff / 恢复 / 清理全流程
  - 编辑或清洗覆盖前自动创建版本快照；前端 `VersionHistory.tsx` 模态组件支持 diff 对比与恢复
- **学习目标与计划管理**
  - 新增 `learning_goals` 与 `daily_plans` 表；目标类型 daily / weekly，状态 active / completed / expired / archived / deleted
  - 每用户最多 5 个 active 目标；`goal_service.py` 提供 CRUD + 进度计算 + 每日推荐
  - 每日推荐三类任务优先级：`weak_points`(3) > `review`(2) > `new_materials`(1)，上限 `DAILY_REVIEW_LIMIT=50`
  - Celery Beat 每日 00:30 自动刷新目标进度；`LearningGoals.tsx` 页面 + Dashboard 集成目标卡片
- **复习到期提醒**
  - `notification_service.py` 提供提醒数据查询与邮件发送；`GET /api/review/reminders` 返回到期数 / 1 小时内到期 / 薄弱点数
  - 可选 SMTP 邮件提醒（配置开启），Celery Beat 每日 09:00 发送
  - 前端 `notifications.ts` 浏览器通知工具（权限请求 / 通知发送 / 免打扰 / sessionStorage 去重）
  - `ReminderBanner.tsx` 提醒横幅；免打扰时段默认 22:00-08:00
- **数据库迁移 004**
  - `database.py` 的 `_migrate_sqlite` 新增 3 张表（note_versions / learning_goals / daily_plans）防御性建表
  - `backend/alembic/versions/004_v2_models.py` Alembic 迁移脚本
- **项目隔离 + 状态旁载（Vault 目录结构）**
  - 存储改为「项目 + 状态旁载」结构：新增 `projects` 表（迁移 005），每个笔记归属一个项目目录
  - 目录树：`{vault}/{user_id}/{project_slug}/source/`（原始文件）、`output/markdown/`（`{base}.md` 原始转换 + `{base}.clean.md` 清洗副本）、`output/meta/`（`{base}.json` 状态旁载镜像）、`history/versions/`（版本归档）、`output/assets/` 与 `cache/`（预留）
  - 命名关联法则：`source/{base}{ext}` ↔ `output/markdown/{base}.md` 仅扩展名不同；状态只写入 `output/meta/{base}.json`，DB 仍为权威源（写穿镜像）
  - Vault 根目录可用环境变量 `VAULT_DIR` 覆盖（默认 `backend/data/vault`）；本地与 MinIO 共用同一套 object-name 约定
  - 项目 slug 创建后不可变（作为 Vault 目录名），重命名只改显示名
  - 创建项目时**同步预建磁盘目录树**（`source/output/history/cache`），前端 Projects 页展示 Vault 路径
  - **手动放盘 + 扫描导入**：把文件直接拷贝到项目 `source/` 子目录后，点击「扫描导入」（`POST /projects/{id}/scan`）自动识别为笔记并触发转换，已导入文件自动跳过

---

## 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy (async) + SQLite | 零外部数据库依赖 |
| 异步任务 | Celery + 文件系统 broker + Celery Beat | 无需 Redis；V2.0 引入 Beat 定时任务（每日刷新目标进度 / 发送复习邮件） |
| 流式输出 | SSE（Server-Sent Events） | V2.0 问答接口 token 级流式推送，复用 DeepSeek 缓存 |
| AI 理解 | DeepSeek / GLM API | 通过 OpenAI 兼容接口调用；httpx stream=True 实现流式 |
| 文档解析 | MinerU | PDF → Markdown（保留 LaTeX 公式、表格） |
| 嵌入模型 | BGE-M3 (BAAI/bge-m3) | 多语言嵌入，ModelScope 优先下载；V2.0 隔离到 Celery Worker 进程 |
| 向量存储 | Chroma | 嵌入式向量数据库 |
| 混合检索 | 向量 + BM25（纯 Python）+ n-gram + RRF 融合 | V2.0 RAG 三路混合检索，零新依赖 |
| 前端 | React 18 + TypeScript + Vite | 学术优雅视觉设计 |
| 图谱可视化 | react-force-graph-2d | 力导向图，4 种节点形状 |
| 容器化 | Docker + Docker Compose + Nginx | 一键部署 |

---

## 快速开始

### 环境要求

| 软件 | 版本 | 说明 |
|------|------|------|
| **Python** | 3.10+ | 推荐 conda 环境 |
| **Node.js** | 18+ | 前端构建 |
| **Git** | 2.0+ | 克隆项目 |
| **pip** | 23+ | Python 包管理 |

> **无需安装**：PostgreSQL、Redis、MinIO —— 默认使用 SQLite + 文件系统 broker + 本地文件存储，零外部依赖。

### 一键安装与检测

本项目提供**自动环境检测脚本**，会自动检测环境、安装依赖、下载模型（通过国内源）：

```bash
# 1. 克隆项目
git clone https://github.com/你的用户名/EngramNote.git
cd EngramNote

# 2. （推荐）创建 conda 环境
conda create -n mineru_env python=3.10
conda activate mineru_env

# 3. 运行环境检测（自动安装依赖 + 下载模型）
python check_env.py --fix
```

检测脚本会自动完成以下 10 个步骤：

| 步骤 | 检测内容 | 自动修复 |
|------|----------|----------|
| 1 | Python 3.10+ 版本 | - |
| 2 | Node.js 18+ 与 npm | - |
| 3 | 后端 Python 依赖（17 个包 + qwen_asr 可选） | 清华 PyPI 源自动安装 |
| 4 | 前端 Node 依赖 | 淘宝 npm 源自动安装 |
| 5 | .env 配置文件 | 从 .env.example 自动创建 |
| 6 | BGE-M3 嵌入模型（2.2GB，必需） | ModelScope 国内源自动下载 |
| 7 | Silero VAD 模型（2MB，ASR 用，可选） | torch.hub / ModelScope 自动下载 |
| 8 | MinerU 模型（7GB，PDF 本地解析用，可选） | 需 `--all` 参数，或使用云端 API 替代 |
| 9 | Qwen3-ASR 模型（1.2GB，音视频转写用，可选） | 需 `--all` 参数 |
| 10 | 运行时数据目录 | 自动创建 |

#### 命令参数说明

```bash
python check_env.py                      # 仅检测不修复
python check_env.py --fix                # 自动修复必需项（依赖+BGE-M3+VAD）
python check_env.py --fix --all          # 包含可选大模型（MinerU 7GB + ASR 1.2GB）
python check_env.py --download-mineru    # 仅下载 MinerU 模型
python check_env.py --download-asr       # 仅下载 ASR 模型
```

> **推荐做法**：先运行 `python check_env.py --fix`（下载必需项），然后根据需要运行 `--download-mineru` 或 `--download-asr` 下载可选模型。

### 配置 API 密钥

检测脚本会自动从 `.env.example` 创建 `.env` 文件，你需要**手动编辑**填入 API 密钥：

```bash
# 编辑配置文件
# Windows: notepad backend\.env
# Linux/Mac: nano backend/.env
```

**必填项（二选一）**：

```env
# 选项 A：DeepSeek API（生产环境推荐）
DEEPSEEK_API_KEY=sk-your-deepseek-key-here

# 选项 B：GLM API（开发调试推荐，有免费额度）
GLM_API_KEY=your-glm-key-here
```

**可选配置**：

```env
# JWT 密钥（生产环境务必更换，生成方法见下方）
JWT_SECRET_KEY=your-random-secret-key

# 文档解析（不配置则无法解析 PDF）
MINERU_API_TOKEN=your-mineru-token
```

> 详细的 API 密钥获取方法见下方 [API 密钥获取指南](#api-密钥获取指南)。

### 启动项目

**方式一：一键启动脚本（推荐）**

```bash
# Windows
start.bat

# Linux/Mac
chmod +x start.sh
./start.sh
```

启动脚本会自动运行环境检测，然后启动 3 个服务：
- Backend API（端口 8000）
- Celery Worker（异步任务）
- Frontend（端口 5173）

**方式二：手动启动（3 个终端）**

```bash
# 终端 1：后端 API
cd backend
python -m uvicorn app.main:app --reload --port 8000 --reload-dir app

# 终端 2：Celery Worker（异步任务）
cd backend
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo

# 终端 3：前端
cd frontend
npm run dev
```

**访问地址**：

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:5173 |
| 后端 API | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |

> **首次上传文件**：第一次上传 PDF 后，系统会加载嵌入模型（约 30 秒），之后会缓存为模块级单例。

---

## Docker 部署

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

Docker 配置使用国内镜像源加速：
- **pip**：清华源 `https://pypi.tuna.tsinghua.edu.cn/simple`
- **npm**：淘宝源 `https://registry.npmmirror.com`

> **注意**：Docker 镜像不包含嵌入模型（文件过大，约 2.2GB）。生产环境需挂载本地模型目录或首次启动时自动下载。

---

## 项目结构

```
EngramNote/
├── backend/                     # 后端 (FastAPI)
│   ├── app/
│   │   ├── api/                 # API 路由（含 V2.0 新增：versions/goals/reminders/ask/stream）
│   │   ├── models/              # 数据模型（14 个表，V2.0 新增 note_versions/learning_goals/daily_plans）
│   │   ├── schemas/             # Pydantic Schema
│   │   ├── services/            # 业务逻辑
│   │   │   ├── mineru/          # PDF 解析服务
│   │   │   ├── asr/             # 语音转写服务
│   │   │   ├── cleaning_service.py    # AI 清洗管道
│   │   │   ├── embedding_service.py   # BGE-M3 嵌入
│   │   │   ├── llm_service.py         # DeepSeek/GLM 调用（V2.0 新增 chat_stream）
│   │   │   ├── rag_service.py         # RAG 问答（V2.0 三路混合检索 + RRF 融合）
│   │   │   ├── version_service.py     # 笔记版本历史（V2.0 新增）
│   │   │   ├── goal_service.py        # 学习目标管理（V2.0 新增）
│   │   │   ├── notification_service.py# 复习提醒 + 邮件（V2.0 新增）
│   │   │   ├── sm2_service.py         # SM-2 间隔重复算法
│   │   │   ├── graph_service.py       # 知识图谱双机制推断
│   │   │   ├── mastery_service.py     # 掌握度双因子计算
│   │   │   └── ...
│   │   ├── tasks/               # Celery 异步任务（V2.0 新增 embedding_tasks/reminder_tasks + Beat 定时）
│   │   ├── middleware/          # 中间件
│   │   ├── config.py            # 配置管理（pydantic-settings）
│   │   └── main.py              # 应用入口
│   ├── alembic/                 # 数据库迁移（V2.0 新增 004_v2_models.py）
│   ├── tests/                   # 测试
│   ├── .env.example             # 环境变量模板（提交到 Git）
│   ├── .env                     # 你的私有配置（不提交，.gitignore 排除）
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                    # 前端 (React + TypeScript + Vite)
│   ├── src/
│   │   ├── api/                 # API 请求封装
│   │   ├── components/          # 通用组件（V2.0 新增 VersionHistory/ReminderBanner）
│   │   ├── pages/               # 页面组件（V2.0 新增 LearningGoals；QA 重构为流式）
│   │   ├── contexts/            # React Context
│   │   ├── utils/               # 工具函数（V2.0 新增 notifications.ts）
│   │   └── styles/              # 全局样式
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── check_env.py                 # 环境自动检测脚本
├── start.bat                    # Windows 一键启动
├── start.sh                     # Linux/Mac 一键启动
├── docker-compose.yml           # Docker Compose 配置
├── .gitignore                   # Git 忽略规则
├── .env.example                 # 环境变量模板（根目录引用）
└── README.md                    # 本文件
```

---

## 核心流程

```
上传资料 → MinerU 转 Markdown → AI 清洗（去噪+去重，自动版本快照）→ AI 理解（摘要+知识点+题目）
                                                              ↓
                                                        知识图谱构建
                                                              ↓
学习评估 ← SM-2 复习 ← RAG 问答（三路混合检索+SSE 流式）← 知识卡片库
                                                              ↓
                                                    学习目标管理 + 复习提醒
```

1. **上传** → PDF/图片/Office/音视频 自动转换为 Markdown
2. **清洗** → 规则去噪 + BGE-M3 向量去重，生成干净副本（三视图对比）；覆盖前自动创建版本快照
3. **理解** → AI 提取章节摘要、4 类知识卡片、自动出题
4. **图谱** → 嵌入相似度 + LLM 双机制自动推断卡片间关系
5. **复习** → SM-2 间隔重复，薄弱点优先，掌握度双因子计算
6. **评估** → 笔记比对 + 盲点检测 + 改进建议
7. **报告** → 每日学习统计与 7 天趋势分析
8. **目标**（V2.0）→ 设定 daily/weekly 目标，自动生成每日推荐任务（薄弱点 > 复习 > 新资料）
9. **提醒**（V2.0）→ 浏览器通知 + 邮件提醒 + 免打扰时段，Celery Beat 每日 00:30 刷新目标进度、09:00 发送复习邮件

---

## API 密钥获取指南

本项目需要以下 API 密钥，请按需配置：

### 1. DeepSeek API（必填，二选一）

- **用途**：AI 理解管道（摘要、知识点提取、题目生成、RAG 问答）
- **获取地址**：https://platform.deepseek.com/
- **步骤**：
  1. 注册 DeepSeek 账号
  2. 进入 API Keys 页面
  3. 创建新的 API Key
  4. 复制到 `.env` 文件：`DEEPSEEK_API_KEY=sk-xxxxxxxx`

### 2. GLM API（必填，二选一，推荐开发调试）

- **用途**：DeepSeek 的备选方案，有免费额度，适合开发调试
- **获取地址**：https://open.bigmodel.cn/
- **步骤**：
  1. 注册智谱 AI 账号
  2. 进入 API 管理页面
  3. 创建 API Key
  4. 复制到 `.env` 文件：`GLM_API_KEY=xxxxxxxx`
- **切换方式**：`.env` 中设置 `DEBUG=true` 使用 GLM，`DEBUG=false` 使用 DeepSeek

### 3. Mineru API Token（可选，PDF 解析需要）

- **用途**：PDF 文档解析为 Markdown（保留 LaTeX 公式、表格）
- **获取地址**：https://mineru.net/
- **步骤**：
  1. 注册 Mineru 账号
  2. 进入个人中心获取 API Token
  3. 复制到 `.env` 文件：`MINERU_API_TOKEN=xxxxxxxx`
- **不配置的后果**：无法上传 PDF，但 Markdown 文件可直接使用

### 4. JWT 密钥（生产环境必填）

- **用途**：JWT Token 签名，保护 API 安全
- **生成方法**：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

- **配置**：将生成的字符串填入 `.env`：`JWT_SECRET_KEY=你的随机字符串`

---

## 模型下载说明

本项目使用以下 5 个 AI 模型，均通过**国内源（ModelScope）**下载：

### 模型总览

| 模型 | 大小 | 必需性 | 用途 | 下载命令 |
|------|------|--------|------|----------|
| **BGE-M3** | 2.2GB | 必需 | 文本向量化（清洗去重、知识图谱） | `--fix` 自动下载 |
| **Silero VAD** | 2MB | 可选 | 语音活动检测（ASR 切分语音段） | `--fix` 自动下载 |
| **MinerU Pipeline** | 5GB+ | 可选 | PDF 本地解析（布局/公式/表格识别） | `--download-mineru` 或 `--all` |
| **MinerU VLM** | 2.5GB | 可选 | PDF 本地解析（视觉语言模型） | `--download-mineru` 或 `--all` |
| **Qwen3-ASR** | 1.2GB | 可选 | 音视频转写（语音转文字） | `--download-asr` 或 `--all` |

### 1. BGE-M3 嵌入模型（必需，约 2.2GB）

- **用途**：文本向量化，用于清洗去重和知识图谱
- **下载源**：ModelScope（国内）优先，HuggingFace 镜像备选
- **自动下载**：运行 `python check_env.py --fix` 自动下载
- **手动下载**：

```bash
# 方式 1：ModelScope（推荐，国内速度快）
pip install modelscope
python -c "from modelscope import snapshot_download; snapshot_download('Xorbits/bge-m3')"

# 方式 2：HuggingFace 镜像
set HF_ENDPOINT=https://hf-mirror.com
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"
```

- **缓存位置**：`~/.cache/modelscope/hub/Xorbits/bge-m3/`

### 2. Silero VAD 模型（可选，约 2MB）

- **用途**：语音活动检测，ASR 音视频转写时切分语音段
- **下载源**：torch.hub（GitHub）/ ModelScope
- **自动下载**：运行 `python check_env.py --fix` 自动下载
- **本地路径**：`backend/data/models/silero-vad/silero_vad.jit`
- **不下载的后果**：ASR 功能会降级为固定时长切分（仍可使用，但效果较差）

### 3. MinerU 模型（可选，PDF 本地解析用，约 7GB）

> **重要**：如果配置了 `MINERU_API_TOKEN` 并设置 `MINERU_BACKEND=vlm-http-client`（云端 API 模式），则**无需下载**本地模型，可节省约 7GB 磁盘空间。推荐使用云端 API。

MinerU 本地 pipeline 模式需要 2 个模型：

#### 3a. Pipeline 模型 (PDF-Extract-Kit-1.0，约 5GB+)

- **用途**：PDF 布局检测、公式识别、表格识别
- **ModelScope ID**：`OpenDataLab/PDF-Extract-Kit-1.0`
- **下载命令**：

```bash
python check_env.py --download-mineru
# 或手动：
python -c "from modelscope import snapshot_download; snapshot_download('OpenDataLab/PDF-Extract-Kit-1.0')"
```

- **缓存位置**：`~/.cache/modelscope/hub/models/OpenDataLab/PDF-Extract-Kit-1___0/`

#### 3b. VLM 模型 (MinerU2.5-Pro-2604-1.2B，约 2.5GB)

- **用途**：PDF 视觉语言模型解析
- **ModelScope ID**：`OpenDataLab/MinerU2.5-Pro-2604-1.2B`
- **下载命令**：同上（与 Pipeline 模型一起下载）
- **缓存位置**：`~/.cache/modelscope/hub/models/OpenDataLab/MinerU2___5-Pro-2604-1___2B/`

#### 选择本地模式 vs 云端 API

| 模式 | 配置 | 优点 | 缺点 |
|------|------|------|------|
| **云端 API（推荐）** | `MINERU_API_TOKEN=你的token` + `MINERU_BACKEND=vlm-http-client` | 无需下载 7GB 模型 | 消耗 API 额度 |
| **本地 Pipeline** | `MINERU_BACKEND=pipeline` + 下载模型 | 离线可用，无 API 费用 | 占用 7GB 磁盘 + 首次下载耗时 |

### 4. Qwen3-ASR 模型（可选，音视频转写用，约 1.2GB）

- **用途**：音视频文件转写为文字
- **ModelScope ID**：`Qwen/Qwen3-ASR-0.6B`
- **依赖**：需先安装 `qwen-asr` 包（`pip install qwen-asr`）
- **下载命令**：

```bash
python check_env.py --download-asr
# 或手动：
pip install qwen-asr
python -c "from modelscope import snapshot_download; snapshot_download('Qwen/Qwen3-ASR-0.6B')"
```

- **缓存位置**：`~/.cache/modelscope/hub/models/Qwen/Qwen3-ASR-0___6B/`
- **不下载的后果**：无法上传音视频文件，PDF/图片/Office/Markdown 功能不受影响

### 国内源配置

本项目默认使用以下国内源加速下载：

| 类型 | 源地址 | 配置位置 |
|------|--------|----------|
| PyPI（Python 包） | `https://pypi.tuna.tsinghua.edu.cn/simple` | check_env.py, start.bat/sh |
| npm（Node 包） | `https://registry.npmmirror.com` | check_env.py, start.bat/sh |
| ModelScope（模型） | `https://modelscope.cn` | check_env.py |
| HF 镜像（模型备选） | `https://hf-mirror.com` | 环境变量 HF_ENDPOINT |

---

## 常见问题

### Q: 启动后访问前端显示空白？

**A**: 确保前端依赖已安装：`cd frontend && npm install`。运行 `python check_env.py --check` 检测。

### Q: 上传 PDF 后一直处于 "converting" 状态？

**A**: 检查是否配置了 Mineru API Token。查看 Celery Worker 终端的错误日志。运行 `python check_env.py --check` 确认环境。

### Q: 清洗功能报错 "model not found"？

**A**: BGE-M3 模型未下载。运行 `python check_env.py --fix` 自动下载（约 2.2GB，需要较长时间）。

### Q: AI 理解功能报错 "API key not configured"？

**A**: 检查 `backend/.env` 文件是否配置了 `DEEPSEEK_API_KEY` 或 `GLM_API_KEY`。参考 [API 密钥获取指南](#api-密钥获取指南)。

### Q: Celery Worker 启动失败？

**A**: Windows 环境下需要加 `--pool=solo` 参数。启动脚本已自动配置。手动启动时请确保在 `backend/` 目录下运行。

### Q: 如何切换 DeepSeek / GLM？

**A**: 编辑 `backend/.env`：
- `DEBUG=true` → 使用 GLM（开发调试，有免费额度）
- `DEBUG=false` → 使用 DeepSeek（生产环境）

### Q: 如何重置数据库？

**A**: 删除 `backend/data/db/engramnote.db` 文件，重启后端会自动重建。

---

## 文档

- [项目架构](项目架构.md) — 架构设计与技术决策
- [项目图解](项目图解.md) — 图解式全貌梳理（Mermaid 图：架构/流程/数据模型/Vault 目录）
- [开发时间表](开发时间表.md) — 12 周完整开发记录
- [新手教学](新手教学.md) — 面向初学者的代码讲解
- [UX 审计报告](UX_AUDIT_REPORT.md) — 用户体验审计与改进

---

## License

MIT License — 详见 [LICENSE](LICENSE) 文件

---

**EngramNote** — 从"被动阅读"到"主动内化 + 长期记忆"的学习闭环
