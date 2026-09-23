# 贡献指南（CONTRIBUTING）

感谢你愿意为 EngramNote 花时间。这份文档只写**能被执行、能被核对**的东西：

- 所有门禁命令**照抄 `.github/workflows/ci.yml`**，不凭记忆写；
- 每条结论都带 `文件:行号`，方便你自己去核；
- 凡是"声明比现实宽松"的地方，本文直接写明现状，不粉饰。

> **本项目的底线规矩**（改代码前请先读一遍 `AGENTS.md`）：
> 文档与代码冲突时**以代码实测为准**（`AGENTS.md:11`）；
> 已否决的方向不要再提（`AGENTS.md:13-18`）；
> 改完必须过门禁（`AGENTS.md:37-44`）。

---

## 目录

- [1. 环境要求](#1-环境要求)
- [2. 装依赖](#2-装依赖)
- [3. 本地起服务](#3-本地起服务)
- [4. 门禁命令（照抄 CI）](#4-门禁命令照抄-ci)
- [5. 契约：改了后端接口必须重跑两条命令](#5-契约改了后端接口必须重跑两条命令)
- [6. 测试环境变量](#6-测试环境变量)
- [7. 错误契约：前端只认 `error_code`](#7-错误契约前端只认-error_code)
- [8. 提交约定](#8-提交约定)
- [9. 提 PR 前自检](#9-提-pr-前自检)
- [10. 你大概想找的东西在哪](#10-你大概想找的东西在哪)

---

## 1. 环境要求

| 软件 | 要求 | 依据 |
|---|---|---|
| Python | **3.10+** | `pyproject.toml:44`（`requires-python = ">=3.10"`）、`check_env.py:258` |
| conda | 推荐（不是必需） | `check_env.py:264-269` 会提示 `conda create -n mineru_env python=3.10` |
| Node.js | 见 `frontend/.nvmrc`（当前 `22.22.2`） | `frontend/.nvmrc:1`；下界同时写在 `frontend/package.json:73-75` 的 `engines.node` = `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0` |

⚠️ **Node 版本不是"随便挑个新的"**：`engines.node` 是 `jsdom@30` 自己的要求，
而 `jsdom` 是 `vitest` 跑组件测试的环境。Node 18/20 上 `npm ci` **只会警告**（EBADENGINE）、照样装得完，
但 `npm test` 会报一句与原因毫无关系的 `Test Files  no tests`——
CI 为此单独加了一步 `node -e "require('jsdom')"` 守卫，让这类失败**指名道姓**
（`.github/workflows/ci.yml:207-216`）。

**不需要装**：PostgreSQL、Redis、MinIO、任何独立向量库（`README.md:118`）。
本项目的存储是 SQLite + 文件系统 broker，这不是"暂时没配"，是**已定的路线**
（`AGENTS.md:15`、`backend/requirements.txt:31`）。

---

## 2. 装依赖

后端有**三份**清单，各有各的判据（`backend/requirements-dev.txt` 文件头、
`pyproject.toml:22-28`）：

| 清单 | 用途 | 命令 |
|---|---|---|
| `requirements.txt` | 用户装这一份：全新装完**服务能跑** | `pip install -r requirements.txt` |
| `requirements-test.txt` | CI 装这一份：**测试与 lint 能跑** | `pip install -r requirements-test.txt` |
| `requirements-dev.txt` | 贡献者读这一份：解释每个依赖为什么存在 | `pip install -r requirements.txt -r requirements-dev.txt` |

CI 装的是 `requirements-test.txt`（`.github/workflows/ci.yml:51`）——
它是 `requirements.txt` 的**测试子集**，故意不装
`sentence-transformers` / `chromadb` / `openai` 这些 GB 级依赖，
因为单元测试**不加载模型**（见 `backend/tests/conftest.py` 的离线守卫）。
**只跑门禁的话，装 `requirements-test.txt` 就够了。**

前端：

```bash
cd frontend
npm ci          # ci.yml:205；必须用 ci 而不是 install，锁文件才被尊重
```

> ⚠️ `npm ci` 对 `engines` 不匹配**只警告不失败**（`ci.yml:203-204`），
> 所以"Node 版本对不对"要靠第 4 节的 jsdom 守卫。

---

## 3. 本地起服务

一键脚本会拉起 3 个进程：后端 API（**8001**）、Celery Worker、前端（**5173**）
（`README.md:181`，端口也写在 `AGENTS.md:22`）：

```bash
# Windows
start.bat
# Linux / macOS
chmod +x start.sh && ./start.sh
```

手动起（3 个终端，命令与脚本一致，`README.md:185-193`）：

```bash
cd backend && python -m uvicorn app.main:app --reload --port 8001 --reload-dir app
cd backend && python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo  # Windows 必须 --pool=solo
cd frontend && npm run dev
```

首次配置密钥：`cp backend/.env.example backend/.env`，然后填 `JWT_SECRET_KEY` 等。
⚠️ **`APP_ENV` 默认是 `prod`**，prod 下 `JWT_SECRET_KEY` 为空（或为历史文档里的公开占位值）
会**拒绝启动**——这是有意的安全姿态，不是 bug。本地开发请显式写 `APP_ENV=dev`
（`README.md:165-169`、`backend/app/config.py:627-670`）。

> ⚠️ **`LOG_SQL` 默认 `false`，不要图省事打开**：`LOG_SQL=true` 会把 SQL 打进
> `data/logs`，其中含 **bcrypt 哈希与知识卡片/题目正文**（`backend/app/config.py:566-572`、
> `backend/app/database.py:41`、`backend/app/main.py:161-165`）。

---

## 4. 门禁命令（照抄 CI）

本地跑的就是 CI 跑的这几条。命令与出处逐条对应，**不要凭记忆写**（`AGENTS.md:43-44`）。

### 4.1 后端（在 `backend/` 下执行）

| 命令 | CI 出处 | 是否阻断 |
|---|---|---|
| `python -m pip install -r requirements-test.txt` | `ci.yml:51` | —（前置步骤） |
| `python -m ruff check app` | `ci.yml:56` | **阻断** |
| `python -m ruff check tests` | `ci.yml:61` | **阻断**（存量问题在 `backend/ruff.toml:20-30` 的 `per-file-ignores` 里豁免） |
| `python -m ruff check scripts` | `ci.yml:68` | **阻断** |
| `python scripts/gen_env_example.py --check` | `ci.yml:87` | **阻断**（`.env.example` 与 `app/config.py` 双向一致） |
| `python scripts/dump_openapi.py --check` | `ci.yml:129` | **阻断**（契约：代码 → `openapi.json`） |
| `python -m ruff format --check app tests` | `ci.yml:146` | **建议性**（`continue-on-error: true`，`ci.yml:147`；理由见 `ci.yml:131-145`） |
| `python -m pytest -q` | `ci.yml:154` | **阻断**（离线；网络被 `backend/tests/conftest.py` 挡住） |

三条 ruff 在本地可以合成一条（`README.md:251`、`backend/requirements-dev.txt` 用法段）：

```bash
cd backend
python -m ruff check app tests scripts
```

`dump_openapi.py --check` 在 CI 上带两个环境变量
（`APP_ENV=dev` 与一个**只为过校验的假** `JWT_SECRET_KEY`，`ci.yml:126-128`）。
你本机若有 `backend/.env` 就不必设；在**没有 `.env`** 的干净克隆里，
不设这两个变量会得到一句**误导性**的报错——它看起来像"契约漂移"，
实际是"环境不满足"（`ci.yml:112-125` 记录了这次实测）：

```bash
# 干净环境（没有 backend/.env）下跑契约检查
cd backend
APP_ENV=dev JWT_SECRET_KEY=ci-only-dummy-key-not-a-secret python scripts/dump_openapi.py --check
```

> Windows PowerShell 写法：`$env:APP_ENV='dev'; $env:JWT_SECRET_KEY='ci-only-dummy-key-not-a-secret'; python scripts/dump_openapi.py --check`

### 4.2 前端（在 `frontend/` 下执行）

| 命令 | CI 出处 | 是否阻断 |
|---|---|---|
| `npm ci` | `ci.yml:205` | —（前置步骤） |
| `node -e "require('jsdom'); console.log('jsdom ok on', process.version)"` | `ci.yml:216` | **阻断**（Node 太旧时让失败指名道姓） |
| `npm run lint` | `ci.yml:219`（脚本：`frontend/package.json:21`） | **阻断** |
| `npm run gen:api` + `git diff --exit-code -- src/api/generated/schema.ts` | `ci.yml:233-235`（脚本：`frontend/package.json:35`） | **阻断**（生成器幂等） |
| `npm run format:check` | `ci.yml:254`（脚本：`frontend/package.json:34`） | **阻断**（2026-09-23 起） |
| `npm test` | `ci.yml:266`（脚本：`frontend/package.json:23`） | **阻断** |
| `npm run build` | `ci.yml:269`（脚本：`frontend/package.json:19`） | **阻断** |
| `npx playwright install --with-deps chromium` | `ci.yml:289` | —（首次跑 E2E 前执行一次） |
| `npm run e2e` | `ci.yml:309`（脚本：`frontend/package.json:25`） | **阻断** |
| `npm run a11y` | `ci.yml:338`（脚本：`frontend/package.json:31`） | **阻断**（2026-09-23 起，`ci.yml:311-337`） |

`npm run e2e` / `npm run a11y` 是**自洽**的：dev server 由
`frontend/playwright.config.ts` 的 `webServer` 自动拉起与关闭（端口 4319、`--strictPort`），
**不需要**你另外起服务，也**不要**改成复用已存在的服务（`ci.yml:304-307`）。

> 📌 **两处文档不一致，以 CI 为准**（`AGENTS.md:10-11`）：
> `README.md:263` 把 `npm run a11y` 写作"建议性"，而 `ci.yml:311-338` 已在
> 2026-09-23 把它改成**阻断**；`README.md:254` 说 `ruff format --check` 建议性，
> 这一条与 `ci.yml:147` 的 `continue-on-error` **一致**。
> 本文件按 `ci.yml` 的现状写。（README 的修订不在本文件作者的可写范围内。）

**不阻断但会在 CI 里跑的两项**（`README.md:266-268`）：

| Job | 出处 | 性质 |
|---|---|---|
| `security-scan`（`pip-audit` + `npm audit`，归档报告） | `ci.yml:340-362` | 建议性，**但"扫描器没跑起来"（退出码 2）是无条件红灯**（`ci.yml:458-459`） |
| `docker-nginx-config`（部署配置守卫） | `ci.yml:481-529` | 只做文本 `grep` 守卫 |

---

## 5. 契约：改了后端接口必须重跑两条命令

前端类型（`frontend/src/api/generated/schema.ts`）从 `backend/openapi.json` 生成，
而那份 JSON 从**当前代码**生成。少跑任何一条，就会出现
"后端已改、schema 未更新，而前端照样编译通过"的窗口
（`ci.yml:89-110` 把这个状态叫"继续绿"）。

```bash
# 1) 代码 → openapi.json
cd backend && python scripts/dump_openapi.py

# 2) openapi.json → schema.ts（脚本内已含 prettier --write）
cd frontend && npm run gen:api
```

跑完把 `backend/openapi.json` 与 `frontend/src/api/generated/schema.ts` **一起**提交，
然后自己先跑一遍第 4 节的两条 `--check`，别让 CI 替你发现。

---

## 6. 测试环境变量

### 6.1 `TEST_PDF_PATH`：真实 PDF 语料**不在仓库里**

真语料曾是真实个人/客户数据，**已从仓库清除**，相关用例在未设置该变量时
**整模块 skip（而不是失败）**：

```bash
# 在 backend/ 下
TEST_PDF_PATH=/path/to/your.pdf python -m pytest -q
```

语料的**取用口径**是单一出口：`backend/app/test_support/corpus.py`
（`corpus.py:7` 的默认值只是一个占位路径、`:33` 是环境变量名，
`:51` 读环境变量、`:61-70` 在缺失时给出明确报错，而不是让用例静默跑空）。

未设置时**整模块 skip** 的例子：`backend/tests/test_week1_2_fixes.py:43-46`；
另有若干**脚本式 E2E**（已归档到 `backend/scripts/dev/`）同样只从该变量取真实语料，
例如 `backend/scripts/dev/test_week9_10_e2e.py:4`、`test_week11_e2e.py:4`、
`test_week12_e2e.py:4`、`test_full_e2e.py:34`。

"仓库里不留个人/客户数据"这条不变量由
`backend/tests/test_no_personal_data.py:15`、`:72`、`:95` 钉住。

### 6.2 `ENGRAMNOTE_ALLOW_NETWORK_TESTS=1`：默认**禁止**真实外呼

`backend/tests/conftest.py` 在 session 级装了网络守卫：默认**阻断一切真实网络访问**，
只有显式把 `ENGRAMNOTE_ALLOW_NETWORK_TESTS` 设成 `1` / `true` / `True`
（`conftest.py:184`）才放行，且用例必须自带 `@pytest.mark.integration`
（守卫的原文提示见 `conftest.py:194-195`，跳过逻辑见 `conftest.py:452-454`）。

`backend/tests/integration/` 是**需要真实外部服务**的集成脚本，`pytest.ini:9-10`
的 `norecursedirs` 让它们**默认不被收集**——因为它们会在**收集阶段**就发真实请求、
烧掉真实 LLM 额度（`pytest.ini:3-6` 记录了这次实测）。要跑它们必须显式指定：

```bash
cd backend
ENGRAMNOTE_ALLOW_NETWORK_TESTS=1 python -m pytest tests/integration -m integration   # pytest.ini:7-8
```

CI 里这两个变量是显式写死的：`ENGRAMNOTE_ALLOW_NETWORK_TESTS: "0"`（`ci.yml:153`）。

> ⚠️ 守卫还包含**真实生产库写入守卫**：测试默认落到会话级临时库与临时 Vault，
> **不碰** `backend/data/db/engramnote.db` 与真实存储（`conftest.py:67-105`、`:108-120`）。
> 请不要为了"让测试跑通"而绕过它。

---

## 7. 错误契约：前端只认 `error_code`

所有业务错误的响应体是统一信封（`backend/app/core/app_error.py:9`、
`backend/app/middleware/error_handler.py:11`、`backend/app/main.py:508-518`）：

```json
{ "detail": "面向用户的中文说明", "error_code": "STABLE_MACHINE_CODE", "request_id": "..." }
```

- `error_code` 是**稳定契约**：全大写、语义化、`name == value`，
  **不把 HTTP 状态码写进名字**，一个 code 只表达一件事（`app_error.py:26-33`）；
- `detail` 是给人看的文案，**可能随时改**；
- **前端一律按 `error_code` 分流，不要匹配中文文案**（`README.md:241`）。

新增/调整错误码时：改 `app_error.py` 的常量区，并注意 `app/api/**` 里
仍允许的少量 `HTTPException` 豁免——例如必须带 `WWW-Authenticate` 质询头的认证出口：
`backend/app/api/auth.py:100`、`:114`、`:226`（豁免语法是紧邻 raise 的
`# error-contract: exempt — <理由>`）。它们不能改成 `AppError` 的原因很具体：
`ErrorHandlerMiddleware._error_response()` 的签名里**没有** `headers=` 参数
（`backend/app/middleware/error_handler.py:96-116`）。背景见 `docs/overhaul-plan.md:2947`。

---

## 8. 提交约定

- **提交信息用中文，重点说清"为什么"**，而不是"改了哪些行"。
  本仓库的历史提交就是这个形态（`docs/open-source-readiness.md:729`：
  "150 个提交每条都说清了'为什么'"），**不需要**改写成 Conventional Commits。
- 一次提交只做一件事：**纯格式重排不要和逻辑修复混在一起**——
  `ruff format --check` 至今是建议性，正是为了让审阅者能分辨
  "哪一处是真修复、哪一处只是排版"（`ci.yml:131-145`）。
- **绝不提交**（`.gitignore:22-40`、`README.md:156-159`）：
  - `backend/.env`（含真实密钥）；
  - `backend/data/`（真实库、Vault、模型、日志）；
  - 任何真实笔记、真实 PDF/音视频语料（走 `TEST_PDF_PATH`）。
- 单人项目也请**只提交、不推送**（`AGENTS.md:33`）——推送由维护者决定。

---

## 9. 提 PR 前自检

按改动面选跑，**全绿再开 PR**（对应的勾选项已做进
`.github/PULL_REQUEST_TEMPLATE.md`）：

- [ ] 改了后端 → 第 4.1 节的后端门禁全跑（ruff `app`/`tests`/`scripts`、
      `gen_env_example.py --check`、`dump_openapi.py --check`、`pytest -q`）
- [ ] 改了前端 → 第 4.2 节的前端门禁全跑（`lint` / `test` / `build`，
      涉及 e2e 或样式时另加 `e2e`、`a11y`）
- [ ] 改了后端接口 → `python scripts/dump_openapi.py` **与** `npm run gen:api`
      都重跑，产物一起提交（第 5 节）
- [ ] 改了依赖 → 同步 `backend/requirements*.txt` 与 `pyproject.toml`
      （两者一致性由 `backend/tests/test_packaging_metadata.py` 断言，漂移即红，
      见 `pyproject.toml:30-31`）
- [ ] 结论带 `文件:行号`；没有"建议加强"这类空话（`AGENTS.md:34`）

---

## 10. 你大概想找的东西在哪

| 想知道 | 去哪看 |
|---|---|
| 系统架构（**重构前快照**） | `docs/architecture.md`（文件头有标注） |
| 重构全过程台账（最完整，11709 行） | `docs/overhaul-plan.md` |
| 还没做的事 / 不影响当前使用的缺口 | `docs/overhaul-plan.md` 附录 BN（`:11145`） |
| 开源化差距核查（带 `文件:行号`） | `docs/open-source-readiness.md` |
| 关键取舍归档（F-xx，只读） | `docs/decisions.md` |
| 为什么只能跑一个 worker | `docs/sqlite-single-writer.md` |
| 依赖扫描的原始结果与处置口径 | `docs/security-scan.md` |
| 安全漏洞怎么上报 | `SECURITY.md` |
| 升级会不会动我的数据 | `UPGRADING.md` |
| 改了什么、哪个版本 | `CHANGELOG.md` |
| 容器化到底能不能用 | `README.md` 的「关于容器化」一节 + `docker-compose.yml` 文件头（**未验证**） |

---

## License

贡献即表示同意以 **MIT License** 授权（`LICENSE`、`pyproject.toml:45`）。
