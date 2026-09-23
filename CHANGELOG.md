# 变更日志（Changelog）

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **诚实声明（请先读这三行）**
> 1. 本仓库**没有 git tag、没有 release**，`0.1.0` 是 `pyproject.toml:41` /
>    `frontend/package.json:4` 里声明的版本，**不是**一个已发布的制品。
> 2. 下面 `0.1.0` 一节是**首个公开版本的能力清单**，不是"逐次提交的回溯"。
>    完整过程台账（含每条改动的实测证据与未做事项）在 `docs/overhaul-plan.md`，
>    未做事项总表在该文件附录 BN（`:11132`）。
> 3. 只写**能核实**的内容：每条都带 `文件:行号` 或指向文档中的具体位置。
>    宁可不写，也不编。

---

## [Unreleased]

### Added

- `CONTRIBUTING.md`：把 CI 的门禁命令、测试环境变量与契约流程写成一份可执行的贡献指南。
- `SECURITY.md`：漏洞上报渠道 + **已知未处理发现的处置口径**。
- `CODE_OF_CONDUCT.md`（Contributor Covenant 2.1 中文版）、`CODEOWNERS`。
- `UPGRADING.md`：升级时 schema 怎么前滚、备份与恢复怎么用。
- `.editorconfig`：显式声明 `charset = utf-8` 与换行符策略（对齐 `.gitattributes`）。
- `.github/ISSUE_TEMPLATE/`（`bug_report.yml` / `feature_request.yml` / `config.yml`）、
  `.github/PULL_REQUEST_TEMPLATE.md`、`.github/dependabot.yml`。
- 容器化补缺：`backend/.dockerignore`（此前**只有** `frontend/.dockerignore`，
  见 `docs/open-source-readiness.md:579-580`）。

### Changed

- `docker-compose.yml` 文件头改为**如实描述**：默认只有
  `backend` / `frontend` / `celery-worker` / `celery-beat` 四个服务，
  全部基于 SQLite + 文件系统 broker；原先注释掉的
  PostgreSQL + Redis + MinIO 服务块已删除（这些中间件已被项目**明确放弃**，
  见 `AGENTS.md:15` 与 `backend/requirements.txt:31`）。
- `backend/Dockerfile`：删除 `mkdir -p /app/data/chroma`（Chroma 已废弃，
  见 `backend/requirements.txt:60` 与 `backend/app/models/chunk.py:5-26`），
  并修正与实际不符的注释；**未**新增任何"已验证"承诺。

### Security

- 本节**刻意不宣称"本批次修复了任何安全发现"**（同类声明最容易变成"文档比现实宽松"）。
  当前口径：依赖扫描在 CI 里**仍是建议性**的（`.github/workflows/ci.yml:340-362`），
  升级为阻断的前置条件见 `docs/security-scan.md:520-532`；
  已知未处理项（`python-jose` 的 CVE、镜像未加非 root `USER` / `HEALTHCHECK`、
  访问令牌无状态、限流无锁定）逐条列在 `SECURITY.md`。

---

## [0.1.0] - 2026-09-23

首个公开版本。产品命题是**把「资料 → 清洗 → 理解 → 复习」收进同一个自托管应用**
（`README.md:41-46`），三条设计原则：原文不可篡改、零外部依赖
（SQLite + 文件系统 broker，不装 PostgreSQL / Redis / 向量库）、
嵌入模型只在异步 worker 进程加载（`README.md:48-52`）。

### Added — 资料摄入

- 支持 **PDF / 图片 / Office / 音视频 / Markdown**；PDF 走 MinerU（云端 API 或本地模型），
  音视频走 Qwen3-ASR（`README.md:59`）。
- 「项目 + 状态旁载」存储结构 `{vault}/{user_id}/{project_slug}/source|output|history|cache`
  （`README.md:60`）。
- 手动放盘 + 「扫描导入」：文件拷进 `source/` 即可入库（`README.md:61`）。

