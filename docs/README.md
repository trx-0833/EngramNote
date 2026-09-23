# EngramNote 文档地图（docs/）

> **状态：活文档** ｜ 最后核对：2026-09-23 ｜ 权威性：本目录的阅读顺序与状态判据

`docs/` 里同时住着**活文档**、**历史快照**与**过程记录**。这份地图回答一个问题：**你现在该读哪一份。**

---

## 0. 本目录的约定（先读这 6 行）

**每份文档的标题下方都有一行状态横幅**（markdown 引用块），只有三种状态：

| 状态 | 横幅格式 | 含义 |
|---|---|---|
| 活文档 | `> **状态：活文档** ｜ 最后核对：<日期> ｜ 权威性：本主题的现状依据` | 跟着代码走；可以作为"现状"被引用 |
| 历史快照 | `> **状态：历史快照** ｜ 冻结于 <日期> ｜ 权威性：仅供参考，现状以 <活文档路径> 为准` | 已冻结；只回答"以前长什么样" |
| 过程记录 | `> **状态：过程记录** ｜ 记录时间：<日期> ｜ 权威性：不是现状依据` | 一次运行 / 一轮改动的留档 |

**权威性判据（冲突时按这个顺序裁决）**：

1. **代码实测 > 任何文档**（`AGENTS.md` §1：文档与代码冲突时以代码实测为准）；
2. 文档之间冲突 → 以**最新的计划文档**为准。当前是 [`overhaul-plan.md`](./overhaul-plan.md)：它是重构期间**唯一执行依据**；
3. `历史快照` 与 `过程记录` **都不作为现状依据** —— 它们只能回答"以前为什么那样设计"、"当时测到了什么"。

新增文档**必须**带状态横幅（模板见 §5）。

---

## 1. 按顺序读（访客路线图）

### ① 想把它用起来 → 仓库根 [`README.md`](../README.md) 的「快速开始」

**不需要读 `docs/`。** 安装、环境检测、三个进程的启动方式（后端 8001 / 前端 5173）都在根 README 里。

### ② 想知道系统**现在**怎么工作 → 活文档 + 台账

- ⚠️ **[`architecture.md`](./architecture.md) 是"重构前快照"，不是现状。** 它写作于 2025-06，描述的是大改造**之前**的结构，
  文件头自述"**本文不再是'活'的架构文档**"——其中的 Chroma、SM-2、`n-gram` 通道、手写 `client.ts` 等都已经不存在了。
  **要知道现在的结构，读 [`overhaul-plan.md`](./overhaul-plan.md)**（第四部分·目标架构，加各阶段附录的逐轮记录），并**以代码实测为准**。
- 运行事实与硬约束：仓库根 `AGENTS.md`、[`sqlite-single-writer.md`](./sqlite-single-writer.md)。
- 别把 [`archive/`](./archive/) 当现状：那是重构前的设计文档与教程，**仅供追溯**。

### ③ 想知道**为什么**这样设计 → 历史原因与决策

- [`decisions.md`](./decisions.md)：F-01~F-35 的**历史原因**（只读归档）。代码注释里的 `见 docs/decisions.md#F-xx` 指的就是它 ——
  说明的是"以前为什么那样"，不是"现在为什么这样"。
- **现在的决策**记在 [`overhaul-plan.md`](./overhaul-plan.md) 的**附录**（J→BO 编号），**不再新增 F-xx**。
- 有些"踩过的坑"至今仍然成立（F-20 的可重试状态码、F-30 的 Celery retry 语义、F-33 的 JSON 截断）——查这些时读 `decisions.md` 是对的。

### ④ 过程记录 → [`journal/`](./journal/)（**2026-09-23 已建立**）

- [`journal/README.md`](./journal/README.md) 是本目录的索引，并登记了**迁移后仍写旧路径的引用处**。
- 已迁入三份：`journal/` 下的 `code-review-report.md`（2026-08-30 代码审查）、
  `verification-report-20260830.md`（启动与功能验证）、`tooling-report.md`（2025-06 首轮工具链检查）。
- 还留在 `docs/` 顶层的**过程记录**：[`security-scan.md`](./security-scan.md) ——
  **刻意不移动**，因为 `backend/scripts/security_scan.py:65,200,666,671` 与
  `backend/scripts/check_dependency_drift.py:58,587` 在引用它，移动会在脚本输出里留下死链。
- 这些**都不是现状依据**：要现状，就重跑对应脚本（`backend/scripts/security_scan.py` 等）或读活文档。

### ⑤ 前端专题 → [`../frontend/docs/`](../frontend/docs/)

