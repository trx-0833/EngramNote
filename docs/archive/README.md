# 归档文档索引

本目录存放 EngramNote 历史文档。它们是开发过程中逐步累积的产物（其中多份由 AI 生成），**内容可能与当前代码不一致，仅供追溯，不要以它们为准**。

项目当前唯一"活"的架构文档为 [`../architecture.md`](../architecture.md)。

## 索引

| 文件 | 内容 | 归档原因 | 替代信息 |
|---|---|---|---|
| 项目架构.md | AI 根据早期对话整理的架构设计要求（660 行） | 与当前实现脱节，属"原始需求"而非"现状描述" | docs/architecture.md §1-§5 |
| 项目图解.md | Mermaid 图解式全貌（架构/流程/数据模型/Vault 目录） | 内容已过时，且与 architecture.md 重复 | docs/architecture.md 各节 |
| 开发时间表.md | V1.0 十二周开发记录（78KB） | 过程文档，已完成使命 | git log（开发历史） |
| 新手教学.md | 面向初学者的代码讲解（171KB） | 教程类材料，与代码维护解耦 | 需要时按章节查阅，但不作为架构依据 |
| LLM服务详解.md | LLM 调用零基础讲解 | 教程类材料 | backend/app/services/llm_service.py |
| 存储结构与数据库设计.md | 存储与数据库设计说明 | 数据库迁移体系即将重构（见 architecture.md §9），届时以新的 docs/database.md 为准 | docs/architecture.md §6-§7 |
| GitHub上传教程.md | 上传项目到 GitHub 的教程 | 教程类材料，与代码维护无关 | — |

## 约定

- 新文档一律放入 `docs/` 根（活文档），不要放回根目录。
- 本目录文件不更新；如需引用其中设计，先与当前代码核对，再以新文档为准。