### Added — AI 清洗与理解

- 逐行规则去噪（**代码块与数学块内不套规则**）+ BGE-M3 向量相似度去重；
  三视图（原始 / 清洗副本 / 行级 Diff），覆盖前自动建版本快照（`README.md:64-65`）。
- 章节摘要、**4 类知识卡片**（概念 / 公式 / 问答 / 定义）、
  自动出题（选择 / 填空 / 简答）（`README.md:68`）。
- Markdown 结构感知分段：按表格 / 代码块 / 列表的**原子块**切分，不从结构中间截断
  （`README.md:69`）。

### Added — 检索与问答

- 混合检索**两路**：SQLite FTS5 词法（BM25）+ `chunks` 表向量，RRF 融合
  （`rag_rrf_k=1`、BM25 权重 `0.65`）（`README.md:72`）。
- 向量与原文**同库同表**（`chunks`），不引入独立向量库（`README.md:73`）。
- SSE 流式问答 + 引用来源；检索失败时**显式降级**并告知用户（`README.md:74`）。
- 引用可回跳：chunk 落库时带定位信息，前端点击引用可定位并高亮
  （`docs/overhaul-plan.md:3016`）。

### Added — 复习与掌握度

- 调度器 **FSRS-5**（`review_scheduler="fsrs"`，可显式回退 SM-2），含 fuzz 与业务日到期时刻
  （`README.md:77`）。
- 掌握度双因子、薄弱点优先；每日答题上限 `daily_review_limit=10`（`README.md:78`）。
- 学习评估（笔记比对 + 盲点检测 + 改进建议）、学习目标（daily / weekly）与每日推荐任务
  （`README.md:79`）。
- 复习提醒：浏览器通知 + 可选 SMTP 邮件 + 免打扰时段；Celery Beat 定时刷新
  （`README.md:80`）。

### Added — 其他能力

- 知识图谱：嵌入相似度 + LLM 双机制推断关系，力导向可视化（`README.md:83`）。
- 笔记版本历史、回收站、批注与选中文本 AI 提问（`README.md:84`）。
- LLM 调用治理：重试、限流（按用户 / 供应商 / 总闸）、配额、缓存、成本记账
  （`README.md:85`；实现与逐条验收见 `docs/overhaul-plan.md` 阶段 4，`:3270-3285`）。
- **统一错误契约**：响应体为 `{detail, error_code, request_id}`
  （`backend/app/core/app_error.py:9`、`README.md:236-241`）。
- 运行状态探针：`GET /health`（零依赖存活）与 `GET /ready`
  （真实业务查询判就绪，队列深度只报告不决定状态码）（`README.md:228-231`、
  `docs/overhaul-plan.md:2946`）。

### Added — 工程与质量门禁

- **CI**（`.github/workflows/ci.yml`）：后端 ruff（`app` / `tests` / `scripts` 三段）
  + `.env.example` 一致性检查 + OpenAPI 契约检查 + **离线** pytest；
  前端 eslint + 契约生成幂等 + prettier + Vitest + `tsc`/`vite build` + Playwright E2E
  + axe-core 可访问性审计（`ci.yml:22-338`）。
- **依赖安全扫描**（`pip-audit` + `npm audit`）接入 CI，**建议性、不阻断**，
  报告归档（`ci.yml:340-479`；口径见 `docs/security-scan.md`）。
- **配置守卫**：nginx 上传体积与 SSE、`frontend/.dockerignore`、
  `frontend/Dockerfile` 的 `npm ci` + 锁文件（`ci.yml:481-529`）。
- 前端单元测试（Vitest + jsdom）、Playwright E2E 与 a11y 审计三层
  （`frontend/package.json:23-31`；背景见 `docs/overhaul-plan.md` 附录 AB / BE / BG）。

### Changed — 相对重构前的承重结构（摘要）

