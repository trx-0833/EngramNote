# 过程记录（journal）索引

> **状态：活文档** ｜ 最后核对：2026-09-24 ｜ 权威性：本目录（journal）的索引与分类依据（被索引的文件本身是过程记录）

本目录存放 EngramNote 的**过程记录与分析报告**：某一轮工作做完之后写下的核查、
比对、实验结果与结论。它们的价值在**追溯**（"当时看到了什么、依据是什么、
怎么判定的"），**不是现状依据** —— 报告里的数字、行号与"未修/已修"结论
都会随代码继续演化而漂移。

判据很简单（三种状态的完整定义见 [`../README.md`](../README.md) §0）。
**2026-09-24 按状态重新核对了一遍**，括号里引用的是对应文件**横幅里的原文**：

- **"现在是什么样"** → `docs/` 顶层的**活文档**：
  `overhaul-plan.md`（台账；**无横幅**，自述性质，登记见 `../README.md` §4）、
  `sqlite-single-writer.md`（运维约束；**无横幅**，本轮禁改，登记见 `../README.md` §4）、
  `open-source-readiness.md`（横幅 = `状态：活文档`），外加两份索引
  `../README.md` 与本文件（横幅同为 `状态：活文档`）；
- **"以前是什么样"** → **历史快照**（横幅**均为** `状态：历史快照`）：
  `architecture.md`、`decisions.md`、`archive/`（7 份 + 索引，逐份同横幅）；
- **"某一轮当时测到了什么"** → 本目录（**过程记录**：`code-review-report.md`、
  `verification-report-20260830.md`、`tooling-report.md` 三份横幅均为 `状态：过程记录`）。
  `security-scan.md` 同属这一类（横幅也是 `状态：过程记录`），
  只是因为被脚本引用而留在顶层。

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

### 遗留的旧路径引用（2026-09-24 全部收口 ✅）

移动之后曾有 4 处仍写着旧路径（`docs/<名字>.md`）。它们当时**不在那一批改动的写权限范围内**，
所以只做了登记；**2026-09-24 由另一批收口**（这次权限覆盖到了那两个文件）：

| 引用处 | 引用内容 | 现状 |
|---|---|---|
| `docs/overhaul-plan.md:7252` | 文档处置表里的 `docs/code-review-report.md`（25KB） | ✅ 已改为 `docs/journal/code-review-report.md` |
| `docs/overhaul-plan.md:10713` | `docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24` | ✅ 两处均已改为 `docs/journal/…` |
| `backend/scripts/dev/README.md:35`（登记时写的是 `:28`） | `docs/code-review-report.md`、`docs/verification-report-20260830.md:24` | ✅ 已改为 `docs/journal/…` |
| `backend/scripts/dev/README.md:212`（登记时写的是 `:88`） | `docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24` | ✅ 已改为 `docs/journal/…` |

> ⚠️ **登记时写的行号本身就是漂移的**：上表**最后两行** `backend/scripts/dev/README.md` 的引用
> 实际在 `:35` 与 `:212`，而 2026-09-23 登记的是 `:28` 与 `:88`（差 7 行与 124 行）——
> 同一个提交 `3c631f9` 里，那份 README 正在被追加"第二批清单"等章节，
> 登记读的是**追加前**的版本。这是"以文件名/小节为准，不要只认行号"的现成实证。

> 已经同步过的：根 `README.md`（全篇 grep 零命中，本就不引用）、
> `docs/README.md`（文档地图 §④ 与 §2.2 已指向 `journal/`）、
> `docs/open-source-readiness.md`（§4.7 指向 `docs/journal/tooling-report.md`，现为 `:731`）。

### 行号引用漂移登记（2026-09-24 改写 `open-source-readiness.md` 的连带后果）

2026-09-24 的记账把 `docs/open-source-readiness.md` 从 **789 行改到 844 行**，
于是仓库里其它文件按 `open-source-readiness.md:<行号>` 写的引用**整体下移**。
本次改动本身能覆盖的引用已改成"**小节号 + 行号**"；下面这些**不在本次写权限内**，
仍写着旧行号，留给下一批收口：

| 文件 | 旧行号 | 现在应在 | 偏移 |
|---|---|---|---|
| `.editorconfig:10` | `:263-266` | `:291-294` | +28 |
| `docker-compose.yml:37` | `:577` | `:624` | +47 |
| `CHANGELOG.md:29` | `:579-580` | `:626-627` | +47 |
| `backend/.dockerignore:8` | `:579-580` | `:626-627` | +47 |
| `backend/.dockerignore:11` | `:580` | `:627` | +47 |
| `CODEOWNERS:12` | `:602` | `:657` | +55 |
| `CONTRIBUTING.md:277` | `:729` | `:784` | +55 |

> 这几处的旧行号在改写前是**对得上**的（逐条用 `git show HEAD:…` 对过），所以漂移完全是
> 这次改写造成的。建议下一批顺手改成**小节号**（例如"`docs/open-source-readiness.md` §2.8"），
> 或者接受"活文档里的行号必然继续漂"这件事，只认小节与关键词。

## 约定

- 新的**过程记录/审查报告/实验结论**请直接写进本目录，不要放 `docs/` 顶层；
- 本目录文件原则上**不更新**；若要引用其中的结论，先与当前代码核对，
  并以活文档/代码为准；
- 移动/改名本目录的文件时，同步更新上面的索引、"遗留的旧路径引用"表与"行号引用漂移登记"；
- 引用**别的文件**时优先写"**文件 + 小节号**"，行号只当定位线索 ——
  2026-09-23/24 两轮里，登记过的行号全都漂过（本文件的"漂移登记"一节就是为此存在）。
