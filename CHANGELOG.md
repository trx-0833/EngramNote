# 变更日志（Changelog）

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **诚实声明（请先读这三行）**
> 1. `v0.1.0` 已有 **annotated tag**（`git cat-file -t v0.1.0` → `tag`，指向 `154060c`），
>    **但 GitHub Releases 页面尚未创建** —— 仓库里的 tag 与页面上的 release 对象是两件事。
>    版本号 `0.1.0` 写在 `pyproject.toml:41` 与 `frontend/package.json:4` 里，
>    两者是否一致由守卫测试 `backend/tests/test_version_single_source.py` 守着（这是**机器**保证，
>    不是靠人记得改）。
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
  见 `docs/open-source-readiness.md` §3.6（依赖漂移））。

### Changed

- `docker-compose.yml` 文件头改为**如实描述**：默认只有
  `backend` / `frontend` / `celery-worker` / `celery-beat` 四个服务，
  全部基于 SQLite + 文件系统 broker；原先注释掉的
  PostgreSQL + Redis + MinIO 服务块已删除（这些中间件已被项目**明确放弃**，
  见 `AGENTS.md:15` 与 `backend/requirements.txt:31`）。
- `backend/Dockerfile`：删除 `mkdir -p /app/data/chroma`（Chroma 已废弃，
  见 `backend/requirements.txt:60` 与 `backend/app/models/chunk.py:5-26`），
  并修正与实际不符的注释；**未**新增任何"已验证"承诺。
- **前端依赖树升级（2026-09-24）**：合并 Dependabot 的四个大版本/副版本 PR，
  让"声明的版本"与"实际在跑的版本"重新对齐（此前 6 条 pip 声明已对齐，这次是前端侧）：

  | 依赖 | 从 | 到 | 说明 |
  |---|---|---|---|
  | `vite` | 5.4.21 | **8.3.0** | 打包器由 **Rollup 换成 Rolldown**；`esbuild` / `rollup` 已不在依赖树里 |
  | `@vitejs/plugin-react` | 4.7.0 | **6.1.1** | 必须与 vite 8 联动（4.7.0 的 peer 不含 `^8`）|
  | `vitest` | 2.1.9 | **4.1.11** | 同上，2.x 与 vite 8 配置契约不兼容 |
  | `typescript-eslint` | 8.67.0 | **8.70.0** | 副版本 |
  | `marked` | 14.1.4 | **18.0.13** | 跨 4 个大版本，**升级前先补了 14 条公式渲染护栏** |
  | `react-router-dom` | 6.30.4 | **7.18.4** | 唯一进生产包的一个 |

  **分包配置随打包器迁移**：`frontend/vite.config.ts` 从
  `build.rollupOptions.output.manualChunks`（Rolldown 下**已弃用**，函数形式仍可用）
  迁到 `build.rolldownOptions.output.codeSplitting.groups`。
  迁移后三个手工分包（`react` / `graph` / `markdown`）的产物**文件名逐字节相同**，
  即 chunk 边界未变。

  **浏览器下界变化（如实记录）**：Vite 8 的默认 `build.target` 是
  `baseline-widely-available`，展开为 `chrome111 / edge111 / firefox114 / safari16.4 / ios16.4`
  （源码实测）。产物语法扫描显示实际只用到 Chrome 85 级别的语法。
  `tsconfig.json` 的 `target` 与 `package.json` 的 `engines.node` **均未改动**
  （vite 8 要 Node `^20.19 || >=22.12`，本项目 `.nvmrc` 是 `22.22.2`，本来就在范围内）。

  **一条 npm 自身缺陷的记录**：本项目环境里的 npm 10.9.8（Node 22.22.3 自带）在解析
  `vitest@4` 的**可选 peer 环**（`vitest` ↔ `@vitest/browser-*`）时崩溃
  （`Cannot read properties of null (reading 'edgesOut')`，`#loadPeerSet` 无限递归）。
  绕法是换 **npm 11** 执行同一条安装命令；`lockfileVersion` 仍是 3，
  CI 用 `npm ci` 只读锁文件、不重新解析，故 runner 上不受影响。