以下每条都在 `docs/overhaul-plan.md` 的对应阶段里有实测记录，这里只做摘要：

- **向量与索引**：从 Chroma（90+ collection）迁到 **`chunks` 表 + SQLite FTS5**
  （阶段 2.2′ / 2.4′ / 2.5′，`docs/overhaul-plan.md:3026-3039`、
  `backend/app/models/chunk.py:1-49`）。
- **检索融合**：删除字符 n-gram 通道，RRF 改为**加权**融合
  （阶段 2.6，`docs/overhaul-plan.md:3021`）。
- **调度算法**：SM-2 → **FSRS-5**（阶段 3.6 / 3.7 / 3.9，`docs/overhaul-plan.md:3446`）。
- **配置开关**：单一 `debug` 拆成 `APP_ENV` / `LOG_SQL` / `LLM_PROVIDER`
  三个互不相干的开关（阶段 4.10，`docs/overhaul-plan.md:3284`、
  `backend/app/config.py:554-592`）。⚠️ 一处**有意的行为变化**：
  `DEBUG=true` **不再**打开 SQL 日志。
- **迁移机制**：schema 由 `init_db()` + `_migrate_sqlite()` 前滚（只加列、不删数据），
  **启动路径不调用 alembic**（阶段 1.2/1.3 的结论，`docs/overhaul-plan.md:2981-2982`、
  `backend/requirements.txt:69-72`）。细节见 `UPGRADING.md`。
- **数据安全底线**：破坏性 schema 重建与全局去重**已移出启动路径**，
  需显式 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1` 才执行
  （阶段 0.3，`docs/overhaul-plan.md:2939`；实现见 `backend/app/database.py:260-276`、
  `:390-399`）。
- **SQLite 并发**：`journal_mode=WAL` + `busy_timeout=30000`（阶段 0.4，
  `docs/overhaul-plan.md:2940`）。
- **前端**：18 个页面 `React.lazy` 路由分包（阶段 5.4）、巨型页面拆分
  （阶段 5.5）、全局 CSS → CSS Modules 迁移（阶段 5.6）、可访问性审计与修复
  （阶段 5.9）——均见 `docs/overhaul-plan.md:3287-3310`。

### Removed

- `chromadb` 依赖与运行期的 Chroma 往返（`backend/requirements.txt:60`；
  收尾记录见 `docs/overhaul-plan.md` 附录 U，`:5368`）。
- 字符 n-gram 检索通道（`README.md:97`）。
- `cleaning_service.split_into_chunks`（统一到 `markdown_segmenter`，
  `docs/overhaul-plan.md:3015`）。
- 启动路径上的破坏性迁移调用（见上「Changed」最后两条）。

### Known issues / 未做（不是"以后再说"，是今天就不成立）

- **容器化未验证**：`Dockerfile` / `docker-compose.yml` / `nginx.conf` 保留在仓库里，
  但项目**实际以本地进程方式运行**，这些文件**从未构建或运行过**；
  配置守卫只保证"文件没被改坏"（`README.md:324-336`）。
- **依赖扫描不阻断**：见 `SECURITY.md` 与 `docs/security-scan.md:490-532`。
- **未做事项总表**：`docs/overhaul-plan.md` 附录 BN（`:11132`）逐条列出
  "哪些不影响现在的使用"。

---

## 版本政策（当前只有一条）

- 版本号写在 `pyproject.toml:41`（后端包）与 `frontend/package.json:4`（前端包）里，
  目前两者都是 `0.1.0`；**本仓库没有 git tag / release**，
  因此本文件与代码版本的一致性靠人工维护——这一条是已知缺口，不是机制。
- 破坏性变更会写进本文件并在 `UPGRADING.md` 里说明升级路径。

[Unreleased]: https://github.com/trx-0833/EngramNote/commits/main
[0.1.0]: https://github.com/trx-0833/EngramNote/commits/main
