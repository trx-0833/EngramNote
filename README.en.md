# EngramNote

> AI-powered study-note management and knowledge base — closing the loop from
> **material → cleaning → understanding → review**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-22.22.2+-green.svg)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-blue.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18+-blue.svg)](https://react.dev)
[![CI](https://github.com/trx-0833/EngramNote/actions/workflows/ci.yml/badge.svg)](https://github.com/trx-0833/EngramNote/actions/workflows/ci.yml)

> **📖 The canonical README is Chinese: [README.md](README.md).**
> This English file covers what you need to run and understand the project.
> Most in-repo documentation (`docs/`, `frontend/docs/`, code comments) is
> **Chinese only** — see [Documentation language](#documentation-language).

---

## Table of contents

- [What it solves](#what-it-solves)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Internationalization](#internationalization)
- [API and health probes](#api-and-health-probes)
- [Quality gates](#quality-gates)
- [Project layout](#project-layout)
- [Documentation language](#documentation-language)
- [License](#license)

---

## What it solves

Read a PDF, take notes, forget everything two weeks later — that is how most
studying goes, because **cleaning, understanding and reviewing live in three
separate tools** and none of them handles the next step.

EngramNote pulls that whole chain into one self-hosted application:

```
upload material → convert to Markdown → AI cleaning (de-noise) → knowledge cards + questions
                → knowledge graph → RAG Q&A → FSRS spaced repetition → study reports
```

Three design principles:

1. **The original is immutable.** AI only produces **side-car** cleaned copies
   and cards; the original file and its conversion output are always kept.
2. **Zero external dependencies.** SQLite + a filesystem broker — no PostgreSQL,
   no Redis, no separate vector database.
3. **Large models are isolated.** Embedding models such as BGE-M3 are loaded
   **only in the async worker process**; the main process stays lightweight.

---

## Features

**Material intake**
- PDF / images / Office / audio-video / Markdown. PDFs go through MinerU
  (cloud API or local model); audio and video go through Qwen3-ASR.
- Project + side-car storage layout:
  `{vault}/{user_id}/{project_slug}/source|output|history|cache`
- Drop files straight into `source/` and click **Scan import**.

**AI cleaning**
- Line-by-line rule-based de-noising (**no rules applied inside code or math
  blocks**) plus BGE-M3 vector-similarity de-duplication.
- Three views — original / cleaned copy / line-level diff. A version snapshot is
  taken before overwriting, and duplicate blocks can be restored one by one.

**AI understanding**
- Chapter summaries, 4 kinds of knowledge cards (concept / formula / Q&A /
  definition), automatic question generation (multiple choice / fill-in / short
  answer).
- Markdown-structure-aware chunking: splits on the **atomic blocks** of tables,
  code blocks and lists, never cutting through the middle of a structure.

**Retrieval and Q&A**
- Hybrid retrieval over **two channels**: SQLite FTS5 lexical (BM25) + vectors in
  the `chunks` table, fused with RRF (`rag_rrf_k=1`, BM25 weight `0.65`).
- Vectors and source text live in the **same database and the same table**
  (`chunks`) — no separate vector store.
- SSE streaming answers with cited sources. When retrieval fails it **degrades
  explicitly and tells the user** instead of pretending to answer.

**Review and mastery**
- Scheduler **FSRS-5** (`review_scheduler="fsrs"`, with an explicit fallback to
  SM-2), including fuzzing and a business-day due time.
- Two-factor mastery, weak-point prioritisation; daily question limit
  `daily_review_limit=10`.
- Learning assessment: note comparison + blind-spot detection + suggestions;
  study goals (daily / weekly) and daily recommended tasks.
- Review reminders: browser notifications + optional SMTP email + quiet hours;
  refreshed on a Celery Beat schedule.

**Other**
- Knowledge graph: relations inferred by both embedding similarity and an LLM,
  visualised as a force-directed graph.
- Note version history (user edit / automatic cleaning / system snapshot),
  trash, annotations, and "ask AI about the selected text".
- LLM call governance: retries, rate limiting (per user / per provider / global),
  quotas, caching, and cost accounting.

---

## Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | FastAPI + SQLAlchemy (async) + SQLite | No external database (`aiosqlite`) |
| Async tasks | Celery + filesystem broker + Celery Beat | No Redis needed |
| Embeddings | BGE-M3 (`BAAI/bge-m3`) | **Loaded only in the Celery worker process** |
| Vectors + lexical | `chunks` table + SQLite **FTS5** | Same DB and table; no Chroma |
| Retrieval fusion | vectors + BM25 → RRF | Two channels (the n-gram channel was removed) |
| Scheduler | **FSRS-5** | SM-2 fallback; state in `review_states` / `review_logs` |
| AI calls | DeepSeek / GLM (OpenAI-compatible) | One gateway: retry / rate limit / cache / accounting |
| Document parsing | MinerU (cloud `vlm-http-client` or local `pipeline`) | Keeps LaTeX formulas and tables |
| Frontend | React 18 + TypeScript + Vite | `React.lazy` route splitting |
| Graph rendering | react-force-graph-2d | Force-directed |
| Containers | Docker / Compose / Nginx | **Legacy, never build-verified** — see below |

---

## Quick start

### Requirements

| Software | Version | Notes |
|---|---|---|
| **Python** | 3.10+ | conda recommended (`conda create -n mineru_env python=3.10`) |
| **Node.js** | **22.22.2+** | Lower bound is in `frontend/.nvmrc` and `engines.node` |
| **Git** | 2.0+ | |
| **pip** | 23+ | |

> **Nothing else to install**: no PostgreSQL, Redis, MinIO or vector database.

> ⚠️ **The Node version is not arbitrary.** `engines.node` comes from `jsdom@30`
> (`^22.22.2 || ^24.15.0 || >=26.0.0`), and jsdom is the environment `vitest`
> runs component tests in. On Node 18/20 `npm ci` only **warns** (EBADENGINE)
> and installs fine, but `npm test` then fails with an unrelated-looking
> `Test Files  no tests`. CI has a separate `node -e "require('jsdom')"` guard
> that makes this failure name its own cause.

### Install and check

```bash
# 1. Clone
git clone https://github.com/trx-0833/EngramNote.git
cd EngramNote

# 2. (Recommended) create a conda environment
conda create -n mineru_env python=3.10
conda activate mineru_env

# 3. Detect and auto-fix what is required (deps + .env template + BGE-M3 + VAD)
python check_env.py --fix
```

`check_env.py` runs 10 checks (versions → dependencies → `.env` → models → data
dirs). Common flags:

```bash
python check_env.py                    # check only, no fixes
python check_env.py --check --deep     # import every dependency for real (~26s)
python check_env.py --fix              # fix required items (incl. BGE-M3 ≈2.2GB)
python check_env.py --fix --all        # also optional big models (MinerU ≈7GB + ASR ≈1.2GB)
python check_env.py --download-mineru  # MinerU only (for local PDF parsing)
python check_env.py --download-asr     # ASR only (for audio/video transcription)
```

> To save 7GB of disk, skip local MinerU — set `MINERU_API_TOKEN` and it uses the
> cloud API by default.

### Configure secrets

```bash
# The check script already generated .env from the template; fill in your keys
# Windows: notepad backend\.env      Linux/macOS: nano backend/.env
```

| Variable | Required? | Notes |
|---|---|---|
| `DEEPSEEK_API_KEY` | one of the two | Recommended for production. [platform.deepseek.com](https://platform.deepseek.com/) |
| `GLM_API_KEY` | one of the two | Has a free tier. [open.bigmodel.cn](https://open.bigmodel.cn/) |
| `JWT_SECRET_KEY` | **depends on env** | With `APP_ENV=prod` (the default) the app **refuses to start** if empty; with `APP_ENV=dev` it is generated and persisted to `data/.jwt-secret`. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MINERU_API_TOKEN` | optional | Cloud PDF parsing. [mineru.net](https://mineru.net/); without it only text formats such as Markdown work |

> ⚠️ **`APP_ENV` defaults to `prod`**: copying `.env.example` and leaving
> `JWT_SECRET_KEY` empty will make startup **fail**. That is a deliberate
> security posture, not a bug. For local development set `APP_ENV=dev`.

### Run

```bash
# Windows
start.bat

# Linux / macOS
chmod +x start.sh && ./start.sh
```

The scripts bring up three processes: backend API (**8001**), Celery worker,
frontend (**5173**).

Manually (three terminals, same commands the scripts use):

```bash
# Terminal 1: backend API
cd backend && python -m uvicorn app.main:app --reload --port 8001 --reload-dir app

# Terminal 2: Celery worker (Windows requires --pool=solo)
cd backend && python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo

# Terminal 3: frontend
cd frontend && npm run dev
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8001 |
| API docs | http://localhost:8001/docs (**only with `APP_ENV=dev`**; in prod `/docs`, `/redoc` and `/openapi.json` are all disabled) |

> **The first PDF upload** makes the worker load the embedding model (~30s);
> after that it stays resident.

---

## Configuration

The full field list lives in `backend/app/config.py` (`Settings`); the template is
`backend/.env.example`. The four most-used switches:

| Variable | Default | Effect |
|---|---|---|
| `APP_ENV` | `prod` | `dev`: allows an empty JWT key (auto-generated), returns tracebacks, opens `/docs`. `prod`: uniform error messages, docs disabled |
| `LOG_SQL` | `false` | Whether to log SQL into `data/logs`. ⚠️ Contains bcrypt hashes and card text — **do not enable in production** |
| `LLM_PROVIDER` | `auto` | `auto` / `deepseek` / `glm`; `auto` = GLM in dev, DeepSeek in prod |
| `REVIEW_SCHEDULER` | `fsrs` | `fsrs` (FSRS-5) or `sm2` (fallback) |

Other commonly changed settings: `CORS_ORIGINS` (local 5173/3000 by default),
`MAX_UPLOAD_SIZE_MB` (500), `DAILY_REVIEW_LIMIT` (10),
`EMBEDDING_MODEL` / `EMBEDDING_MODEL_FALLBACK`, `VAULT_DIR`, and the SMTP and
reminder settings (`SMTP_*`, `EMAIL_REMINDER_ENABLED`, `REMINDER_QUIET_HOURS_*`).

---

## Internationalization

The app ships with **two UI languages: Simplified Chinese (`zh-CN`, default) and
English (`en`)**. Switch languages from the sidebar footer; the choice is
persisted and also stored on the user record.

### How it is wired

| Concern | Where | Notes |
|---|---|---|
| Frontend messages | `frontend/src/i18n/messages.zh-CN.ts` (authority) + `messages.en.ts` | `en` is declared `satisfies typeof zhCN`, so **a missing key is a compile error** |
| Frontend runtime | `frontend/src/i18n/` (`locales.ts` / `translate.ts` / `index.tsx`) | Hand-written, **no i18n library** — see below |
| Backend error messages | `backend/app/core/error_messages.py` | Translated per request from `Accept-Language`; `error_code` stays language-independent |
| Backend negotiation | `backend/app/core/i18n.py`, `middleware/request_context.py` | `zh-*` → `zh-CN`, `en-*` → `en`, anything else → default |
| LLM prompts | `backend/app/services/llm/prompts.py` | 11 prompts in both languages; the prompt language follows the interface language |

### Why no i18n library

The project has a **zero-new-dependency** principle. Adding i18next (plus its
React bindings and a locale-detector plugin) would mean three dependencies for
two languages and a few hundred keys. The hand-written layer is about 400 lines
and gives something a library would not: **`tsc` proves the two message tables
have the same shape**, so forgetting a translation is a build failure rather
than a runtime fallback to a raw key.

The cost is that three things have to be built by hand — and they are:

- **Pluralisation** is not implemented; `{count}` is interpolated directly.
- **Date and number formatting** goes through `frontend/src/utils/datetime.ts`,
  which takes the locale as a parameter.
- **A key-name guard test** compensates for the one place where the compiler is
  bypassed (`frontend/src/utils/labels.i18n.test.ts`).

### `Accept-Language` is sent on every request

`frontend/src/api/client.ts` attaches the current locale to every API call, so
backend error messages come back in the language you are reading. Verified
end-to-end in `frontend/e2e/i18n.spec.ts`.

---

## API and health probes

| Endpoint | Meaning |
|---|---|
| `GET /health` | **Liveness**: reports application state only, **deliberately does not check dependencies** (so a flaky dependency is not translated into a restart) |
| `GET /ready` | **Readiness**: runs one real business query to verify "the database is reachable and the schema is ready" — 200 when ready, 503 otherwise. Queue depth and index backlog are only **reported**, never decide the status code |

The API lives under `/api`, grouped by domain: `auth` `notes` `upload` `cleaning`
`understanding` `review` `report` `graph` `folders` `projects` `assessment`
`knowledge` `goals` `tasks` `llm`. Error responses are a stable contract:

```json
{ "detail": "user-facing message", "error_code": "STABLE_MACHINE_CODE", "request_id": "..." }
```

`detail` is translated according to `Accept-Language`; **`error_code` is not** —
branch on it, never on the message text.

---

## Quality gates

CI is defined in `.github/workflows/ci.yml`; the same commands run locally
(copied from CI — do not write them from memory):

```bash
# Backend (in backend/)
python -m ruff check app tests scripts     # blocking
python scripts/dump_openapi.py --check     # contract: code → openapi.json, blocking
python -m pytest -q                        # offline tests; network is blocked by conftest.py, blocking
python -m ruff format --check app tests    # advisory (intentional, see ci.yml:131-147)

# Frontend (in frontend/)
npm ci
npm run lint          # ESLint, blocking
npm run gen:api       # openapi.json → src/api/generated/schema.ts (must be zero-diff), blocking
npm test              # vitest unit/component, blocking
npm run build         # tsc + vite build, blocking
npm run e2e           # Playwright (with /api stubbed), blocking
npm run a11y          # axe-core accessibility, blocking
```

Two more jobs run in CI: `security-scan` (`pip-audit` + `npm audit`, **archives
only, non-blocking**) and `docker-nginx-config` (deployment-config guard,
**blocking**).

> **After changing a backend interface** you must re-run
> `python scripts/dump_openapi.py` and `npm run gen:api`, or the frontend will
> compile against a stale contract.

---

## Project layout

```
EngramNote/
├── backend/
│   ├── app/
│   │   ├── api/                # routes, split by domain
│   │   ├── models/             # ORM (20 model modules / 21 tables)
│   │   ├── schemas/            # Pydantic contracts
│   │   ├── services/           # cleaning / embedding / rag / fsrs / scheduler
│   │   │                       #   mastery / graph / version / goal / notification / mineru / asr
│   │   │   └── llm/            # LLM gateway: gateway / prompts / scenes / client / accounting
│   │   ├── tasks/              # Celery tasks (convert / clean / understand / embedding / reminder)
│   │   ├── core/               # error contract, logging, request context, i18n
│   │   ├── middleware/         # rate limiting, error handling
│   │   ├── database.py         # table creation + runtime migration
│   │   └── config.py           # every configuration field (single source of truth)
│   ├── alembic/                # ⚠️ legacy: not called on the startup path
│   ├── scripts/                # ops and dev scripts (backup / restore / verify / eval / drift)
│   ├── tests/                  # pytest (offline by default)
│   └── requirements.txt
├── frontend/
│   ├── src/                    # React + TS (api / components / pages / hooks / i18n / styles)
│   ├── e2e/                    # Playwright: functional, a11y, i18n, full-chain
│   ├── scripts/                # CSS and documentation probes
│   └── package.json
├── docs/                       # architecture, decisions, plans, topic records (Chinese)
├── check_env.py                # environment check and bootstrap
├── start.bat / start.sh        # one-command start (3 processes)
├── docker-compose.yml          # ⚠️ legacy, unverified
├── README.md                   # canonical README (Chinese)
└── README.en.md                # this file
```

### About containers (please read)

The repository keeps `docker-compose.yml`, `backend/Dockerfile`,
`frontend/Dockerfile` and `frontend/nginx.conf`, but:

- the project **actually runs as local processes** (backend 8001 / frontend 5173);
- containerisation and PostgreSQL/Redis were **explicitly dropped** for disk and
  resource reasons;
- these files have **never been built or run in this project**. The config guard
  only proves "the files were not broken", **not that this path works**.

```bash
# ⚠️ UNVERIFIED: do not treat this as a supported deployment path
docker compose up -d
```

---

## Documentation language

**The in-repo documentation is Chinese.** That includes:

- `README.md` (the canonical README — this English file is a summary),
- everything under `docs/` (architecture, decisions, plans, audits),
- `frontend/docs/` (contract generation, CSS conventions, e2e notes, a11y audit),
- and the **code comments**, which are unusually detailed and often record *why*
  a design was chosen and which alternatives were rejected.

Two English entry points exist:

| Entry point | Covers |
|---|---|
| **this file** | install, run, configuration, tech stack, i18n architecture, quality gates |
| [`docs/README.en.md`](docs/README.en.md) | the documentation map: the three-state banner convention, what each document contains, and which one to trust |

If you need the actual content of a specific Chinese document, translation is
currently the practical route. The code itself is the most reliable source for
anything beyond these two files: every non-obvious decision is commented at the
point where it matters.

---

## License

MIT License — see [LICENSE](LICENSE)

---

**EngramNote** — from passive reading to active internalisation and long-term memory