- **`check_env.py` 的依赖检测改为「并行 + 分层」：每个启动周期都跑的第 3 步从 31.98s 降到 1.35s**
  （2026-09-26；本机 conda `mineru_env` / Python 3.10.21 / Windows，三轮取中位数）。
  `start.bat:96` 与 `start.sh:84` 每次启动都会跑这一步，而它原来把 22 个模块
  **串行真实导入**：`sentence_transformers` 20.6s、`qwen_asr` 6.2s、`openai` 2.7s、
  `torch` 2.8s，其余 18 个合计不到 1.4s。而**后端真实启动**（`import app.main`）只要
  4.2s，且 `sys.modules` 里没有这四个 —— 那 30s 验的不是「能不能启动」。

  - 省下来的时间**不靠并行**：线程池 8 线程实测只值 **-11%**（`check_env.py:544` 记了原因：
    GIL 下 import 的耗时大头是模块体字节码，不释放 GIL）。真正的大头是**分层**。
  - 四个**启动路径不会导入**的重型模块默认只用 `find_spec` 确认「装没装」
    （22 个模块合计 **0.007s**），真实导入交给新增的 `--deep`
    （`check_env.py:1147`；`--fix` 自动深度检测）。`--deep` 下全量真实导入为
    **26.34s**，比原版还快 17.6%（并行那部分仍然有效）。
  - **严格性没有静默降低**：第 3 步收尾会点名列出「仅确认已安装」的模块与 `--deep`
    的用法（`check_env.py:597`），不把两种结论印成一样的 ✓。判据写在
    `check_env.py:490` 的 `HEAVY_MODULES`。
  - 顺带修掉并行引入的**假失败**：失败项一律**串行复验**后才判负。独立子进程实测
    「`sqlalchemy` + `alembic` 两线程并发」**8/8** 撞上
    `partially initialized module ... circular import` —— 直接采信第一次结果就会把
    已安装的包报成未安装。
  - 顺带补上两处**隐蔽的重型导入**护栏（否则快路径会被它们偷偷跑掉）：
    `check_silero_vad` 的 `import torch`（`check_env.py:825`）与
    `check_qwen_asr` 的 `import qwen_asr`（`check_env.py:1009`）。
  - `start.bat:117` / `start.sh:102` 用 `find_spec` 取代重复的
    `python -c "import fastapi"`（实测 **0.704s → 0.111s**）：该步只决定要不要跑
    `pip install`，而第 3 步已经真实导入过一次。
  - 分层的前提（「导入昂贵」且「不在 `import app.main` 的闭包内」）由
    `backend/tests/test_check_env_probe.py` **起子进程实测**守着：哪天有人把
    `torch` / `openai` 加进启动路径，那条守卫会红，而不是等用户机器上
    「自检全绿但服务起不来」。

