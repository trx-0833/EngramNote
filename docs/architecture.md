# EngramNote 架构文档

> **本文是项目唯一"活"的架构文档**。修改代码结构时请同步更新本文。
> 历史设计文档（项目架构.md、项目图解.md、新手教学.md 等）已归档至 `docs/archive/`，仅供追溯，内容可能与当前代码不一致，**不要以它们为准**。

写作日期：2025-06 · 适用范围：backend/app 与 frontend/src 当前代码节奏。

---

## 1. 系统总览

EngramNote 是"AI 驱动的学习笔记管理与知识库工具"：用户上传学习资料（PDF / 图片 / Office / 音视频 / Markdown），系统自动完成 **转换 → 清洗 → 理解** 三步加工，产出知识卡片与复习题目，再通过 **SM-2 间隔重复 + 知识图谱 + RAG 问答** 帮助用户内化知识。

```
┌──────────────────────┐        ┌─────────────────────────────────────┐
│  React SPA (Vite)    │  HTTP  │  FastAPI 后端 (uvicorn, :8001)        │
│  frontend/src        │ ─────► │  app/main.py → app/api/*（路由层）    │
│                      │ ◄───── │         │                             │
└──────────────────────┘  JSON  │         ▼ 业务逻辑                    │
                                │  app/services/*（服务层）             │
                                │  app/models/*（ORM，SQLite/Postgres） │
                                └───────┬─────────────────────────────┘
                                        │ 任务分发（Celery）
                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Celery Worker（独立进程，app/tasks/*）                                  │
│    convert_tasks   转换（MinerU 云端/本地、ASR 语音转写）                 │
│    clean_tasks     清洗（规则去噪 + BGE-M3 向量去重）                    │
│    understand_tasks 理解（LLM 摘要/知识卡片/出题）                       │
│    embedding_tasks 嵌入向量化（worker 进程隔离，避免主进程段错误）        │
│  Celery Beat（定时）：00:30 目标进度刷新 / 09:00 复习邮件               │
└──────────────────────────────────────────────────────────────────────┘
        │ 外部依赖
        ├─ LLM API：DeepSeek（生产）/ GLM（debug 开发），OpenAI 兼容接口
        ├─ Mineru API：PDF → Markdown（默认云端 vlm-http-client 模式）
        ├─ 嵌入模型：BGE-M3（ModelScope 优先下载，~2.2GB）
        ├─ Chroma：本地向量数据库（去重 + RAG 检索）
        └─ 文件存储：本地磁盘（Vault 目录结构）/ 可选 MinIO
```

**进程模型要点**：

| 进程 | 入口 | 职责 |
|---|---|---|
| API 进程 | `backend/app/main.py` | 全部 HTTP 接口、同步执行业务（fastapi uvicorn） |
| Celery Worker | `backend/app/tasks/celery_app.py` | 耗时管道（转换/清洗/理解/嵌入），独立数据库会话（`tasks/common.py` 的 `get_sync_session`） |
| Celery Beat | 同上 | 定时任务（见 `celery_app.py` 的 `beat_schedule`） |
| 前端开发服务器 | `frontend/`（vite dev / nginx 静态托管） | SPA 页面 |

---

## 2. 后端目录导航（backend/app/）

