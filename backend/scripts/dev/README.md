# `backend/scripts/dev/` —— 一次性开发脚本（历史归档）

**2026-09-14 建**：把 `backend/` 根目录的 **15 个一次性脚本**移进来，
执行的是 `docs/overhaul-plan.md` 的**计划项 0.2**（该行的状态随之从
"🟡 守卫已落地，脚本搬迁未做"改为"✅ 已落地"；审计登记见该文件附录 BJ）。

**这些脚本不是测试套件的一部分，也不保证今天仍能跑通**：它们是当时某次
联调 / 排障用的现场脚本，多数硬编码了真实笔记 ID、绝对路径或
`localhost:8000`。`pytest` **不会**收集它们（`backend/pytest.ini` 的
`testpaths = tests`）。它们被保留是为了**可追溯**，不是为了被再次调用。

**2026-09-23 第二批（9 个）**：`backend/tests/` 根下那 9 个"零用例脚本"也搬了进来 ——
这是计划项 0.2 的**另一半**（"`backend/tests/` 只留正式测试"；第一批只搬了 `backend/`
根目录的 15 个）。它们同源（e2e 现场脚本、`localhost:8001`、真实语料走 `TEST_PDF_PATH`），
但危害更大：它们躺在 **pytest 的收集目录里**，收集期就会被 `import`；每个模块导入时都建
`httpx.Client`，`test_full_e2e.py` 还 `os.makedirs(...)` 建目录，另有 2 个会直连**真实
生产库**插数据。清单见下面"第二批清单"，搬迁时改了什么见文末"第二批搬迁（2026-09-23）"。

## 清单（搬迁前的 git 状态 / 用途 / 谁引用它）