- **知识图谱的大图性能：新增规模探针 + 批次 1 优化**（2026-09-26）。
  真实库是 **1181 节点 / 260 边**（`backend/data/db/engramnote.db` 只读统计），
  而 `GET /api/graph`（`backend/app/api/graph.py:57-71`）没有上限，全部下发全部渲染。

  **先立基线**：新增真 Chromium 探针 `frontend/e2e/graph-perf-probe.spec.ts`
  （`npm run perf:graph`，桩数据见 `e2e/graph-perf-fixtures.ts` —— 复制真实结构、
  标题用中性占位，真实语料不进仓库）。它量三件事：空闲重绘次数、
  **每帧强制重绘下的帧间隔**、以及 canvas 调用的逐项归因（含消融实验）。

  | 指标（1181 节点 / 260 边） | 改前 | 改后 |
  |---|---|---|
  | 每帧强制重绘 | **78.13 ms（12.8 fps）**，p95 117.10 ms | **42.54 ms（23.5 fps）**，p95 81.20 ms |
  | 空闲时重绘（用户没动） | 57 次 / 3 s（≈19 次/秒） | **0** |
  | 每帧 `createRadialGradient` | 1322 | 23 |
  | 每帧 `measureText` / `roundRect` | 1097 / 1097 | 26 / 26 |

  > 上表是**同一时间窗内的三方对比**（各 3 次取中位数，`f10f6a5` 与它的前一版
  > 交替跑）。帧率对机器负载极敏感：同一份代码在不同时段量到 34.9 ~ 46.4 ms，
  > 所以"改前/改后"必须同时段量才算数 —— 跨时段比出来的数字不算证据。

  改了什么（全部按**画布实际节点数**分级，阈值 `LARGE_GRAPH_NODES = 300`，
  既有的一切小图场景 —— a11y 桩 4 节点、全链路用例、视觉基线 —— 渲染逐字不变）：

  - **节点阴影与玻璃径向渐变按需**（`knowledgegraph/drawNode.ts`）：大图时只给
    枢纽（度 ≥ 3）与选中/悬停/待创建的节点保留，其余用一次实心填充的廉价光晕代替。
    消融实测这两项在改前合计约 14.6 ms/帧。
  - **粒子按需**（`components/graph/GraphCanvas.tsx`）：`force-graph.mjs:1626-1628`
    的 `doRedraw` 里有一条"任何一条边有粒子就重绘"，而真实数据 260 条边的
    `similarity_score` **全部 > 0.6**（每条 2 个粒子 = 520 个持续动画的粒子），
    于是画布**永不空闲**。大图时关掉。
  - **视口剔除**（同上，`nodeVisibility`）：force-graph 默认不剔除，离屏节点
    照样每帧走完整绘制。判据抽成纯函数 `components/graph/viewportCulling.ts`。
  - **搜索命中改集合判定**（`knowledgegraph/useCanvasObjects.ts`）：
    原先每帧每节点线性扫搜索结果，是 O(节点数 × 结果数)。
  - **minimap 建 id 索引**（`knowledgegraph/renderMinimap.ts`）：原先每条边两次
    `nodes.find`，260 边 × 1181 节点 ≈ 每帧三十万次比较；同时去掉
    `Math.min(...xs)` 对 1181 个元素的参数展开。
  - **删掉每帧 1181 次的死分配**：`node.__bckgDimensions` 在整个依赖树里
    **零消费者**（`force-graph` / `react-force-graph-2d` / `kapsule` 等的 dist
    里搜不到），是旧版 API 的遗留。

  > ⚠️ **同批次内修掉一个自己引入的回归：视口剔除算错了变换，把左半边的节点藏了。**
  > 第一版拿 `onZoom` 回调参数当 d3-zoom 的平移量，而它是**图空间里的视口中心**
  > （`force-graph.mjs:1506` 的 `{ ...zoomTransform, ...centerAt() }`，后者覆盖前者）；
  > 加上真实初值是 `{ k: 1, x: width/2, y: height/2 }`（`force-graph.mjs:999-1002`）
  > 而页面里停在 `{ k: 1, x: 0, y: 0 }` —— 可见区域被算成图空间左上角那一块：
  > **该看见的藏起来、界外的反倒画出来**（实测越界 4397 次、最远 290 px）。
  > 修法：真值改由 `onRenderFramePre` + `graph2ScreenCoords(0, 0)` 每帧写入，
  > 并在拿到真实变换之前**一律不剔除**。顺带修好 **minimap 的视口框** ——
  > 它按"`vp.x` 是平移量"计算（`vx = -vp.x / vp.k`），而拿到的一直是图空间中心，
  > 所以框的位置本来就是错的。
  > 守卫：`components/graph/viewportCulling.test.ts`（8 条，逐点压坐标换算）
  > + 探针的第 ⑤ 条（端到端，跑在旧版本上会红：4397 次越界）。

  守卫：新增 `src/pages/knowledgegraph/drawNode.test.ts`（10 条）、
  `src/components/graph/viewportCulling.test.ts`（8 条）与
  `src/components/graph/types.test.ts` 的阈值用例，锁住"降级不能降过头"
  （枢纽/选中/悬停仍走昂贵路径）与"小图行为不变"。

  > 未做（如实登记）：`GET /api/graph` 仍无上限，节点数与下发量本身没变；
  > 批次 2（按缩放级别的 LOD）与批次 3（服务端上限 / 默认子图模式）尚未开工。

