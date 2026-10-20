# EngramNote documentation map (`docs/`) — English entry

> **Status: living document** ｜ Last verified: 2026-10-19 ｜ Authority: this file is the
> English **entry point** to `docs/`; the authoritative map is the Chinese
> [`README.md`](./README.md)

## Read this first: the documentation is Chinese

**Every document in `docs/`, `frontend/docs/` and `docs/archive/` is written in
Chinese, and so are the code comments.** This file exists so that an English
reader knows *what is where* and *which document to trust* — it is a map, not a
translation.

If you need the actual content of a specific document, translation is currently
the practical route. The one exception is the code itself: every non-obvious
decision is commented at the point where it matters, and those comments
consistently record **why** a design was chosen and which alternatives were
rejected.

| Document | Language | English alternative |
|---|---|---|
| [`../README.md`](../README.md) | Chinese | **[`../README.en.md`](../README.en.md)** — install, run, stack, i18n architecture |
| [`README.md`](./README.md) (this directory's map) | Chinese | **this file** |
| `architecture.md`, `decisions.md`, `overhaul-plan.md`, … | Chinese | none — see the table below for what each contains |
| `../frontend/docs/*` | Chinese | none |

---

## The one convention you need to understand

Every document in `docs/` carries a **status banner** directly under its title
(a Markdown block quote). There are exactly three states, and they decide
whether you may cite the document as "how things are":

| State | Banner shape | Meaning |
|---|---|---|
| **Living** (活文档) | `> **状态：活文档** ｜ 最后核对：<date> ｜ 权威性：…` | Tracks the code. **May** be cited as current. |
| **Historical snapshot** (历史快照) | `> **状态：历史快照** ｜ 冻结于 <date> ｜ …` | Frozen. Only answers "what did it look like before". |
| **Process record** (过程记录) | `> **状态：过程记录** ｜ 记录时间：<date> ｜ …` | A one-off run / round of work. **Not** a source of truth. |

### Precedence when documents disagree

1. **Code measured behaviour beats every document.** (`AGENTS.md` §1: when a
   document and the code disagree, the code wins — and the document gets fixed.)
2. Between documents, the **newest plan document** wins. Currently that is
   [`overhaul-plan.md`](./overhaul-plan.md), which describes itself as the single
   execution authority for the refactor.
3. Historical snapshots and process records are **never** a source of truth.
   They only answer "why was it designed that way then" and "what was measured".

---

## What each top-level document contains

| Path | State | In one line | Authoritative? |
|---|---|---|---|
| [`README.md`](./README.md) | Living | **The document map**: three states, reading order, full inventory (Chinese) | ✅ for this directory's reading order |
| [`overhaul-plan.md`](./overhaul-plan.md) | Living (ledger) | The full refactor plan + round-by-round execution log (~7,700 lines) | ✅ during the refactor |
| [`pipeline-mechanism-plan.md`](./pipeline-mechanism-plan.md) | Living (decided) | **Processing-pipeline mechanism redesign**: code-level evidence + phase matrix + failure grading / idempotency checkpoints / progress cancellation / cost queue | ✅ for the task-mechanism topic |
| [`sqlite-single-writer.md`](./sqlite-single-writer.md) | Living (constraint) | Hard constraints of the SQLite single-writer route | ✅ for that topic |
| [`open-source-readiness.md`](./open-source-readiness.md) | Living | Open-source readiness gap audit, every item with `file:line` evidence | ✅ for that topic |
| [`architecture.md`](./architecture.md) | Historical snapshot | System architecture, data flow, state machines — **pre-refactor** | ❌ (read `overhaul-plan.md` + the code) |
| [`decisions.md`](./decisions.md) | Historical snapshot | Decision records F-01…F-35: "why it was written that way before" | ❌ (but the pitfalls are still valid) |
| [`security-scan.md`](./security-scan.md) | Process record | One real dependency/image scan and how findings were handled | ❌ (re-run the script for current results) |
| [`archive/`](./archive/) | Historical snapshot | Pre-refactor design docs and tutorials (~390 KB) | ❌ |
| [`journal/`](./journal/) | Process records | Code review, startup verification, tooling checks | ❌ |

> `docs/*.txt` (17 files) are **raw ruff / eslint / prettier output**, not
> Markdown, so the banner convention does not apply to them.

---

## Reading order for a visitor

### ① I want to run it

Read the repository root **[`README.en.md`](../README.en.md)** (English) or
[`README.md`](../README.md) (Chinese). You do **not** need `docs/` — install,
environment check and the three processes (backend 8001 / frontend 5173) are all
in the root README.

### ② I want to know how the system works **now**

- ⚠️ **[`architecture.md`](./architecture.md) is a pre-refactor snapshot, not
  current.** It was written in 2025-06 and its own header says it is no longer a
  living architecture document — the Chroma, SM-2, n-gram channel and hand-written
  `client.ts` it describes no longer exist.
- For the current structure, read [`overhaul-plan.md`](./overhaul-plan.md) (part 4
  "target architecture" plus the per-phase appendices) and **trust the code**.
- Runtime facts and hard constraints: root `AGENTS.md`,
  [`sqlite-single-writer.md`](./sqlite-single-writer.md).

### ③ I want to know **why** it is designed this way

- [`decisions.md`](./decisions.md): the historical reasons (F-01…F-35, read-only
  archive). A code comment saying `见 docs/decisions.md#F-xx` points here.
- **Current** decisions live in the appendices of
  [`overhaul-plan.md`](./overhaul-plan.md) (numbers J→BO); no new F-xx are added.
- Some recorded pitfalls are still true today (F-20 retryable status codes, F-30
  Celery retry semantics, F-33 JSON truncation) — reading `decisions.md` for those
  is correct.

### ④ Process records

[`journal/`](./journal/) — code review (2026-08-30), startup and functional
verification, first tooling pass. These are **not** sources of truth: to get the
current state, re-run the corresponding script.

### ⑤ Frontend topics

[`../frontend/docs/`](../frontend/docs/) — CSS layering conventions, E2E coverage
boundaries, contract generation and drift checks, accessibility audit records.
All Chinese.

---

## Internationalization (i18n) — where to look

The application UI, backend error messages and LLM prompts are bilingual
(Simplified Chinese + English). The **code** is the reliable entry point:

| Concern | Where |
|---|---|
| Frontend message tables | `frontend/src/i18n/messages.zh-CN.ts` (authority) and `messages.en.ts` |
| Frontend runtime | `frontend/src/i18n/` (`locales.ts` / `translate.ts` / `index.tsx`) |
| Backend error messages | `backend/app/core/error_messages.py` |
| Backend negotiation | `backend/app/core/i18n.py`, `middleware/request_context.py` |
| LLM prompts | `backend/app/services/llm/prompts.py` |
| English overview | [`../README.en.md`](../README.en.md) §Internationalization |

---

## Maintenance conventions (for anyone editing the docs)

1. **New document**: put it in `docs/` (or the relevant topic directory) and add a
   status banner under the title; then add a row to §2 / §3 of the Chinese
   [`README.md`](./README.md).
2. **State change** (a snapshot is frozen, a plan lands, a report goes stale):
   edit the banner line only — do not rewrite the historical body.
3. **Moving a file**: update the paths in the Chinese map first, then the relative
   links inside the files. Files under `docs/**` are referenced by code comments,
   CI notes and other documents **by `file:line`**, so moving or inserting lines
   shifts those references.
4. **Keep this file in sync** with the Chinese map when the top-level inventory
   changes: this file deliberately lists documents at the same granularity, so a
   new living document should appear in both.