| 文件 | 用途（文件头自述） | 搬迁前的引用方 | git 状态 |
|---|---|---|---|
| `test.py` | **与项目无关**：一段 11 行的 generator 教学片段（`count_to_three`） | 只有 `docs/overhaul-plan.md` 的清单列了它 | 被忽略（`.gitignore:64`） |
| `test_api.py` | 快速验证 API 是否正常（`urllib`，写死 `localhost:8000`） | `docs/archive/新手教学.md:4175` 有 `python test_api.py`；`docs/overhaul-plan.md` 清单 | 被忽略（`:65`） |
| `test_clean_failed_api.py` | 模拟 `cleaning_failed` 再打 API（硬编码一个笔记 UUID） | `docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_clean_failed_db.py` | 用笔记归属用户走一遍清洗失败流程（相对路径 `data/db/…`，须在 `backend/` 下跑） | `docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_clean_failed_quick.py` | 清洗失败 / 停止清洗的快速验证（硬编码 PDF 绝对路径） | `.trae/documents/engramnote-migration-completion-plan.md`、`docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_clean_quick.py` | 快速测清洗（`httpx`，写死 `127.0.0.1:8000`） | `docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_cleaning_failed.py` | 清洗失败脚本；**唯一含 `def test_cleaning_flow()` 的文件**，但 pytest 不收集它（见下） | `docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_cleaning_pipeline.py` | 清洗全链路脚本（`API_BASE` 写死 `127.0.0.1:8000`） | `.trae/…migration-completion-plan.md`、`docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_convert_direct.py` | 绕过 Celery broker 直接调 `_convert_document`（`import app.*`） | `docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_e2e.py` | 自己拉起 uvicorn（:8765）跑"上传→转换"端到端 | `.trae/documents/deep_audit_report.md`、`docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `test_pdf_pipeline.py` | PDF 上传 + 转换端到端（硬编码 PDF 绝对路径） | `.trae/…migration-completion-plan.md`、`docs/overhaul-plan.md` 清单 | 被忽略（`:66`） |
| `verify_clean.py` | 验证清洗结果（硬编码笔记 ID 前缀与绝对路径） | `docs/overhaul-plan.md` 清单 | 被忽略（`:67`） |
| `e2e_cleanup.py` | 清理 E2E 遗留测试账号及其数据（绝对路径） | `backend/scripts/dev/test_full_e2e.py:758,767`（注释与提示串）、`backend/tests/测试账号信息.md:35,45`、`docs/code-review-report.md`、`docs/verification-report-20260830.md:24` | **已跟踪**（唯一一个） |
| `reset_cleaning.py` | 把卡在 `cleaning` 的笔记重置为 `converted`（绝对路径） | `docs/archive/开发时间表.md:77,514`、`docs/archive/新手教学.md:3709`、`docs/overhaul-plan.md` 清单 | 被忽略（`:68`） |
| `restore_note.py` | 7 行：把**某一条**硬编码 UUID 的笔记改回 `cleaned` | `docs/overhaul-plan.md` 清单 | 被忽略（`:69`） |

### 第二批清单（2026-09-23，从 `backend/tests/` 搬入）

| 文件 | 用途（文件头自述） | 搬迁前的引用方 | git 状态 |
|---|---|---|---|
| `test_full_e2e.py` | 全链路 API 级 e2e（登录→上传→转换→清洗→理解→RAG→SM-2→报告→跨用户隔离→负面用例），报告写 `tests/results/` | `backend/tests/测试账号信息.md:3,42`、`backend/tests/test_rate_limit.py:26`（注释）、`docs/open-source-readiness.md`（§2.20）、`docs/overhaul-plan.md:2938,10711` | 已跟踪 |
| `test_full_flow.py` | 第 7 周全流程（真实 PDF + 卡片编辑/删除/归档新功能） | `docs/open-source-readiness.md`（§2.17）、`docs/archive/开发时间表.md:150`、`backend/tests/test_rate_limit.py:26` | 已跟踪 |
| `test_week5_6_integration.py` | Week5-6 全流程（上传→转换→清洗→理解→题目→RAG） | `docs/open-source-readiness.md`（§2.17）、`backend/tests/test_rate_limit.py:26` | 已跟踪 |
| `test_week8_e2e.py` | 第 8 周复习调度 e2e；**直连真库插题目**（`:43`、`:139` 的 `sqlite3.connect("data/db/engramnote.db")`） | `docs/open-source-readiness.md`（§2.20）、`docs/archive/开发时间表.md:548` | 已跟踪 |
| `test_week8_review.py` | 第 8 周复习全流程（真实 PDF，SM-2 参数/统计/历史验证） | `docs/archive/开发时间表.md:549` | 已跟踪 |
| `test_week8_review_existing.py` | 第 8 周复习（复用库里已有用户与题目；`:45` 直连真库） | `docs/overhaul-plan.md:2938` | 已跟踪 |
| `test_week9_10_e2e.py` | 第 9-10 周全流程（卡片/题目、报告、趋势、薄弱点、异常与认证校验） | `backend/tests/test_no_personal_data.py:14`（"第三种写法"）、`docs/overhaul-plan.md:2938` | 已跟踪 |
| `test_week11_e2e.py` | 第 11 周全流程 + 前端 `/today` 路由可访问性 | `docs/open-source-readiness.md`（§2.17）、`docs/archive/开发时间表.md:246` | 已跟踪 |
| `test_week12_e2e.py` | 第 12 周发布准备（Git/.gitignore/Docker/启动脚本/README）+ API 全流程 | `docs/open-source-readiness.md`（§2.17）、`docs/archive/开发时间表.md:281` | 已跟踪 |

> 行号实测于 **2026-09-23 23:16**，**2026-09-24 00:33 重新对过一次**
> （`docs/open-source-readiness.md` 当天被改写，行号整体下移：§2.17 `:396`→`:423`、
> §2.20 `:438-443`→`:463-480`）。⚠️ 这两份 doc 都被**并发编辑**过多次 ——
> `docs/overhaul-plan.md`（`:2925`→`:2938`）、`docs/archive/开发时间表.md`（`:148`→`:150` 等）
> 的行号也都漂移过，所以引用时**以内容与小节号为准**，行号只当定位线索。
>
> "已跟踪"一列的依据：`docs/open-source-readiness.md`（§2.17）把它们列为"**已跟踪**"文件，
> 且 `backend/tests/test_no_personal_data.py` 的 `git grep`（只扫已跟踪文件）历史上命中过它们。
> ⚠️ **2026-09-23 这次搬迁按指令没有运行任何 git 命令**，因此该列由上面的仓库内证据推出，
> 不是当场 `git status` 的读数。

## 搬迁时同步改了什么（否则会当场坏）

1. **`test_e2e.py` / `test_pdf_pipeline.py` / `test_convert_direct.py`**：
   三者都用 `os.path.dirname(os.path.abspath(__file__))` 推"项目根"再
   `sys.path.insert` / 当 uvicorn 的 `cwd`。文件往下挪了两级之后那个值会变成
   `backend/scripts/`，所以改成**上溯三级**（`backend/`），并加注释说明。
   其余脚本用相对路径或绝对路径，取值只依赖 **cwd**，搬迁不影响。
2. **`ruff check scripts`**：CI 有 `python -m ruff check scripts` 这一步
   （`.github/workflows/ci.yml:63-68`），而 `scripts/` 是**递归**扫描的 ——
   这 15 个文件在原位置（`backend/` 根）从未被 lint 过，搬进来就会带出
   **59 条**历史问题（46×F541 f-string 无占位符、7×F401 未使用导入、
   2×E401 一行多导入、2×E402 导入不在文件头、1×B007、1×F841）。
   搬迁时已全部按最小改动修掉（`ruff check scripts` 现在 **All checks passed**）。
3. **`pytest` 不受影响**：`pytest.ini` 的 `testpaths = tests` 决定了
   `python -m pytest` 只收 `tests/`，这些脚本**过去和现在都不被收集**。
   ⚠️ 其中 `test_cleaning_failed.py` 定义了 `def test_cleaning_flow():` ——
   名字像真测试，但（a）它不在 `testpaths` 里，（b）被 `pytest tests/test_cleaning_failed.py`
   直接点名时会真的去打 `localhost:8000`。若将来要把它变成正式测试，
   必须重写成用 `conftest.py` 的隔离 fixture，而不是搬回来。
4. **`test_e2e.py` 的存活探针 `/docs` → `/health`（2026-09-14，阶段 0.10 之后）**：
   该脚本用 `GET /docs` 判断"服务器起来了没有"。0.10 起生产姿态
   （`APP_ENV` 非 dev，含遗留 `DEBUG=false`；本机 `.env` 就是）**根本不注册**
   `/docs`、`/openapi.json`、`/redoc`（`app/main.py::_schema_endpoint_kwargs`），
   于是探针拿到 404、脚本空等 30 秒后打印"[FAIL] 服务器启动超时"并退出 1 ——
   而服务器其实早就起来了。两处探针都改为 `GET /health`（不依赖任何东西的
   **存活**探针，任何姿态都注册、都 200）。
   ⚠️ 刻意**不**用 `/ready`：它表达"现在能不能干活"（DB 连得上 + schema 在），
   把依赖抖动翻译成"服务器启动超时"会把排查引向错误方向 ——
   `/ready` 是给编排器的，不是给"我的子进程起来了吗"这个问题的。
   这也不是"换个端点"那么简单：**任何**门禁（认证或关闭）都会让调试端点
   不再返回 200，**拿调试端点当存活探针本身才是缺陷**。
   同时确认：`backend/scripts/**` 里没有其它 `/docs`｜`/openapi.json`｜`/redoc`
   的探针（`dump_openapi.py` 只在注释里提到它们，它走进程内 `app.openapi()`）。

## 第二批搬迁（2026-09-23）：9 个脚本从 `backend/tests/` 搬入

**为什么搬**：这 9 个文件**实测每个都是 0 条 `def test_`**（只有 `main()` /
`log_step()` 这类脚本结构），但名字是 `test_*.py`、又躺在 `testpaths = tests` 里，
于是 `pytest` **收集期就会 `import`** 它们。导入期的副作用：模块级
`httpx.Client(...)`（建连接池对象，不发请求）、`test_full_e2e.py:49` 的
`os.makedirs(RESULT_DIR)`（建 `backend/tests/results/`）。真正危险的是另外两个 ——
`test_week8_e2e.py:43,139` 与 `test_week8_review_existing.py:45` 会
`sqlite3.connect("data/db/engramnote.db")` 写**真实生产库**（写在函数里，所以是
"被调用时"触发，不是导入时）。这正是 `docs/open-source-readiness.md`（§2.20）登记的问题。

**搬迁时同步改了什么（否则会当场坏或行为漂移）**：

1. **删掉 `pytestmark = pytest.mark.skipif(PDF_PATH is None, ...)` 及其 `import pytest`**
   （6 个文件：`test_full_flow.py` / `test_week5_6_integration.py` / `test_week8_review.py` /
   `test_week9_10_e2e.py` / `test_week11_e2e.py` / `test_week12_e2e.py`）。那个 skipif 是
   **给 pytest 用的**：脚本不再是 pytest 用例，缺语料时必须**响亮失败**，不是把自己 skip 掉。
2. **`real_pdf_path()` → `require_pdf_path()`**（同 6 个文件）。两个函数都在
   `app/test_support/corpus.py`，后者拿不到语料就抛 `RuntimeError`。`import` 那一行仍走
   `app.test_support.corpus`（**没改**，理由与限制见下面第 5 条）。
3. **文档字符串里的运行命令**：9 个文件的"运行方式 / 用法"都从 `python tests/<file>.py`
   改成 `python scripts/dev/<file>.py`（cwd 仍然是 `backend/`）。
4. **两个按 `__file__` 推路径的写法**（第一批同类问题，见上一节第 1 条）：
   - `test_week12_e2e.py` 的 `PROJECT_ROOT`：上溯两级 → **三级**。否则它会指到 `backend/`
     而不是仓库根，而步骤 1~5 查的东西（git 状态、`.gitignore`、`docker-compose.yml`、
     启动脚本、`README.md`）全在仓库根。
   - `test_full_e2e.py` 的 `RESULT_DIR`：显式指回 `backend/tests/results/`。否则报告会写进
     `backend/scripts/dev/results/`，而**被 `.gitignore` 忽略的是 `backend/tests/results/`**
     （`.gitignore:116`）—— 新目录会在 `git status` 里冒出来，与"该目录未跟踪文件为 0"
     的现状相冲。
5. **⚠️ 直接 `python scripts/dev/xxx.py` 仍然跑不起来，而且这不是本次搬迁造成的**：
   9 个文件都在模块级 `from app.test_support.corpus import ...`，而 `backend/` 并不在
   `sys.path` 上（项目没有 pip 安装成包，`PYTHONPATH` 也是空的）。实测（cwd = `backend/`，
   在旧位置与新位置各放一个内容相同的探针）：

       python tests/_probe_import_A.py        -> ModuleNotFoundError: No module named 'app'
       python scripts/dev/_probe_import_B.py  -> ModuleNotFoundError: No module named 'app'

   **搬迁前后一样失败**（`sys.path[0]` 分别是 `backend/tests/` 与 `backend/scripts/dev/`，
   都不是 `backend/`）。要跑起来需 `PYTHONPATH=backend`，或 `python -m scripts.dev.xxx`。
   所以 `app/test_support/corpus.py:23` 那句"`backend/scripts/dev/*.py` 手动脚本：
   `backend/` 在 path 上（**脚本自己插入**）"对本批 9 个**不成立** —— 它们没有插入语句
   （第一批的 `test_e2e.py` / `test_pdf_pipeline.py` / `test_convert_direct.py` 才有）。
6. **`backend/pytest.ini` 一个字都没改**：它靠 `testpaths = tests` 只收 `tests/`，`scripts/`
   本来就不在其中，所以默认跑法（`python -m pytest`）**从来不会**碰这些脚本。
   ⚠️ 但要说清楚：`norecursedirs`（现为 `tests/integration data data_backup_e2e __pycache__
   .pytest_cache`）**并不含 `scripts`**，因此"显式点名"的跑法仍会去 `import` 它们 ——
   实测 `python -m pytest --collect-only scripts/dev/test_week8_e2e.py` → 模块被导入、
   收集到 0 条、退出码 5。`docs/open-source-readiness.md`（§2.20 的"建议"）里就有
   "并在 `pytest.ini` 里显式排除"这一条；本轮**按"读了再决定"的授权选择不改**
   （默认跑法已由 `testpaths` 兜住，且 `norecursedirs` 对"显式指定文件"本来也不生效），
   要不要加 `scripts` 留给下一次决定。
7. **没有搬任何辅助文件**：`backend/tests/` 下不存在"只被这 9 个使用"的 fixture / 工具模块。
   唯一沾边的是 `backend/tests/results/`（只有 `test_full_e2e.py` 写它）与
   `backend/tests/测试账号信息.md`（描述 `test_full_e2e.py` 建的本地账号）—— 两者都不是模块：
   前者是**被 `.gitignore` 忽略的产物目录**（搬它等于砸掉 `.gitignore:116` 那条规则），
   后者是**文档**且被本 README 与 `docs/**` 引用；所以都留在原地，只把其中指向这 9 个文件
   的**路径引用**改成新位置。

**搬迁后实测（2026-09-23 23:07–23:11，cwd = `backend/`，`ENGRAMNOTE_ALLOW_NETWORK_TESTS=0`）**：

- `python -m pytest -q --collect-only` → **1197 collected、0 errors**。搬迁前同一命令是 1169 ——
  差 **+28**，而 +28 全部来自**同一工作区里被同时新增**的
  `backend/tests/test_security_hardening_batch5.py`（该文件 28 条，`pytest` 逐文件计数可对账）；
  本批 9 个文件在 pytest 眼里本来就是 **0 条用例**，搬走不改变计数。
- `python -m pytest -q` → **1165 passed / 30 skipped / 2 failed**。搬迁前基线是
  **1135 passed / 30 skipped / 4 failed**：passed +30 = 上面新文件的 28 条 + 被同时修好的 2 条
  `.env.example` 一致性用例；剩下的 2 条 `test_cors_middleware_order.py`（CORS 凭证响应头）
  失败**搬迁前后都在**，与本批无关。
- `python -m ruff check app tests scripts` → **All checks passed**。⚠️ 这条**必须**在搬迁后重跑：
  `ruff.toml` 给 `tests/**` 开了 `E722/E402/E712/F841/B007/B011` 豁免，文件一旦离开 `tests/`
  豁免就**不再适用** —— 本批当场带出 **21 条**历史问题，已按**最小改动**修掉（与第一批
  "59 条全部按最小改动修掉、不新增豁免"一致）：8×E722 裸 `except:` → `except Exception:`、
  4×F841 目标名加 `_` 前缀、3×B007 循环变量 → `_wait`、2×E712 `== True/False` → 真值判断、
  2×B011 `assert False, msg` → `raise AssertionError(msg)`。
- 旧路径引用核对：`backend/tests/` 下**已无**指向这 9 个文件旧位置的引用（`测试账号信息.md`
  已同步改成 `backend/scripts/dev/...`）；剩下的引用全在**文档**里（`docs/open-source-readiness.md`（§2.17）、
  `docs/overhaul-plan.md:10711`、`docs/archive/开发时间表.md:150,548,549`）与
  `backend/app/test_support/corpus.py:12` 的一句用法示例，本轮未动（后者在 `app/` 下）。

**遗留（本次搬迁没有解决的）**：

- `backend/tests/integration/` 下另有 **8 个同类脚本**（实测同样 0 条 `def test_`，
  `test_rag.py` 甚至在导入期就发真请求），靠 `pytest.ini` 的
  `norecursedirs = tests/integration` 排除，没有搬走。
- 下列**说明性文字**在本轮之后已与现状不符（且涉及 `app/` 目录，本轮禁改），未动：
  ~~`docs/open-source-readiness.md`（"它们仍会被 import"、`:443` 建目录）~~ ——
  ✅ **2026-09-24 已修**：`docs/open-source-readiness.md` §2.20 的标题与正文已改为
  "曾有 9 个零用例脚本 / ✅ 已搬出"，并注明仍留在 `tests/` 的是 **0 个**（该文件在本次写权限内）。
  其余各处**仍未改**：`docs/overhaul-plan.md:2938`（"仍未做的一半"）、
  `backend/tests/test_rate_limit.py:26`（"其他测试模块 … 也会请求 /auth/login"）、
  `backend/app/test_support/corpus.py:12,23`。

## 遗留

> **2026-09-14 复核：下面第 1、2 条已经收口，第 3、4 条仍未处理。**
> 第 1 条的结论是"**不改 `.gitignore`**"：搬迁提交 `b73ebfd` 已把这 15 个脚本
> （含本 README）全部入库，`git status` 在该目录下**没有任何未跟踪文件**；
> 而那六条根目录规则**刻意保留**为"将来有人往 `backend/` 根丢草稿"的闸门 ——
> 把它们延伸到 `backend/scripts/dev/` 会静默忽略**将来新增**的同名文件
> （完整理由与实测写在 `.gitignore:63-93`，并由
> `backend/tests/test_gitignore_scope.py` 钉住"不许改写成无路径前缀"）。

1. ~~**`.gitignore` 没跟着改**~~ → **已决定不改**（理由见上方引用块）。
   搬迁后的实测状态：`git ls-files backend/scripts/dev` 列出全部 16 个文件，
   该目录下未跟踪文件为 0。
2. ~~**`backend/e2e_cleanup.py` 是唯一被 git 跟踪的文件**~~ → **已随 `b73ebfd` 入库**；
   它在历史里表现为"删除 + 新增"（而不是 rename），这是当时**预期**的结果，
   不是遗漏。
3. **仍然指向旧路径的引用（未处理）**：
   - `backend/scripts/dev/test_full_e2e.py:758,767`（注释与用户可见提示串里的
     `tools/e2e_cleanup.py` / `backend/e2e_cleanup.py`）——
     ⚠️ 2026-09-23 第二批搬迁后路径与行号都变了（原写法 `:753,762` 相对当时的
     实际行号 `:752,761` 就已经少 1）。本轮**只更正引用位置，不改那两处文本**：
     `e2e_cleanup.py` 现在在 `backend/scripts/dev/e2e_cleanup.py`。
   - `backend/tests/测试账号信息.md:35,45`（`python e2e_cleanup.py`）
   - `docs/archive/新手教学.md:4175`（`python test_api.py`）、
     `docs/archive/开发时间表.md:77,514`、`docs/archive/新手教学.md:3709`
   - `docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24`
   - `.trae/documents/**`（迁移计划与审计报告里的路径表）
   以上都是**历史记录或注释**，没有一处是可执行的构建/CI 路径；正确的
   命令现在要写成 `python scripts/dev/<file>.py`（且工作目录仍是 `backend/`，
   因为部分脚本按 cwd 找 `data/db/engramnote.db`）。
4. **疑似死文件（只登记，不删）**：`test.py`（与本项目无关的教学片段）
   与 `restore_note.py`（7 行、写死一个笔记 UUID）。二者除计划文档的清单外
   没有任何引用方；删除与否不是本次搬迁能决定的。