- **知识图谱的关系判定：下界按「是否同笔记」分档（同日三次修正的最终形态）**
  （2026-09-26）。真实库 **1181 张卡片只有 120 张有边**，1061 张是孤点 —— 测量后发现
  根因不是"还没轮到"，而是判定方法本身在这份语料上**几乎不建边**。

  > ⚠️ **这一条经过三次修正，请注意哪一版是最终形态。**
  > 1. `f454a1b`：下界 0.75 → **0.55** —— **错**（判准只看标题、且判的人看得见分数）
  > 2. `da775b2`：下界修回 **0.75** + 同章节要求 0.80 —— 方向对，但**轴拧错了**
  > 3. **本次**：轴从「是否同**章节**」改成「是否同**笔记**」
  > 下面写的是最终形态。

  **先测量再改**：新增只读脚本 `backend/scripts/measure_relation_quality.py`
  （不写库、不调 LLM、不联网、不下载模型），报告在
  `docs/journal/relation-quality-2026-09-26.md`（§8 撤回声明、§9 盲判 v2、§10 本批）。

  **盲判 v2**（每格 24 对、判的人看不到任何分数、层内控制笔记关系、掺 16 个重复项）：

  | 层 | 跨笔记 | 同笔记跨章节 |
  |---|---|---|
  | 余弦 ≥0.85 | 83% [55, 95] | **100%** [76, 100] |
  | 余弦 0.75–0.85 | **50%** [25, 75] | **83%** [55, 95] |
  | 余弦 0.70–0.75 | 25% [9, 53] | 42% [19, 68] |
  | 余弦 0.65–0.70 | **0%** [0, 24] | 42% [19, 68] |
  | 同章节 ≥0.80 | — | **100%** [86, 100] |
  | 同章节 0.75–0.80 | — | **92%** [74, 98] |

  自洽率（16 个重复项、甲乙互换后重判）**16/16**。

  **三条结论**（都改变了实现）：

  1. **真正的分档轴是「笔记」，不是「章节」**：四个余弦层里同笔记对**全部**优于
     跨笔记对。跨笔记的余弦更容易被"同领域"骗到 —— 两份不同的技术标准都在讲电力
     设备，用词与术语体系都像，但讲的是**不同的对象**。
  2. **同章节不该特殊对待**：`0.75-0.80` 实测 92%，上一版要求它 ≥0.80 是多余的收紧。
  3. **「有联系」与「值得建边」是两个目标**：可导航 67%、可行动（可合并/排序/对比）
     只有 **17%** —— 混成一个"精确率"没有意义，报告里已分开报。

  **改动**（`backend/app/services/graph_service.py`，只在 `auto_suggest_relations` 内）：

  - `MIN_SIMILARITY = 0.75`（**同笔记**，含同章节）
  - 新增 `CROSS_NOTE_MIN_SIMILARITY = 0.85`（**跨笔记**）
  - **删掉** `SAME_CHAPTER_MIN_SIMILARITY`；取数不再查 `chapter_title`（轴不需要它）
  - `TOP_K_PER_CARD = 3` 不变 —— 它**不是判据**，只是把候选推到高分段
  - **契约未动**：函数签名、返回的 `new_count`、`GraphSuggestResponse` 一律不变

  **效果**（200 张候选池，线上口径）：

  | 配置 | 新建边 | 覆盖候选卡 | 预期精确率\* |
  |---|---|---|---|
  | 上一版 `da775b2` | 108 | 95/200 | 95% |
  | **最终形态** | **151** | **120/200** | 95% |

  \* 按每条边所在分层套上表点估计再取均值。

  **读法**：本批收益是**覆盖**（95 → 120 张卡），不是精确率。
  「跨笔记 0.85」在当前池子里**几乎不起作用**（池子只覆盖 5 篇笔记，跨笔记边本来
  只有 4 条）—— 它是**为扩池准备的**（全量 1181 张时约 55% 的候选对是跨笔记）。

  **只读复算**：真库副本上跑**生产函数**得到 `new_count = 151`（与探针预测一致）；
  411 条关系的 `similarity_score` 全部 ≥0.75；**真库未被写入**（仍是 260 条）。

  守卫：`backend/tests/test_graph_relation_suggestion.py`（12 条，判定逻辑此前**零测试**）。
  已验证**有牙齿** —— 跑在 `da775b2` 的 `graph_service.py` 上 2 条变红。

  > 未做（如实登记）：候选池仍只取最新 200 张（实测只落在 5/15 篇笔记，且重复
  > 点击不推进）；FTS5 召回、接进理解管道、LLM 判关系类型、样板文本闸均未开工。
  > **判准由 AI 做、每格 12–24 对、未经用户抽查**；笔记效应的差值**未达显著**
  > （区间有重叠），方向可信、数值待更大样本。局限逐条登记在报告 §9.6 / §10.4，
  > **不能当定论用**。

