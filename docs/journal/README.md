# 过程记录（journal）索引

> **状态：活文档** ｜ 最后核对：2026-09-23 ｜ 权威性：本目录（journal）的索引与分类依据（被索引的文件本身是过程记录）

本目录存放 EngramNote 的**过程记录与分析报告**：某一轮工作做完之后写下的核查、
比对、实验结果与结论。它们的价值在**追溯**（"当时看到了什么、依据是什么、
怎么判定的"），**不是现状依据** —— 报告里的数字、行号与"未修/已修"结论
都会随代码继续演化而漂移。

判据很简单（三种状态的完整定义见 [`../README.md`](../README.md) §0）：

- **"现在是什么样"** → `docs/` 顶层的**活文档**（`overhaul-plan.md` 台账、
  `sqlite-single-writer.md`、`open-source-readiness.md` …）；
- **"以前是什么样"** → **历史快照**（`architecture.md`、`decisions.md`、`archive/`）；
- **"某一轮当时测到了什么"** → 本目录（**过程记录**；`security-scan.md` 同属这一类，
  只是因为被脚本引用而留在顶层）。

> 引用本目录的内容前，先与**当前代码**核对：文档与代码冲突时以代码实测为准
> （见仓库根 `AGENTS.md` §1）。本目录的文件**不再更新**，也不作为门禁依据。

## 索引

| 文件 | 内容 | 记录时间 | 原路径 |
|---|---|---|---|
| [code-review-report.md](code-review-report.md) | 全项目代码审查报告（静态审查 + 文档三方对照，235 行） | 2026-08-30 | `docs/code-review-report.md` |
| [verification-report-20260830.md](verification-report-20260830.md) | 启动与功能验证报告（L0 环境自检 → L5 前端冒烟） | 2026-08-30 | `docs/verification-report-20260830.md` |
| [tooling-report.md](tooling-report.md) | 工具链首轮检查报告（ruff 146 / eslint 81 / prettier 39 = 266 个问题） | 2025-06 | `docs/tooling-report.md` |

三份文件头部都已自带 `状态：过程记录 ｜ 权威性：不是现状依据` 的横幅 ——
本目录只是把它们从"活文档"堆里分出来，内容一个字都没有改。

## 迁移说明（2026-09-23）

### 移入了什么

三份**分析/过程类**文档从 `docs/` 顶层移入本目录，路径变化如下：

| 旧路径 | 新路径 |
|---|---|
| `docs/code-review-report.md` | `docs/journal/code-review-report.md` |
| `docs/verification-report-20260830.md` | `docs/journal/verification-report-20260830.md` |
| `docs/tooling-report.md` | `docs/journal/tooling-report.md` |

判据：这三份都是**一次性结论的快照**（"某日审查发现 N 个问题"），
与 `docs/` 顶层那些会被持续修正的活文档混在一起时，
读者（和引用它们的文档）很容易把过期结论当成现状 ——
`code-review-report.md` 里"Chroma + SM-2 + RAG 三路检索"的描述即是实例。

### 刻意**没有**移入的

- `docs/security-scan.md`：**代码与注释在引用它** ——
  `backend/scripts/security_scan.py:65,200,666,671`、
  `backend/scripts/check_dependency_drift.py:58,587`。
  移动它会在脚本的说明与输出里留下死链，属另一件事。
- `docs/overhaul-plan.md`：它是**台账**（逐轮追加、持续更新），不是过程快照。

### 遗留的旧路径引用（本次未改，需责任人跟进）

移动之后，下面几处仍写着旧路径（`docs/<名字>.md`）。
它们**不在本次改动的写权限范围内**，此处登记以免"移完就以为没有残留"：

| 引用处 | 引用内容 |
|---|---|
| `docs/overhaul-plan.md:7252` | `docs/code-review-report.md`（25KB）→ 应为 `docs/journal/…` |
| `docs/overhaul-plan.md:10713` | `docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24` |
| `backend/scripts/dev/README.md:28` | `docs/code-review-report.md`、`docs/verification-report-20260830.md:24` |
| `backend/scripts/dev/README.md:88` | `docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24` |

> 行号是 **2026-09-23 迁移当时**核对的值，会随文件继续演进漂移 ——
> 找的时候以"文件名"为准，不要只认行号。
> 已经同步过的：根 `README.md`（全篇 grep 零命中，本就不引用）、
> `docs/README.md`（文档地图 §④ 与 §2.2 已指向 `journal/`）、
> `docs/open-source-readiness.md:676`（已改为 `docs/journal/tooling-report.md`）。

## 约定

- 新的**过程记录/审查报告/实验结论**请直接写进本目录，不要放 `docs/` 顶层；
- 本目录文件原则上**不更新**；若要引用其中的结论，先与当前代码核对，
  并以活文档/代码为准；
- 移动/改名本目录的文件时，同步更新上面的索引与"遗留的旧路径引用"表。