- 活文档：[`css-convention.md`](../frontend/docs/css-convention.md)（CSS 分层规范）、[`e2e.md`](../frontend/docs/e2e.md)（E2E 覆盖边界与跑法）；
- 计划（未落地）：[`query-and-state-plan.md`](../frontend/docs/query-and-state-plan.md)（5.2 / 5.3 待批）；
- 进度与逐轮记录：[`css-migration-plan.md`](../frontend/docs/css-migration-plan.md)、
  [`openapi-client.md`](../frontend/docs/openapi-client.md)、[`a11y-audit.md`](../frontend/docs/a11y-audit.md)、
  [`migration-evidence/`](../frontend/docs/migration-evidence/)。

---

## 2. `docs/` 全量清单

### 2.1 `docs/` 顶层

| 路径 | 状态 | 一句话内容 | 是否权威 |
|---|---|---|---|
| [README.md](./README.md) | 活文档 | **本文档地图**：三种状态、阅读顺序、全量清单 | ✅ 本目录的阅读顺序与状态判据 |
| [overhaul-plan.md](./overhaul-plan.md) | 活文档（台账） | 重构全量方案 + J→BO 逐轮执行记录（11,709 行）；自述"重构期间唯一执行依据" | ✅ 是（重构期间的唯一执行依据） |
| [sqlite-single-writer.md](./sqlite-single-writer.md) | 活文档（约束） | SQLite 单写者路线下的硬约束（启动期强制单写者）+ D1~D6 决策落地说明 | ✅ 是（本主题；D5 行已就地标注"Chroma 已移除"） |
| [open-source-readiness.md](./open-source-readiness.md) | 活文档 | 开源化差距核查（逐条带 `文件:行号`）+ §执行进度 台账 | ✅ 是（开源化主题） |
| [architecture.md](./architecture.md) | 历史快照（2026-09-11 冻结） | 系统架构、数据流、状态机、数据库概览 —— **重构前**（2025-06） | ❌ 否（现状见 overhaul-plan 与代码） |
| [decisions.md](./decisions.md) | 历史快照（2026-09-11 冻结） | F-01~F-35 决策记录："以前为什么那样写" | ❌ 否（但历史原因与踩坑仍有效） |
| [security-scan.md](./security-scan.md) | 过程记录（2026-09-14） | 一次真实依赖/镜像扫描（pip-audit + npm audit + Trivy）的结果与处置口径 | ❌ 否（要最新结果需重跑脚本） |
| [archive/](./archive/) | 历史快照（7 份 + [索引](./archive/README.md)） | 重构前的设计文档与教程（约 390KB，逐份带横幅） | ❌ 否 |

> `docs/*.txt`（17 份）是 ruff / eslint / prettier 的**原始输出**，不是 markdown，不适用横幅约定（见 §4）。

### 2.2 `docs/journal/`（过程记录，2026-09-23 建立）

| 路径 | 状态 | 一句话内容 | 是否权威 |
|---|---|---|---|
| [journal/README.md](./journal/README.md) | 活文档 | journal 索引 + 迁移说明 + **遗留的旧路径引用登记** | ✅ 是（journal 的索引与迁移口径） |
| [journal/code-review-report.md](./journal/code-review-report.md) | 过程记录（2026-08-30） | 全项目代码审查（静态审查 + 文档三方对照） | ❌ 否 |
| [journal/verification-report-20260830.md](./journal/verification-report-20260830.md) | 过程记录（2026-08-30） | 启动与功能验证 L0~L5 的实测报告 | ❌ 否 |
| [journal/tooling-report.md](./journal/tooling-report.md) | 过程记录（2025-06） | 首轮 ruff / eslint / prettier 检查报告（266 个问题） | ❌ 否 |

---

## 3. `frontend/docs/` 全量清单

| 路径 | 状态 | 一句话内容 | 是否权威 |
|---|---|---|---|
| [css-convention.md](../frontend/docs/css-convention.md) | 活文档 | CSS 三层结构（令牌 / 全局 / 模块）与"必须留全局"的判据 | ✅ 是（CSS 约定的现状依据） |
| [e2e.md](../frontend/docs/e2e.md) | 活文档 | Playwright E2E 的覆盖边界与运行方式（含 `e2e:full` 全链路） | ✅ 是（E2E 主题） |
| [css-migration-plan.md](../frontend/docs/css-migration-plan.md) | 活文档 | 5.6 CSS 迁移的进度与剩余计划（14 个样式表逐条判定） | ✅ 是（5.6 进度） |
| [query-and-state-plan.md](../frontend/docs/query-and-state-plan.md) | 活文档（**计划，未落地**） | 5.2 TanStack Query / 5.3 Zustand 的待批迁移计划 | ❌ 否（自述"待批，未动代码"） |
| [openapi-client.md](../frontend/docs/openapi-client.md) | 过程记录（2026-09-14） | 5.1 契约生成与漂移检查的逐轮记录（S1→S3b） | ❌ 否（契约现状以 `backend/openapi.json` 与 `src/api/generated/` 为准） |
| [a11y-audit.md](../frontend/docs/a11y-audit.md) | 过程记录（自述；本轮未加横幅） | axe-core + 真 Chromium 的可访问性审计逐轮记录 | ❌ 否 |
| [migration-evidence/](../frontend/docs/migration-evidence/) | 过程记录（**脚本生成**，15 份） | 5.6 各批迁移前/后的实测证据 | ❌ 否（证据留档） |