- **知识图谱：卡片向量改为持久化存储**（2026-09-26）。孤点 1061 张里
  **904 张（85.2%）根本进不了候选池**，而候选池被压在 200 张**是被超时逼出来的**：

  | | 实测 |
  |---|---|
  | 编码 200 张（bge-m3） | 73 秒 |
  | 编码 1181 张（bge-m3） | **275 秒** |
  | `POST /graph/suggest` 里 Celery `task.get` 的超时 | **180 秒** |

  只要向量还得现算，池子就放不开。存进库之后 1181² 余弦矩阵实测 **10 ms**。

  **改动**：

  - `app/models/knowledge_card.py`：加 `embedding_model` / `embedding_dim` /
    `embedding`(LargeBinary) / `has_embedding` + 索引 `(user_id, has_embedding)`
    —— 与 `chunk.py:152-157` 同一套列名与打包方式（AGENTS.md §3：向量与原文同库同表）
  - `app/database.py`：`_migrate_sqlite` 加 `ALTER TABLE ... ADD COLUMN`
    （**只加列、不回填** —— 回填要跑一遍嵌入模型，不该塞在启动路径里），
    先例是 `content_hash`（`:872`）
  - `app/tasks/embedding_tasks.py`：新增 `embed_cards` 任务，只处理
    `has_embedding=False` 的行，分批复用 `encode_text`
  - `app/services/graph_service.py`：候选卡**只取有向量的**、向量从库读；
    "有卡片但一条向量都没有" → **报错并说明怎么补**，而不是静默返回 0
    （静默返回 0 会让用户以为"没有相关卡片"）
  - `scripts/embed_cards.py`：补向量入口（在**本进程内**执行任务，不需要 worker 在跑）

  **端到端验证**（真库副本上跑真函数）：

  ```
  ① 迁移     副本新增 4 列 + 索引 ✓ ｜ 真库未被迁移
  ② 补向量   1181 张全部写入、0 失败、57 秒
  ③ 建议     260 → 417 条（新建 157）｜ 单次耗时 211 ms
  ④ 真库     card_relations 仍是 260，knowledge_cards 仍无向量列
  ```

  **211 ms 对比改造前的 73 秒** —— 差三个数量级，且不再撞 180 秒超时。

  ⚠️ **端到端跑出来两个真问题，都已修**（这是本批最有价值的部分）：

  1. **`embedding_model` 记的是配置值，不是实际加载的模型。**
     `EmbeddingService` 在可用内存不足时会**自动降级**
     （`embedding_service.py:66`：bge-m3 1024 维 → bge-small-zh-v1.5 512 维），
     而且**本机验证时真的触发了**（"可用内存不足（2.6GB < 4.0GB）"）。
     第一版实现写的是 `getattr(service, "model_name", None) or settings.embedding_model`，
     而 `EmbeddingService` **没有 `model_name` 属性**（只有 `loaded_model_name`），
     于是每行都写成配置值 —— **库里 512 维的向量被标成 `BAAI/bge-m3`**。
     这一列存在的意义就是"将来按模型重算"，记错等于废掉它。
  2. **同一个库里可能混着两个模型的向量**（降级按当时的可用内存决定）。
     跨模型算余弦是无意义的数字，维度不同还会让 `np.array` 变成 object 数组。
     现在按 `(embedding_model, embedding_dim)` 分组，**只取条数最多的那一组**，
     其余丢弃并告警。

  守卫：`backend/tests/test_graph_relation_suggestion.py` 12 → **19 条**，
  新增 `TestVectorSourcing`（缺向量不参与 / 全缺则报错 / BLOB 损坏按缺失处理 /
  混模型不比较）、两条 `_model_name` 用例、以及 `TestVectorColumnMigration`
  （**先造一个没有这些列的旧库**再跑真迁移 —— 用全新临时库测不出迁移，
  因为 `create_all` 会直接带着新列建表）。

  > 未做（如实登记）：**验证时的向量是降级模型的（512 维）**，所以 E2E 得到 157 条边、
  > 与离线探针预测的 151 不同 —— 向量本身就不同，**机制验证通过、具体条数不可比**。
  > 本机可用内存 2.6GB < 阈值 4.0GB，说明真实环境可能一直跑在降级模型上，
  > 这对关系判定的影响**未测**。候选池仍取最新 200 张（向量持久化只是解除了上限的
  > 理由，池子本身还没改）。也没有"模型变了就重算"的机制，现在只告警 + 丢弃少数派。