| 目录/文件 | 内容 | 什么时候改它 |
|---|---|---|
| `main.py` | 应用入口：lifespan（启动 init_db + 关闭释放 LLM 客户端）、CORS、全局异常处理器 | 启动行为、CORS、异常响应格式 |
| `config.py` | pydantic-settings 配置类（DB/存储/Celery/JWT/LLM/ASR/SMTP/复习/提醒/日志） | 新增配置项时 |
| `database.py` | 异步引擎、会话工厂、`get_db` 依赖、`init_db` | ⚠️ 有手写 SQLite 迁移（已知债，见 §10） |
| `api/` | 路由层（15 个文件）：auth / notes / upload / cleaning / understanding / review / quick_review / report / graph / folders / projects / assessment / knowledge / goals / router | 新增 HTTP 端点时；**路由内只做校验与组装，业务放 service** |
| `services/` | 业务层（36 个 Python 模块）：note_service（笔记）、llm_service（LLM 调用、1550 行已知债）、cleaning_service、understanding_service、embedding_service、rag_service（三路混合检索）、graph_service、review_service、sm2_service（纯算法）、mastery_service、goal_service、project_service、folder_service、assessment_service、report_service、notification_service、version_service、knowledge_link_service、auth_service、storage_service（本地/MinIO 抽象）、vault_meta / vault_path（Vault 镜像）、markdown_segmenter、pdf_crop；子包 `mineru/`（7 个模块，PDF 解析）、`asr/`（5 个模块，语音转写） | 改业务规则时；这是代码主体所在 |
| `tasks/` | Celery：celery_app（应用+信号）、common（worker 会话工厂/状态更新白名单）、convert / clean / understand / embedding / reminder | 异步管道逻辑 |
| `models/` | ORM（15 张表，见 §8），`base.py` 提供 BaseModel（id/created_at/updated_at）与 TZDateTime | 改表结构（配合迁移，见 §9） |
| `schemas/` | Pydantic 请求/响应模型，与 api/ 一一对应 | 改接口数据结构时 |
| `core/` | context（contextvars 请求/任务上下文）、logging_config（日志格式）、tempfile_compat（mkdtemp 环境兼容） | 可观测性与环境兼容 |
| `middleware/` | error_handler（统一错误 JSON）、request_context（request_id 注入 + 访问日志） | 中间件行为 |
| `utils/` | timeutil（业务日界 = Asia/Shanghai 零点，F-32 语义） | 时区/日期工具 |
| `alembic/` | 迁移（versions 001-009） | ⚠️ 迁移体系与 database.py 手写迁移并存（已知债，见 §10） |

**分层约定（现状与目标）**：当前 `notes.py`、`upload.py`、`understanding.py` 有大量业务逻辑直接写在路由层（已知债），目标是把路由层收敛为"参数校验 + 调 service + 组装响应"。

---

## 3. 前端目录导航（frontend/src/）

| 目录/文件 | 内容 | 什么时候改它 |
|---|---|---|
| `main.tsx` / `App.tsx` | 入口与全部路由（19 个页面，见 App.tsx Routes） | 新增页面/路由时 |
| `api/client.ts`（2221 行，已知债） | 请求封装（request/uploadRequest/SSE）+ Token 管理 + 全部类型定义 + 90 个 API 函数；`api/knowledge.ts` 是唯一已完成拆分的模块 | 调后端接口时 |
| `pages/` | 19 个页面：Dashboard / NotesList / NoteDetail / Trash / Upload / KnowledgeCards / KnowledgeGraph / CardDetail / QA / Review / QuestionSets / TodayLearn / QuickReview / DailyMaterials / Projects / LearningAssessment / LearningGoals / Login / Register | 页面级功能 |
| `components/` | 通用组件：Sidebar / CleaningPanel / DeleteNoteDialog / DiffView / VersionHistory / ReminderBanner / NoteAskPanel（选中文本 AI 提问浮层）/ quiz/QuizAnswerCard / EmptyState / ErrorDisplay / LoadingSpinner | 可复用 UI |
| `hooks/` | useAdhdReader（专注阅读渐变遮罩） | 跨组件逻辑复用 |
| `contexts/AuthContext.tsx` | 认证状态（token 持久化 localStorage） | 认证流程 |
| `utils/` | labels（卡片类型颜色/标签单一数据源）、markdown（渲染）、datetime、notifications | 工具函数 |
| `styles/global.css`（2906 行，已知债） | 全部全局样式（含大量补丁式追加区段） | 样式 |

**页面 → 主要接口调用**：各页面直接 import `api/client.ts` 中的函数；无状态管理库，页面内 useState/useEffect 自管。

---

## 4. 核心数据流（上传 → 复习）

```
用户上传文件
  │  POST /api/upload            api/upload.py（两阶段：prepare → commit）
  ▼
存储原始文件（Vault source/ + 数据库 notes 记录，status=uploading）
  │  Celery: convert_document_task    tasks/convert_tasks.py + services/mineru|asr
  ▼
转换 Markdown（status=converting → converted；原始版本只读保留）
  │  Celery: clean_document_task      tasks/clean_tasks.py + services/cleaning_service.py
  │  （规则去噪 + BGE-M3 相似度去重，产出 clean 副本；覆盖前自动创建版本快照）
  ▼
清洗完成（status=cleaned; 前端 CleaningPanel 展示行级 Diff，可恢复/删除被去重的块）
  │  Celery: understand_document_task  tasks/understand_tasks.py + services/understanding_service.py
  │  （LLM 章节摘要 + 知识卡片提取 + 自动出题）
  ▼
理解完成（status=learning → cleaned；产出 knowledge_cards / quiz_items）
  │
  ├──► 知识图谱   graph_service（嵌入相似度 + LLM 推断，CardRelation 关系）
  ├──► 智能问答   rag_service（三路混合检索：向量 + BM25 + n-gram，RRF 融合，SSE 流式）
  ├──► 复习       review_service（SM-2 调度，薄弱点优先，每日上限）
  ├──► 评估       assessment_service（资料↔笔记比对、盲点检测）
  └──► 报告/目标  report_service、goal_service（Beat 每日 00:30 刷新进度）
```