<details>
<summary><code>migration-evidence/</code> 15 份逐份清单（点开）</summary>

| 文件 | 内容 |
|---|---|
| 5.6-01-before-learning-css.md | 试点：`learning.css` 中属于答题卡片切片的规则（迁移前） |
| 5.6-01-before-responsive-css.md | 试点：`responsive.css` 中 `.self-rating-btn` 的规则（迁移前） |
| 5.6-02-rule-diff.md | 规则清单差集：迁移前（git 源码）vs 迁移后（dist 产物） |
| 5.6-03-built-css.md | 产物 CSS 校验：动画绑定 / 冲突归因 / 级联次序 / 切片与退休类名 |
| 5.6-04-before-batch1.md | 第一批（auth / cleaning / diff / dashboard 私有部分）迁移前规则清单 |
| 5.6-05-before-batch2.md | 第二批（markdown-extras 的 `.ask-ai-*` 与 `.selection-menu`）迁移前规则清单 |
| 5.6-06-before-batch3.md | 第三批（learning 按归属拆分 + dashboard 余下 → StatCard）迁移前规则清单 |
| 5.6-07-markdown-adhd-stayed-global.md | 第三批：`.adhd-*` 留全局的决定（该"停"已由 5.6-14 收掉） |
| 5.6-08-before-batch4.md | 第四批（序 5 assessment 拆分 + refinements 冲突胜者）迁移前规则清单 |
| 5.6-09-conflict-resolution.md | 序 5：assessment × refinements 的 16 条属性冲突 —— 真 Chromium 实测裁决 |
| 5.6-10-before-batches-8-9-10.md | 序 8 / 9 / 10（components 挂点 → Sidebar/App 骨架 → 图谱）迁移前规则清单 |
| 5.6-11-before-batches-12-13-and-empty-patch-layers.md | 序 12 / 13（两张补丁层清空）规则清单 + 逐条去向审计 |
| 5.6-12-rendered-unchanged-probe.md | 序 8~13：真 Chromium computed style 对账（迁移前 HEAD vs 迁移后工作区） |
| 5.6-13-dead-css-cleanup.md | 收尾轮：死 CSS 清理（4 组 21 个条目，逐条证据 + 三向自检） |
| 5.6-14-adhd-moved-to-module.md | 序 6 收尾：5 条 `.adhd-*` 规则进 `src/hooks/useAdhdReader.module.css` |

</details>

---

## 4. 没有统一横幅的文件（与原因）

| 文件 | 为什么可以没有 |
|---|---|
| [overhaul-plan.md](./overhaul-plan.md) | 本轮**禁改**（另有人正在改）；且它自述性质、不是访客入口 |
| [sqlite-single-writer.md](./sqlite-single-writer.md) | 本轮**禁改**（另有人正在改） |
| [../frontend/docs/a11y-audit.md](../frontend/docs/a11y-audit.md) | 本轮**禁改**（另有人正在改）；它开篇自述"已经跑起来的工具与它当场报出的东西"＝过程记录 |
| `frontend/docs/migration-evidence/*.md`（15 份） | **脚本生成**（`frontend/scripts/gen-migration-evidence.mjs`），文件头写明"请勿手改"，手改会被下次生成覆盖 |
| `docs/*.txt`（17 份） | 不是 markdown（工具原始输出），不适用横幅约定 |

---

## 5. 维护约定（改文档时照着做）

1. **新增文档**：放在 `docs/` 根（活文档）或对应专题目录，并**在标题下方加状态横幅**；同时把本文件 §2 / §3 的表格补一行。
2. **状态变更**（快照被冻结、计划落地、报告过期）：改横幅那一行即可，不要改正文的历史描述。
3. **移动文件**：先改本文件的路径，再改文件里的相对链接 —— 本仓库里 `docs/**` 被代码注释、CI 说明与其它文档按 `文件:行号` 引用过，
   移动或插入行都会让引用偏移（已知：`docs/architecture.md` 加横幅后，`overhaul-plan.md` 里的 `architecture.md:NN` 引用整体偏移约 22 行）。
4. **`README.md`（仓库根）的文档清单**应以本文件为准，但**本文件不自动同步根 README** —— 改完请顺手核对。