- **知识图谱：候选池改「孤点优先 + 分批推进」—— 孤点 1061 → 259**（2026-09-26）。
  这一步是"减少孤立节点"的正解：孤点 1061 张里 **904 张（85.2%）根本进不了候选池**，
  因为候选以前是"最新 200 张"，同一批卡片被反复比较，而它们**永远轮不到**。

  **改动**（`app/services/graph_service.py`，只在 `auto_suggest_relations` 内）：

  | 项 | 以前 | 现在 |
  |---|---|---|
  | 候选 | 最新 200 张 | **还没有任何关系的卡片**，取最新 200 张 |
  | 比较范围 | **只在候选池内**（方阵） | **候选 × 全量**（非对称） |
  | 推进方式 | 重复比较同一批 | 处理过就不再是孤点，**下一次点击自然接上** |

  - **为什么比较范围要非对称**：一张孤点的最近邻完全可能不在"这批候选"里。
    以前是方阵，两边只能是同一个集合 —— 那正是"904 张永远轮不到"的机制。
    新增 `_compute_similarity(rows, cols)`（矩形），`_compute_pairwise_similarity`
    保留为它的方阵特例。
  - **为什么不需要游标**：候选是"孤点"，处理完就不再是孤点，**孤点数本身就是进度**。
  - **契约未动**：函数签名、返回的 `new_count`、`GraphSuggestResponse` 一律不变。

  **只读复算**（真库副本、跑生产函数、模拟连续点击）：

  ```
  起点：关系 260 条 ｜ 孤点 1061 张
   点击 1: +213 → 孤点 908 ｜ 点击 4: +168 → 475 ｜ 点击 14: +6 → 259 ｜ 点击 15: 0（收敛）
  ```

  **孤点 1061 → 259（减少 802 张，76%）**，关系 260 → 1,312，需要 **14 次点击**。
  真库未被写入（仍是 260 条 / 1061 孤点）。

  **顺手修掉一处性能问题**：候选查询原本写成 SQL 的 `NOT EXISTS` 相关子查询，
  逐段计时 1,312 条关系时占 **239 ms**，且随关系数增长 —— 而关系**已经全量加载**
  在同一个函数里了。改成用同一个集合在 Python 里筛之后，前几次点击从
  504/544/590 ms 降到 393/361/403 ms。函数本身稳定在 **~380 ms**，不随关系数增长。

  ⚠️ **一个必须登记的代价：237 张卡片按当前规则永远连不上。**
  复算收敛在 259 而不是 0。查下去：**237 张在**全量**里都没有够格的邻居**
  （同笔记 0.75 / 跨笔记 0.85）。它们的最近邻相似度中位 **0.730**、max 0.850，
  而最近的邻居**看起来是真相关的**：

  ```
  0.850 [跨笔记] 接地刀闸操作术语 ↔ 接地刀闸操作原则
  0.849 [跨笔记] 主要引用标准     ↔ 主要引用文件
  0.846 [跨笔记] 开关操作术语     ↔ 隔离开关操作术语
  ```

  这些全是跨笔记对，卡在 0.85 那道线下面一点点 —— 也就是说把跨笔记下界提到 0.85
  的代价是 **237 张（占语料 20%）永久孤立**。精确率与覆盖率的取舍第一次被精确量化，
  **没有免费选项**，需要产品决策（兜底通道 / 只对孤点放宽 / 接受），本批**没做**。

  守卫：`backend/tests/test_graph_relation_suggestion.py` 19 → **24 条**，新增
  `TestIsolationFirstPool`：有边的卡不再被处理 / 候选上限真的分批推进（上限压到 1
  时每轮只救一张）/ 没有孤点时返回 0 / **孤点的最近邻可以在本批候选之外**
  （非对称比较）。已验证**有牙齿** —— 跑在 `8a1b50f` 的 `graph_service.py` 上 2 条变红。

  > 未做（如实登记）：收敛要 14 次点击（每次最多 600 条待审，再合并批次会让队列不可用）；
  > 候选按 `updated_at desc`（"最新的孤点先救"，没有更强的优先级依据）；
  > 验证用的是降级模型（512 维），但与 bge-m3 的差异已量（孤点 259 vs 256，结论一致）；
  > 判准仍由 AI 做、**未经用户抽查**（抽查表在 `_agenttmp/spotcheck.md`）。