**选中文本 AI 提问（仅当前笔记）**：笔记阅读页选中文本后，批注浮层提供「AI 提问」→ 前端截取选区局部上下文（选中文本 + 前后各 1500 字符）→ 新端点 `POST /api/notes/{note_id}/ask/stream`（`api/notes/ask.py`，仅校验笔记归属，不起 RAG、不读全文）→ `LLMService.chat_stream` SSE 逐 token 返回（事件 `meta(provider)` → `token...` → `done` / `error`）→ 前端 `NoteAskPanel.tsx` 浮层流式渲染（可停止 / 重新提问 / 关闭）。（决策见 docs/decisions.md F-36）

**回收站（软删除）**：`DELETE /api/notes/{id}` → `trash_note`（标记 trashed_at + 文件搬至 trash/ 目录）；`purge_note` 物理删除时采用"悬挂引用"策略（关系记录被删端置 NULL，绝不级联删除，核心卡片可提升为独立节点）。

---

## 5. 核心状态机

### 笔记状态 NoteStatus（models/note.py）

```
uploading ─► converting ─► converted ─► cleaning ─► cleaned ─► learning ─► archived
                │              │            │           │            │
                ▼              ▼            ▼           ▼            ▼
              failed    ┌─ cleaning_failed ◄┘    ┌─ learning_failed ◄┘
              （转换失败）  （可重新触发清洗）       （可重新触发理解）
任何状态 ─► archived（手动归档）；archived ◄─►（取消归档回到 cleaned/converted）
任何状态 ─► trashed_at 非空（回收站，软删除）；可 restore / purge（物理删除）
```

### 其他关键枚举

- **NoteRole**：`material`（学习资料）/ `personal_note`（我的笔记），决定双向链接方向。
- **CardRelation**：`related / prerequisite / subsequent / contrast`（无向 vs 有向语义不同）。
- **CardCategory**：`regular / blind_spot / extension`（普通 / 盲点 / 拓展卡片）。

---

## 6. 数据库模型概览（models/，15 张表）

| 模型 | 表 | 一句话职责 |
|---|---|---|
| User | users | 用户与密码哈希 |
| Note | notes | 笔记主实体：状态、文件路径、角色、回收站标记 |
| Folder | folders | 按日期组织的文件夹 |
| Project / NoteProject | projects / note_projects | 项目（纯标签，笔记多对多） |
| KnowledgeCard | knowledge_cards | 知识卡片（含盲点/拓展/重点/难度/掌握度） |
| QuizItem | quiz_items | 复习题目 + SM-2 调度参数（interval/repetition/easiness/next_review_at） |
| ReviewLog | review_logs | 每次答题记录 |
| CardRelation | card_relations | 卡片间语义关系（图谱边） |
| AssessmentResult | assessment_results | 学习评估结果（模式：compare / quiz / combined_extract） |
| NoteMaterialLink | note_material_links | 个人笔记 ↔ 学习资料 双向链接 |
| NoteAnnotation | note_annotations | 笔记批注（高亮/下划线） |
| NoteVersion | note_versions | 版本快照（USER_EDIT / AUTO_CLEAN / SYSTEM） |
| LearningGoal / DailyPlan | learning_goals / daily_plans | 学习目标与每日推荐任务 |

⚠️ **已知债**：表结构与实际约束存在"模型 / database.py 手写 DDL / alembic 迁移"三处出处并存的漂移风险（见 §10-1）。

---

## 7. 存储布局（Vault）

默认根：`backend/data/vault`（可用 `VAULT_DIR` 覆盖；未配置时兼容旧 `data/storage`）。

