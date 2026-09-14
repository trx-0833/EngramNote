# `backend/scripts/dev/` —— 一次性开发脚本（历史归档）

**2026-09-14 建**：把 `backend/` 根目录的 **15 个一次性脚本**移进来，
执行的是 `docs/overhaul-plan.md` 的**计划项 0.2**（该行的状态随之从
"🟡 守卫已落地，脚本搬迁未做"改为"✅ 已落地"；审计登记见该文件附录 BJ）。

**这些脚本不是测试套件的一部分，也不保证今天仍能跑通**：它们是当时某次
联调 / 排障用的现场脚本，多数硬编码了真实笔记 ID、绝对路径或
`localhost:8000`。`pytest` **不会**收集它们（`backend/pytest.ini` 的
`testpaths = tests`）。它们被保留是为了**可追溯**，不是为了被再次调用。

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
| `e2e_cleanup.py` | 清理 E2E 遗留测试账号及其数据（绝对路径） | `backend/tests/test_full_e2e.py:753,762`（注释与提示串）、`backend/tests/测试账号信息.md:35,45`、`docs/code-review-report.md`、`docs/verification-report-20260830.md:24` | **已跟踪**（唯一一个） |
| `reset_cleaning.py` | 把卡在 `cleaning` 的笔记重置为 `converted`（绝对路径） | `docs/archive/开发时间表.md:77,514`、`docs/archive/新手教学.md:3709`、`docs/overhaul-plan.md` 清单 | 被忽略（`:68`） |
| `restore_note.py` | 7 行：把**某一条**硬编码 UUID 的笔记改回 `cleaned` | `docs/overhaul-plan.md` 清单 | 被忽略（`:69`） |

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

## 遗留（**没有**在本次搬迁中处理，留给人工决定）

1. **`.gitignore` 没跟着改**：`.gitignore:63-69` 用 `backend/test_*.py` /
   `backend/verify_*.py` / `backend/reset_*.py` / `backend/restore_*.py` /
   `backend/test.py` 精确匹配**根目录**。文件挪到 `backend/scripts/dev/` 后
   这些规则**不再命中** —— 这 14 个文件现在会以未跟踪文件的形式出现在
   `git status` 里。是提交它们（推荐：归档脚本本就该入库）、还是补一条
   `backend/scripts/dev/` 的忽略规则，属于人工决定，本次**未**改 `.gitignore`。
2. **`backend/e2e_cleanup.py` 是唯一被 git 跟踪的文件**：它的移动在
   `git status` 里表现为"删除 + 新增未跟踪文件"，而不是 rename ——
   本次**没有**动 git 索引（不提交、不 `git add`），由人工决定何时提交。
3. **仍然指向旧路径的引用（本次未改，因为不在允许改动的文件范围内）**：
   - `backend/tests/test_full_e2e.py:753,762`（注释与用户可见提示串里的
     `backend/e2e_cleanup.py`；`backend/tests/**` 本次只许改缺陷 1 的那一行）
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