- **知识图谱：跨笔记下界 0.85 → 0.80 —— 孤点 259 → 216**（2026-09-26，第四次修正）。

  上一条收敛后还有 **239 张卡片在全量里都没有够格的邻居**，而它们最近的邻居长这样：

  ```
  0.850 [跨笔记] 接地刀闸操作术语 ↔ 接地刀闸操作原则
  0.849 [跨笔记] 主要引用标准     ↔ 主要引用文件
  0.846 [跨笔记] 开关操作术语     ↔ 隔离开关操作术语
  ```

  **全是跨笔记对，卡在 0.85 下面千分之几。** 而 §9 只测过"跨笔记 `0.75-0.85`"
  **整段**（12 对，50%）—— 那一段里两头的成色可能完全不同。

  **盲判 v3（拆开重测，各 24 对，判据与 v2 完全一致）**：

  | 档 | 可导航 | Wilson 95% | 可行动 |
  |---|---|---|---|
  | **跨笔记 0.80–0.85** | **83%** | **[64%, 93%]** | 25% |
  | 跨笔记 0.75–0.80 | 58% | [39%, 76%] | 12% |
  | 同笔记 0.70–0.75 | 50% | [31%, 69%] | 4% |

  **`0.80-0.85` 与 0.85 以上同档（都是 83%）—— 整段那个 50% 是两段混出来的。**

  **改动**：`CROSS_NOTE_MIN_SIMILARITY` **0.85 → 0.80**；`MIN_SIMILARITY`（同笔记）
  保持 0.75（`0.70-0.75` 只有 50%，降它没有依据）。**契约未动。**

  **效果**（真库副本、跑生产函数、模拟连续点击）：

  | 跨笔记下界 | 收敛后孤点 | 关系总数 | 需要几次点击 |
  |---|---|---|---|
  | 0.85 | 259 | 1,312 | 14 |
  | **0.80** | **216** | **1,389** | **12** |

  **孤点 1061 → 216（减少 845 张，80%）**，真库未被写入。

  ⚠️ **但它没有解决那批"永久孤立"的卡片**：不可达数 239 → 216，只减 23。
  原因是**不可达的 239 张里 142 张的最近邻是「同笔记」**（在 `[0.70, 0.75)` 里），
  只有 97 张是跨笔记 —— 降跨笔记下界只帮得到后者，而同笔记 `0.70-0.75`
  实测只有 50%。**约 200 张卡片（占语料 17%）只可能在 ~50% 精确率下连上。**
  这不是实现问题，是这份语料的性质：它们与任何卡片都只是"同领域"，不是"同一对象"。
  三条路（兜底通道 / 接受孤立 / 换召回方式）**都需要产品决策，本批没做**。

  守卫：`backend/tests/test_graph_relation_suggestion.py` 仍 24 条，但
  `test_cross_note_pair_needs_the_higher_floor` 的相似度换成 0.777（落在新下界之下）、
  `test_cross_note_high_similarity_is_kept` 换成 0.809（新下界之上）。
  已验证**有牙齿** —— 跑在 `47d05e1`（下界 0.85）上 1 条变红。

  > 未做（如实登记）：v3 每档 24 对，`0.80-0.85` 的 `[64%, 93%]` 与 `0.75-0.80` 的
  > `[39%, 76%]` **区间有重叠**（64–76）—— 方向可信，但"两段确实不同"未达显著。
  > 判准仍由 AI 做、**未经用户抽查**（现有 v2 的 12 项与 v3 的 72 项两批待抽查）。