```
{vault}/{user_id}/{project_slug}/
├── source/            原始上传文件
├── output/
│   ├── markdown/      转换 Markdown（{base}.md 原始 + {base}.clean.md 清洗副本）
│   └── meta/          状态旁载镜像（{base}.json，"状态不撒谎"，DB 仍是权威源）
└── history/versions/  版本快照归档
```

关联法则：`source/{base}{ext}` ↔ `output/markdown/{base}.md` 仅扩展名不同；meta 镜像由 `services/vault_meta.py` 在状态变更时写穿（失败仅记日志）。

---

## 8. 可观测性（core/context.py + core/logging_config.py）

- 每个请求注入 `request_id`，响应头 `X-Request-ID` 返回，日志自动携带 `rid=...` 标签。
- Celery 任务注入 `tid / task=` 标签；业务可设 `biz=`（如 note_id）。
- 统一日志格式 + `errors.log` 独立落盘；所有错误响应体统一为 `{detail, error_code, request_id}`。

---

## 9. 数据库迁移现状与演进方向

**现状（已知债）**：Alembic（versions 001-009）+ `database.py` 启动时无条件 `create_all()` + 手写 SQLite 补列/建表/重建表（`_migrate_sqlite` / `_rebuild_dangling_tables`）三套并存；迁移链缺少基线（001 依赖已存在表，全新库 `upgrade head` 失败）；`requirements.txt` 中 alembic 被注释。

**演进方向（已规划）**：收敛为单一通道 —— 模型为唯一 schema 源，Alembic 为唯一迁移通道（补基线迁移、SQLite batch mode、启动不再建表），删除 `database.py` 手写迁移。详见后续文档 `docs/database.md`（规划中）。

---

## 10. 已知技术债索引（按修复优先级）

| # | 债务 | 位置 | 修复方向 |
|---|---|---|---|
| 1 | 数据库迁移三套并存、无基线 | database.py、alembic/、requirements.txt | 收敛到 Alembic 单一通道（§9） |
| 2 | 修复编号注释 F-01~F-35（180+ 处） | 全仓 | 迁移到 docs/decisions.md，代码注释只留 WHY |
| 3 | 错误契约混乱：service 层五态返回（None/dict/异常/[]/False）、前端匹配中文错误文案、裸 except 泄露内部错误 | services/、api/（如 assessment.py）、frontend | 自定义 AppError + 稳定错误码 |
| 4 | 巨型文件：notes.py(1031) / llm_service.py(1550) / client.ts(2221) / KnowledgeGraph.tsx(1547) / global.css(2906) | 见文件 | 按职责拆分，只搬不改 |
| 5 | LLM JSON 容错错位：parse_json_tolerant(90 行)是死代码，真实解析点裸 json.loads | llm_service.py | 统一解析入口 |
| 6 | 测试体系混乱：tests/ 混入调试脚本（pytest 收集即烧真实 API/改生产库）、week 系列三份拷贝、根目录 15 个一次性脚本 | backend/tests/、backend 根目录 | 守卫 + 收拢 + helpers 抽取 |
| 7 | 前端生命周期缺陷（blob URL 泄漏、轮询无清理）、类型安全失守（Promise\<any\>、20 处 as any） | NoteDetail.tsx、DailyMaterials.tsx、KnowledgeGraph.tsx等 | effect 清理约定 + 精确类型 |
| 8 | 模块导入副作用：database.py 导入即建引擎、tempfile_compat monkeypatch 标准库 | database.py、main.py、celery_app.py | 惰性化/显式化 |

修复顺序建议（每步独立可回退）：**0 文档**（本文）→ **契约层**（错误码/常量）→ **拆分巨型文件**（纯搬移）→ **测试整顿** → **数据库迁移收敛** → **decisions.md 建立**。

---

## 11. 常用命令

```bash
# 后端（backend/ 目录下）
python -m uvicorn app.main:app --reload --port 8001 --reload-dir app
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo   # Windows 需 solo
python -m celery -A app.tasks.celery_app:celery_app beat --loglevel=info

# 前端（frontend/ 目录下）
npm run dev            # 开发
npm run build          # 构建（tsc && vite build）

# 环境检测
python check_env.py --check    # 检测
python check_env.py --fix      # 自动修复（含模型下载）

# 测试（注意：目前仓库存在测试体系混乱问题，见 §10-6）
cd backend && python -m pytest tests/ -x -q    # 仅跑 tests/ 下正式测试
```