- **知识图谱：同名闸（标题相同的卡片不建「相关」边）+ 测量脚本分开报两个目标**
  （2026-09-26，第 4 步）。

  ## 量出来的结果改变了这一步的做法

  原计划是做"样板文本闸"（按**标题在全库的频次**挡）。先量了一遍，那个方向是错的：

  ```
  标题出现 >=3 次：5 种，涉及 22 张卡（1.9%）   ← 按频次挡只能覆盖 1.9%
  ```

  而真正值得看的是**边**：全量收敛后 **49 条边（3.5%）**连接的是**标题完全相同**的
  卡片，相似度**普遍 ~1.000**；其中盲判过的 3 条**全部判为 `dup`（真重复）**。

  **所以那不是"样板噪音"，是重复卡片** —— 同一份内容被抽了两遍。把重复当"相关"
  存下来，等于把"重新理解导致卡片翻倍"这个数据问题藏进图谱里。
  → 闸门建在**标题相同**上，而不是"标题频次"。

  ## 改动

  - `app/services/graph_service.py`：目标池查询补 `title`；新增 `title_of`
    （**只做 `strip`**，不做更激进的归一）；标题相同的对不建边并计入
    `same_title_skipped`，结束时 `logger.info` 打出跳过对数
  - `backend/scripts/measure_relation_quality.py`：`precision_report` 改成
    **两列** —— 「可导航」（四类任意）与「可行动」（`dup`/`opp`/`pre`）。
    标注支持 `{"A:0": {"label": true, "type": "kin"}}` 新格式；旧格式仍可跑，
    只报可导航并给出提示

  **为什么同名只做 `strip`**：更激进的归一（去标点、去"定义"这类后缀）会把
  `操作术语`（总表）与 `操作术语定义`（定义）也判成同名，而它们**内容不同**。
  宁可漏挡，不可误挡 —— 有守卫 `test_near_same_title_is_still_linked`。

  ## 效果与**代价**（真库副本、跑生产函数）

  | | 关系总数 | 收敛后孤点 | 需要几次点击 |
  |---|---|---|---|
  | 上一版（0.80 下界，无闸） | 1,389 | 216 | 12 |
  | **加同名闸** | **1,362**（−27） | **240（+24）** | 14 |

  ⚠️ **孤点反而多了 24 张**：那 24 张卡的**唯一够格邻居就是它的同名孪生**，
  闸门一挡就没有边了。**这是真实的取舍，不是 bug** —— 换来的是 38 对同名卡片
  不再被当成「相关」建议推给用户。

  ⚠️ **真正的修法是去重，不是建边。** 那 24 张孤点与 38 对同名卡片是同一个问题的
  两种表现：语料里有 **42 种 / 96 张**标题重复的卡片，它们该被**合并**。
  合并之后那 24 张不再是"孤立的卡"（它们不该作为独立节点存在）。
  **本批没做去重** —— 它要动卡片本身的存续，属于"删数据/改产品语义"，需要单独批准。
  现状的诚实描述：同名闸把重复问题从"藏在图谱里"挪到了"看得见的 24 张孤点 + 一条日志"，
  **它没有解决问题，只是不再掩盖它**。

  守卫：`backend/tests/test_graph_relation_suggestion.py` 24 → **27 条**，新增
  `TestSameTitleGate`：同名对不建边但两张卡仍能与别的卡建边 / 近似标题不受影响 /
  只差空白的同名也挡住。已验证**有牙齿** —— 跑在 `25695b1` 上 1 条变红。

  ## 门禁

  `python -m pytest -q` **1226 passed / 30 skipped**（+3）· `ruff check app tests scripts`
  退出 0 · `check_dependency_drift.py` 退出 0（9 条漂移为既有事实）

  > 未做（如实登记）：去重/合并（见上，需单独批准）；关系类型仍全是 `related`
  > （`RelationType` 的 `prerequisite`/`subsequent`/`contrast` 在真实数据里零使用，
  > 而盲判显示约 20% 的真关系有明确类型 —— 落它需要 LLM 参与，是独立一批）；
  > 判准仍由 AI 做、**未经用户抽查**（v2 的 12 项 + v3 的 72 项）。

### Security

- 本节**刻意不宣称"本批次修复了任何安全发现"**（同类声明最容易变成"文档比现实宽松"）。
  当前口径：依赖扫描在 CI 里**仍是建议性**的（`.github/workflows/ci.yml:340-362`），
  升级为阻断的前置条件见 `docs/security-scan.md:520-532`；
  已知未处理项（`python-jose` 的 CVE、镜像未加非 root `USER` / `HEALTHCHECK`、
  访问令牌无状态、限流无锁定）逐条列在 `SECURITY.md`。
- **一条如实记录的副作用**（不是"我们修了漏洞"）：上面前端依赖升级之后，
  `npm audit --registry=https://registry.npmjs.org` 的告警数从 **11 条降到 2 条**。
  消掉的是随版本一起更新的 `vite`（optimized deps `.map` 路径穿越）、
  `vitest`（**critical**：UI server 任意文件读取与执行）、`react-router` /
  `react-router-dom`（open redirect，**本轮唯一进生产包的那条**），
  以及 `esbuild` / `postcss` / `nanoid`。
  这是"版本对齐"的附带结果，**不是**一次针对性的漏洞修复；
  仍余 2 条工具链传递依赖告警（`browserslist`、`baseline-browser-mapping`）。
  本节不改口径：扫描仍然不阻断 CI。

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
  目前两者都是 `0.1.0`；`v0.1.0` **有 annotated tag**（指向 `154060c`），
  但 **GitHub Releases 页面尚未创建**（tag ≠ release）。
  两处版本号的一致性由守卫测试保证（`backend/tests/test_version_single_source.py`）；
  **"页面上的 release 与 tag 同步"仍未机制化** —— 这一条是已知缺口，不是机制。
- 破坏性变更会写进本文件并在 `UPGRADING.md` 里说明升级路径。

[Unreleased]: https://github.com/trx-0833/EngramNote/commits/main
[0.1.0]: https://github.com/trx-0833/EngramNote/commits/main
