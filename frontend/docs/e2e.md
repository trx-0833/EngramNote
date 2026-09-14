# 前端 E2E（Playwright）—— 覆盖边界与运行方式

> 对应 overhaul-plan **5.13**（"端到端测试（Playwright）"）。
> 本文件记录的是**已经跑起来并真实通过**的一层，不是计划。

---

## 1. 怎么跑

```bash
cd frontend
npm run e2e            # 无头，一条命令自洽
npm run e2e:headed     # 带界面，调试用
```

开发服务器**由 Playwright 自己拉起和关闭**（`playwright.config.ts` 的 `webServer`）。
使用者不需要先开 `npm run dev`，也不需要在跑完后再关掉它。

- **端口：默认 4319**（刻意避开人类手上 DSH Web GUI 的 3080，也避开 Vite 默认的 5173），
  **可用 `E2E_PORT` 换掉**（见 §1.1）。
  配了 `--strictPort`：端口被占时**直接失败**，不会悄悄换端口 ——
  悄悄换端口会让 `webServer.url` 的健康检查去探一个空地址，
  表现为"超时 120 秒后报一句看不懂的错"。
- `reuseExistingServer: false`：不复用已存在的服务。
  复用会让"测的到底是哪份代码"不可知 —— 上个会话留下的 dev server
  可能跑的是改动前的模块图。

### 1.1 并发运行：`E2E_PORT`（默认 4319，行为不变）

```bash
E2E_PORT=4320 npm run e2e                    # bash / CI
```

```powershell
$env:E2E_PORT = 4320; npm run e2e             # PowerShell
```

**为什么需要它。** 默认端口固定 + `--strictPort` + `reuseExistingServer: false`
三条叠在一起，**第二个并发运行必然失败**，而且失败的样子极容易读错。
实测过一次：两个 agent 同时跑 `npm run e2e`，第二个的输出只有一行

```
Error: http://127.0.0.1:4319 is already used, make sure that nothing is running on
the port/url or set reuseExistingServer:true in config.webServer.
```

—— **连测试汇总行都没有**（Playwright 在起 `webServer` 那一步就退出了），
看起来像"这次跑挂了 / 我改的代码有问题"，真实原因却是"端口被另一个
Playwright 占着"，两份代码可能都没问题。这种**假阴性**比真缺陷更费时间：
它会把排查引向产品代码。

换端口只影响 dev server 与 `baseURL` 的端口，**用例一行都不用改**
（用例里的地址全部是相对路径，走 `baseURL`；实测 `E2E_PORT=4321`
在 4319 被别的进程占着的情况下仍然 10 passed）。

**3080 被写死在禁止列表里**（`playwright.config.ts` 的 `FORBIDDEN_E2E_PORTS`）：
`E2E_PORT=3080` 不会去占那个端口，而是**在配置加载阶段直接抛错、一行测试都不跑**：

```
Error: E2E_PORT=3080 是人类正在使用的 DSH Web GUI 端口，E2E 绝不能占用它。
换一个高位端口（默认 4319，例如 4320）。
```

非数字与越界的值同样在加载阶段报错（`E2E_PORT="abc"`、`E2E_PORT=70000`）。
理由和上面两条是同一条：**这一层的每一种配置错误都必须响亮** ——
占错端口的后果（把人类正在用的界面顶掉）比这次测试失败严重得多。

**CI 不需要设置它**：每个 job 一台机器、独占一个 runner，默认 4319 就是对的。

### 浏览器：只装了 Chromium

| 项 | 值 |
|---|---|
| Playwright | `@playwright/test` 1.63.0（`devDependencies`，锁 ^1.63.0） |
| 浏览器 | **仅 Chromium**，revision `1243`（Chrome for Testing 153.0.8010.12） |
| 位置 | `C:\Users\admin\AppData\Local\ms-playwright\chromium-1243`（431.9 MB）<br>`…\chromium_headless_shell-1243`（270.1 MB） |
| 下载量 | 两个 zip 合计 **310.2 MiB**（chrome-win64 195.6 MiB + chrome-headless-shell 114.6 MiB） |
| 解压后占用 | 磁盘净增 **702.0 MB** |
| 未安装 | Firefox / WebKit / `chromium-tip-of-tree`（未安装，也不打算装） |

`playwright install-deps` **不需要**：那是 Linux 上补齐系统库用的，Windows 无对应步骤。

⚠️ **本机的一个网络事实**：`cdn.playwright.dev` 在这个网络下**完全下不动**
（实测下载文件停在 0 字节，10 分钟无进展）。改用
`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`
后 103 秒下完。这个环境变量**只在安装浏览器时需要**，跑测试不需要，
所以没有写进任何脚本或配置 —— 换台机器重装时按需临时设置即可：

```powershell
$env:PLAYWRIGHT_DOWNLOAD_HOST = 'https://cdn.npmmirror.com/binaries/playwright'
npx playwright install chromium
```

---

## 2. 这一层到底测了什么

4 个 spec 文件、**10 条用例（全部执行）**，全部对 Chromium 跑：

| 文件 | 用例 | 证明的事 |
|---|---|---|
| `e2e/app-shell.spec.ts` | 2 | `index.html` 返回 200、React 真的挂载、无未捕获异常；**全局 CSS 令牌层与 CSS Module 层都作用到了元素上**（用计算后的布局值判定，不是拼类名） |
| `e2e/auth-routing.spec.ts` | 2 | 未登录直接访问 `/`、`/notes`、`/graph`、`/upload`、`/review/cards` 都拿到 **200 + 登录页**（SPA fallback 生效）；登录页 → 注册页是客户端路由而非整页刷新 |
| `e2e/login-form.spec.ts` | 2 | 浏览器**原生约束校验**拦下空表单与非法邮箱，且**一个 `/api` 请求都没发出去** |
| `e2e/login-api.spec.ts` | 4 | 凭据正确 → 一对令牌落盘 → 切到已登录外壳 → Dashboard 渲染；凭据错误 → 统一文案、不写令牌、留在登录页；**从 `/login` 登录成功 → 地址栏变成 `/` 且渲染 Dashboard（不是 404）**；已登录后访问 `/login`、`/register` → 重定向到 `/` |

### 关键设计取舍

- **第三方外链一律 abort**（Google Fonts）。`index.html` 里有
  `<link href="https://fonts.googleapis.com/...">`，而浏览器在样式表解析完之前
  **不渲染**。断网时页面会停在"HTML 到了、元素一直不出来"，失败信息指向
  「登录框没出现」，与真实原因（字体 CDN 不通）毫无关系。
- **`/api` 被桩掉**（`e2e/support.ts` 的 `stubApi`）。所以这一层证明的是
  **前端链路自洽**：表单 → `api/client` → 令牌持久化 → 路由切换 → 外壳渲染。
  它**不**证明后端会这样响应 —— 接口契约由 `backend/tests/` 负责。
- **只收 `pageerror` 断言为空**，不收 `console.error`：后者会把 React 的正常告警
  也算进来，断言 `[]` 就成了维护负担而不是信号。

---

## 3. Vitest（`npm test`）已经覆盖什么

20 个文件 / 273 条用例，跑在 Node + jsdom 里。它是**阻断性**的（CI 里 `npm test` 失败即失败）。
覆盖面：

- 纯函数与工具：`utils/citationJump`、`pages/knowledgegraph/normalize`、
  `pages/projects/helpers`、`styles/mobile-input-font-size`（读 `main.tsx` 的 import 顺序
  拼出真实级联再断言）
- 组件行为：`NoteAskPanel`、`QuizAnswerCard`、`ReviewProgress`、`SelfRatingButtons`、
  `SourceContext`、`useReviewKeyboard`、`Sidebar`、`TaskProgress`、`Toast`
- 页面与数据流：`NoteDetail`（10 条）、`KnowledgeGraph`、`Projects`、`Review`、
  `CardReview`、`App`（移动端汉堡菜单 4 条）
- API 客户端：`api/client.test.ts`（401 → 刷新一次 → 重放一次、超时、错误归一化）

**它的运行方式决定了它的盲区**：所有 API 模块都被 `vi.mock` 掉；没有布局引擎；
不加载 `index.html`；不解析 CSS；`getComputedStyle` 只回字符串不做布局；
不实现 HTML 约束校验（`matches(':invalid')` 恒 false、`validationMessage` 恒空串）。

---

## 4. 两边都覆盖不到的（不要假装覆盖了）

1. **真实后端契约**：本层把 `/api` 全桩掉了。请求/响应字段是否真的对得上，
   只有 `backend/tests/` 与真跑一次全栈才能验证。
   > **2026-09-14 更新**：计划书里那句"上传→理解→复习→问答"的全链路
   > **已经做出来并且真跑过了** —— 见下面的 **§8**。§4 剩下的 2–9 条仍然成立。
2. **核心业务链路的端到端**（计划书里写的"上传→理解→复习→问答"）：
   这需要真实后端 + 真实数据 + LLM，**本轮没有做**。现在有的是"外壳 + 认证入口"级别的冒烟。
3. **响应式与真机**：本层固定 `1280×720`。5.10 的移动端布局（≤768px 抽屉、
   触控尺寸、安全区）没有被 E2E 覆盖 —— `App.test.tsx` 只钉了"点了之后状态怎么变"，
   **没有任何一层验证过手机上看起来是对的**。
4. **可访问性**（5.9）：本层（`npm run e2e`）**不含** axe 扫描 —— 审计是另一个
   project（`npm run a11y`，`@axe-core/playwright` + 真 Chromium），
   计数与边界见 [`a11y-audit.md`](./a11y-audit.md)。它已经报出 30 组
   "规则 × 场景"违规，所以"没报出来"不等于"没问题"；而**键盘焦点顺序、
   屏幕阅读器语义、动态区域、渐变背景上的对比度、触控目标尺寸**
   连 axe 也判不了，那几项仍然没有任何一层在守。
5. **视觉回归**：没有截图基线，CSS 改动导致的视觉变形（如 5.6 的级联反转）仍无自动守护。
6. **跨浏览器**：只装/只跑 Chromium。Safari/Firefox 的差异未验证。
7. **性能**：没有预算、没有 Lighthouse、没有 chunk 体积门禁（`chunkSizeWarningLimit` 只是警告）。
8. **SSE 流式问答**：`useStreamAnswer` 在 Vitest 里有单元级覆盖，
   但"真浏览器里 SSE 分块到达并逐字渲染"没有任何一层验证。
   > **2026-09-14 更新**：`e2e:full` 的第 ⑤ 条现在真的读 SSE 帧
   > （107 个 `token` 事件、`done` 事件、界面文本与流文本一致），见 §8。
9. **`localStorage` 跨标签/跨刷新**：只在单个页面上下文里验证过。

---

## 5. 本轮 E2E 发现的**产品缺陷**（已修复）

### 从 `/login` 登录成功后会落到 404

**复现（每一步在真实使用里都走得到）：**

1. 打开 `/`（未登录 → 渲染登录页）
2. 点页脚「注册」→ 客户端路由到 `/register`
3. 点页脚「登录」→ 客户端路由到 `/login`
4. 输入**正确**凭据并提交
5. 修复前：登录成功、令牌落盘、已登录外壳出现 —— 但 `main` 里是 **404「页面不存在」**

**原因**：`App.tsx` 的已登录路由表里**没有 `/login`**（它只在未登录分支注册），
而登录成功后没有任何"跳到 `/`"的动作，于是 `location.pathname` 仍是
`/login`，落到 `path="*"` 的 `NotFound`。用户必须手动把地址改回 `/`。

**修法（两半都修，只修一半等于把同一个洞留给另一个入口）：**

| 位置 | 改动 |
|---|---|
| `src/pages/Login.tsx` | 登录**成功**后 `navigate('/')`（放在 `await login(...)` 之后、`catch` 之外：401 仍然留在登录页显示统一文案） |
| `src/App.tsx` | 已登录分支里给 `/login`、`/register` 各加一条 `<Route element={<Navigate to="/" replace />} />` —— 令牌长期有效，用户完全可能带着已登录状态回到这两个地址（历史记录、书签、注册后回退） |

**发现它的是哪条用例**：`login-api.spec.ts` 里原本的 `test.fixme`
（用例体断言的是**期望行为**：能看到仪表盘）。修好后它变回真用例，
并补了一条"已登录后直接访问 `/login`、`/register`"的用例守住第二半。

这条缺陷在 Vitest 里**永远看不见**：jsdom 里没有真实地址栏、没有真实
history、没有 SPA fallback，路由是在 mock 出来的路径上跑的。
它也是本层值得做成**阻断性**门禁的直接证据（见 §7）。

---

## 6. 实现时踩到、值得记住的两个坑

### 6.1 不要用 `/api/` 子串判断"这是后端请求"

路由最初写成 `page.route('**' + '/api/' + '**', …)`。Vite 开发服务器从
**源码路径**提供模块，模块地址长这样：

```
http://127.0.0.1:4319/src/api/client.ts
http://127.0.0.1:4319/src/api/goals.ts
```

这些地址里**同样含有 `/api/`**，于是整批 ES 模块被桩响应顶掉：
`page.goto` 成功（HTML 拿到了），但 React 永远挂不上，`#email` 一直等不到 ——
报错指向"登录框没出现"，与真实原因（应用自己的模块被自己的桩吃掉）毫无关系。
同一个坑还让"校验没过就不该发请求"那条断言把 12 条模块请求当成了 API 请求。

现在一律用 URL 判定函数：`url.pathname.startsWith('/api/')`
（`e2e/support.ts` 的 `isApiUrl`）。

### 6.2 通配 glob 写进块注释会把注释提前终止

`**` 紧跟 `/` 的两个字符**就是块注释的结束符**。把那种 glob 原样写进
`/** … */` 说明里，注释会在那里结束，后面的中文变成代码，报错是
`Cannot find name 'api'` —— 指向一句中文说明，完全看不出跟注释有关。
`support.ts` 里为此把 glob 拆成三段写，并在注释里留了说明。

---

## 7. 与 CI 的关系

**本层已接入 `.github/workflows/ci.yml` 的 `frontend` job，并且是阻断性的。**

- `frontend` job 在 `npm run build` 之后增加三步：
  `actions/cache@v4`（缓存 `~/.cache/ms-playwright`，键绑 `package-lock.json`）、
  `npx playwright install --with-deps chromium`（**只装 Chromium**）、
  `npm run e2e`（`timeout-minutes: 10`）。
- **不需要**在 CI 里另外起服务：dev server 仍由 `playwright.config.ts` 的
  `webServer` 自动拉起/关闭，端口 4319（默认值；runner 独占机器，不需要设
  `E2E_PORT`）、`reuseExistingServer: false`。
- 缓存键绑定锁文件是有意的：Playwright 的浏览器 revision 跟着
  `@playwright/test` 版本走，键不随版本变会出现"缓存里是旧 revision"。

离线环境的一个已知事实：本机 `cdn.playwright.dev` 下不动（见 §1）。
GitHub runner 不受影响；若换到同样受限的网络，需要设
`PLAYWRIGHT_DOWNLOAD_HOST`，那是环境变量而不是配置项，所以没有写进 workflow。

`e2e:full`（§8）**没有**接入 CI，而且是刻意的：它要联网调真实 LLM、要花钱、
要几分钟。详见 §8.4。

---

## 8. 全链路层：上传 → 理解 → 复习 → 问答（`npm run e2e:full`）

> 对应 overhaul-plan **5.13 的后半段**。前三层（`e2e` / `a11y` / Vitest）
> 证明"界面没写错"，这一层证明**这条链路真的能跑**：真实 uvicorn、真实
> Celery worker、真实 PostgreSQL…不，是真实 SQLite 库 + 真实 LLM。
>
> 本节记录的是**已经真跑过**的结果（含失败与三个被它抓出来的探针缺陷），
> 不是计划。原始证据落在 `frontend/test-results-e2e-full/evidence.json`。

### 8.1 怎么跑

```bash
cd frontend
ENGRAMNOTE_E2E_FULL=1 npm run e2e:full          # 真实链路（要联网、要额度、分钟级）
ENGRAMNOTE_E2E_FULL=1 npm run e2e:full:probe    # 只跑探针（不碰后端，秒级）
```

```powershell
$env:ENGRAMNOTE_E2E_FULL = '1'; npm run e2e:full
```

**必须显式打开 `ENGRAMNOTE_E2E_FULL=1`。** 没有它：

- `e2e-full` / `e2e-full-probe` 两个 project **根本不注册**
  （`playwright.config.ts` 的 `E2E_FULL_ENABLED`），因此
  `npm run e2e` / `npm run e2e:all` **既不会跑它们、也不会收集它们**；
- `webServer` 也不会启动真实后端。

这条门禁是**必须**的：`npm run e2e` 在 CI 里是阻断性的，而 CI 上没有密钥、
没有额度、也没有网络策略保证能出网。一条会花钱、要几分钟的用例混进那一层，
结果一定是那一层被加 `|| true` 或没人再跑。

#### 8.1.0 ⚠️ 光有"project 门禁"不够：匹配式会把它们收走（真实事故）

第一版接线只加了 project 注册门禁，结果**默认的 `npm run e2e` 被污染**，实测：

```
npm run e2e  →  1 failed / 5 did not run / 16 passed      ← 错
```

原因：顶层与功能层用的那个"除 a11y 之外全部"的**取反匹配**里，`*` 匹配的是
**任意字符串**，所以 `e2e-full.spec.ts`、`e2e-full-probe.spec.ts` 一样落进
`chromium` project。它们在没有 opt-in 时被收集并执行，连不上 4381 的前端与
4382 的后端，于是失败 —— 而这一层是 **CI 的阻断层**，每次 PR 都会因为这个与
产品无关的原因变红。

现在两道门禁都在：

1. 两个全链路 project 只在 `ENGRAMNOTE_E2E_FULL=1` 时注册；
2. `chromium` project 用 **`testIgnore: ['**/e2e-full.spec.ts',
   '**/e2e-full-probe.spec.ts']`** 明确排除。

实测（无 opt-in）：

```
npm run e2e -- --list  →  Total: 10 tests in 4 files     ← 只收功能层的 4 个文件
npm run e2e            →  10 passed (15.5s)  0 failed  0 flaky  0 skipped
```

**反面做法**是把两个文件改名到 `*.spec.ts` 之外 —— 但 `testDir` 下的命名约定是
"哪些文件是用例"的唯一线索，为匹配式的边界改名会让约定本身变模糊。
匹配式的问题用匹配式解决。

#### 8.1.1 端口（全部可覆盖）

| 用途 | env | 默认 |
|---|---|---|
| 前端（Vite dev） | `E2E_FULL_PORT` | 4381 |
| 后端（uvicorn） | `E2E_FULL_API_PORT` | 4382 |
| 后端监督器（就绪探针） | `E2E_FULL_CONTROL_PORT` | 4383 |
| 一次性目录（库/存储/broker/日志） | `ENGRAMNOTE_E2E_TMP` | `%TEMP%\engramnote-e2e-full` |
| 后端解释器（可选） | `ENGRAMNOTE_E2E_PYTHON` | 自动探测 |
| 跑完保留临时目录（排查用） | `ENGRAMNOTE_E2E_KEEP` | 关 |

3080（人类手上的 DSH Web GUI）与功能层的 4319 都不在这三个里。
`E2E_FULL_PORT=3080` 会在**配置加载阶段**直接抛错，一行测试都不跑
（与 `E2E_PORT` 同一套 `FORBIDDEN_E2E_PORTS` 守卫）。

#### 8.1.2 为什么 `webServer` 是一个**数组**

一次真实链路要三个进程，而 `webServer.command` 只接受一条命令：

1. **Vite（4381）**，且 `VITE_API_TARGET` 指向这一层的后端 ——
   不指的话代理会把 `/api` 打到 8001 上那个（人类正在用的）后端；
2. **后端监督器（`backend/scripts/_e2e_full_runner.py`）**，它自己再拉起
   uvicorn（4382）与 Celery worker；
3. 监督器同时开一个**控制口（4383）**，`webServer.url` 探的是它。

**探针 URL 为什么不直接指向 uvicorn**：`webServer` 只能探一个地址，
而这条链路要等的不止 uvicorn —— 还有 worker 连上 broker。若在 uvicorn 一监听
就返回 200，测试会在 worker 还没订阅时开始上传，任务投出去没人接，
表现为"转换超时"，与真实原因（worker 没起来）毫无关系。因此监督器等两件事：

- `GET http://127.0.0.1:4382/health` 返回 200；
- worker **自己的日志**里出现 Celery 的 ready 横幅（`celery@HOST ready.`）。

两条都满足后才让控制口返回 200。

### 8.2 一次性数据库 / 存储 / broker：`backend/data/**` 一个字节都不写

| 数据 | 去哪 | 怎么做到的 |
|---|---|---|
| 数据库 | `<tmp>/db/engram.db` | `DATABASE_URL`（`app/config.py` 的正式入口） |
| 原始文件 + Markdown（vault） | `<tmp>/vault/` | `STORAGE_DIR` / `VAULT_DIR` |
| 日志 | `<tmp>/logs/` | `LOG_DIR` |
| Celery broker / 结果后端 | `<tmp>/celery/{broker,results}` | **`backend/scripts/_e2e_full_bootstrap.py`**（见下） |
| 两阶段上传暂存 | `<tmp>/tmp/upload/` | 同上 |

**为什么 broker 需要一个后端脚本**（这是本轮唯一动到 `backend/` 的原因）：

`Settings.get_celery_broker_dir()` / `get_celery_result_dir()` 返回
`DATA_DIR / "celery" / ...`，而 `DATA_DIR` 是 `backend/data`（**硬编码，
没有环境变量入口**）。`app/tasks/celery_app.py` 在**模块层**就把这个值写进
Celery 配置了。于是"只设 `DATABASE_URL`"的 E2E 后端，任务仍会被投递到
**真实库那套 broker 目录**；此时只要人类的 `start.bat` 起着 worker
（`celery -A app.tasks.celery_app worker --pool=solo`），它就会**抢走** E2E 的
任务，并用**真实数据库**执行 —— 一次 E2E 就能污染生产知识库。
文件系统 broker 是"谁先 rename 谁拿到"，没有任何所有权标记。

`backend/scripts/` 下四个文件（**只在 E2E 时被调用，生产启动路径不经过它们**）：

| 文件 | 作用 |
|---|---|
| `_e2e_full_bootstrap.py` | 在**任何 `app.*` import 之前**把 broker/结果/暂存目录换到临时根；未设置开关时是空操作并返回 False |
| `_e2e_full_app.py` | uvicorn 的 `--factory` 目标：先引导、再 `from app.main import app` |
| `_e2e_full_worker.py` | worker 启动器：先引导、再 import 任务模块（`@celery_app.task` 注册到**同一个** app）、然后把**这个 app 的** broker/backend 改到隔离目录、最后走 `celery` 命令行入口 |
| `_e2e_full_runner.py` | 监督器：拉起上两个进程、探就绪、把生效的 LLM 路由（不含密钥）打进日志、退出时回收子进程 |

为什么不改 `app/config.py` 加一个环境变量：那会动到**生产启动路径**的配置面，
而这一层只需要"测试时换目录"。隔离逻辑留在测试侧，产品代码零改动。

**`DATA_DIR` 本身刻意不挪**：`services/embedding_service.py` 从
`backend/data/models` 读已缓存的 bge-m3（~2.2GB）。挪走 DATA_DIR 会让清洗阶段
退化成"无模型兜底去重"、问答退化成 BM25-only —— 那测的就不是真实链路了。

**用例自己校验隔离**（第 ⑥ 条）：真实库 `backend/data/db/engramnote.db` 的
`mtime` 在跑前跑后必须一致，且临时库必须真的存在（否则"没碰真实库"可以因为
"根本没跑"而成立）。

### 8.3 它到底断言了什么（6 条用例，`workers: 1` + `serial`）

| # | 用例 | 断言的是**真实产出**，不是"没报错" |
|---|---|---|
| ① | 注册登录 | 走真实 `/register` 表单；注册后侧边栏（已登录外壳）出现；`localStorage` 里的令牌能通过 `GET /api/auth/me` |
| ② | 上传 → 转换 → 清洗 | 用 **DataTransfer 触发真实 `drop` 事件**（与用户拖文件同一条链路）→ 出现"上传设置"卡片 → 点"确认上传" → 盯**产品自己的状态文案**（`状态: …` 轮询 / `转换完成！`）→ 自动跳转到 `/notes/<uuid>` → 详情页真的渲染出标题与正文段落 |
| ③ | 理解管道 | 触发理解后轮询 `GET /understanding/{id}/status` 直到 `archived`，并记录**观察到的状态序列**；断言卡片数 > 0、题目数 > 0；再到 `/cards` 页确认这篇笔记的卡片**看得见** |
| ④ | 复习 | 先查 `/review/due`（为空即失败，不能跳过）→ 打开 `/review` → 按题型作答（选择/填空/简答三种都覆盖）→ 提交 → 断言判分区出现（`回答正确`/`回答错误`/`请自己打分`）→ 复习历史里真的有这条记录 |
| ⑤ | 问答（SSE） | 在页面里包一层 `fetch` 旁路 SSE 响应体（`Response.clone()`，应用读原流、探针读副本），逐帧统计：`token` 事件数、`done`、`error`、首末 token 时间差、拼接后的文本；再断言**界面文本包含流文本**、`引用来源` 出现、`由 DeepSeek 提供支持` 出现 |
| ⑥ | LLM 记账 + 隔离 | `/api/llm/usage` 的调用数 > 0 且失败数 = 0（降级/兜底路径不会有这些行）；真实库 mtime 不变、临时库存在、`backend/data/tmp/upload` 的**条目集合前后一致** |

> ⚠️ 第 ⑤ 条的 `token` 事件数**每次都不一样**：实测 11 / 81 / 107 / 110 个。
> 上游网关有时把整段回答合成很少几个帧推下来（那次 11 个帧只用了 25ms），
> 所以"是流式"的判据是 **`token` 事件 ≥ 2 且与 `done` 成对**，
> 不是"token 事件要多"。想稳定观察到"界面文本逐段增长"就需要让模型慢下来
> （延迟是真钱），因此那条只作证据记录、不作硬断言 —— 这一点写在这里，
> 是为了避免以后有人把它"顺手"改成硬断言又说不清为什么不稳。

### 8.4 真实跑过：结果与路由

**跑法**：`ENGRAMNOTE_E2E_FULL=1 npm run e2e:full`（2026-09-14）。
**生效的 LLM 路由**（监督器启动时打进日志，来自 `backend/.env`，不含密钥）：

```
[e2e-full] LLM 路由（来自 backend/.env）| provider=deepseek |
           base_url=https://opencode.ai/zen/go/v1 | model=deepseek-v4-flash | key=已配置(len=67)
[e2e-full] APP_ENV=prod | llm_max_rpm=10
```

即 **OpenCode 的网关**（`https://opencode.ai/zen/go/v1`），**不是**
`api.deepseek.com`。变量名里的 "DEEPSEEK" 只是历史命名。

**每一步的真实数字**（取自 `test-results-e2e-full/evidence.json` 与后端日志）：

| 步骤 | 结果 | 证据 |
|---|---|---|
| ① 注册 | PASS（2–7 秒） | `/api/auth/me` 200，返回 user id |
| ② 上传 + 转换 | PASS（25–36 秒） | 笔记状态 `converting → cleaning → cleaned`；正文出现在详情页 |
| ③ 理解 | PASS（38 秒 ~ 5.5 分钟） | 状态 `learning → archived`；**卡片 10 / 16 / 18 张，题目数与卡片数一致** |
| ④ 复习 | PASS（2–3 秒） | `is_correct=false`（选项作答）、`grading_method=choice`、`next_review_at` 已推进 |
| ⑤ 问答 | PASS（**11 / 81 / 107 / 110 个 token 事件**，流时长 25ms ~ 699ms，流文本 91~212 字） | `meta.retrieval_status=hybrid`、`sources=1`、`done=true`、`error=null`、界面文本包含流文本 |
| ⑥ 记账 + 隔离 | PASS | 记账里同时出现 `extract_knowledge`（理解/出题）与 `rag_answer_stream`（问答），失败数 0；真实库 mtime 前后一致；`backend/data/tmp/upload` 条目集合前后一致（空） |

清洗阶段会在 worker 里加载 bge-m3：日志实证
`嵌入模型加载成功: BAAI/bge-m3（可用内存 3.6GB）`，首次清洗因此约 55 秒；
之后同一 worker 进程复用已加载的模型。三次完整运行的实测耗时分别是
**3.2 分钟 / 1.4 分钟**（成功）与 **27.6 分钟**（因定位式缺陷挂到用例超时）。

### 8.5 ★ 探针层：`npm run e2e:full:probe`（这一层的"元测试"）

第 ⑤ 条要从 DOM 里读出 AI 回答。**这个读取动作本身被写错了三次，每次花掉一整条
链路**（含真实 LLM 调用、20 多分钟），而报错信息全都指向产品代码：

| 写法 | 实测症状 | 真相 |
|---|---|---|
| `filter({ has: getByText(question) }).last()` | "界面上没有渲染出答案（0 字）—— 后端流了但 UI 没显示" | `getByText` 返回**最内层**元素 = 问题气泡自己，读到的文本被 `.replace(question,'')` 清空 |
| `getByText(整句问题, {exact:true}).locator('..')` | `locator.innerText: Test timeout of 1500000ms exceeded`（**挂满 25 分钟**） | `locator.innerText()` 用的是**用例超时**，不是 `expect` 的 60 秒 |
| `filter({ has })` 嵌套 | 读到 `"提问"` | `has` 会让**祖先**也满足条件，`.first()`/`.last()` 取到的是输入卡片 |

因此把"这个选择器到底选到了什么"从昂贵链路里抽出来，做成
**不需要后端、不花钱、秒级**的探针（`e2e-full-probe.spec.ts`，6 条用例，
5 秒跑完）：用与 `src/pages/QA.tsx` 的 JSX **逐层对应**的静态 HTML，
断言定位式读到的是答案、不是问题气泡、也不是输入卡片；断言"还没有这轮记录时
**有上限地**返回 null"；断言失败信息里带现场（`div.card=… / 正文前 300 字=…`）。

选择器的唯一来源是 `e2e-full-locators.ts`（探针与链路共用一份定义）。
**判据写在页面里**（"哪个 div 的**直接文本**等于问题"只有唯一答案），
而不是拼 Playwright 的嵌套 `filter`。

**它是这样抓到第三个错的**：第三条错误就是探针第一次运行时报出来的，
证据是 `Received string: "提问"`。

### 8.6 这一层**仍然**不覆盖什么

1. **PDF / Office / 音视频的上传链路**：本轮上传的是 `.md`（转换阶段直接读文本）。
   `pdf/docx/pptx/xlsx` 要走 MinerU、音视频要走 ASR —— 那两条没被这一层覆盖，
   而它们才是转换链路里最慢、最容易坏的部分。
2. **只跑一个后端进程**：`uvicorn --workers 1` +
   `celery worker --pool=solo`。多进程/多 worker 的行为（以及
   `_enforce_single_writer` 守的那个 SQLite 单写者假设）没有覆盖。
3. **不测失败路径**：LLM 超时/限流/配额耗尽、转换失败、清洗降级
   （`dedup_mode=lightweight`）、SSE 中断 —— 一条都没有。这一层验的是
   "顺利情况下链路通"，不是"出错时的行为"。
4. **向量检索通道**：`GET /ask/stream` 的 `meta.retrieval_status` 实测是
   `hybrid`（词法命中 + 编码成功但向量无命中），不是 `full_vector`。
   原因是 `rag_service._encode_via_celery` 给 Celery 结果只等 **10 秒**，
   而 worker 首次要加载 bge-m3（数十秒）→ 超时 → 降级为仅 BM25。
   也就是说**这条链路里向量通道没被真正验证过**。这是产品侧的真实约束
   （见 `_encode_via_celery` 的说明），不是本层的缺陷，但读这一层的绿灯时要知道。
5. **UI 细节**：不做视觉回归；不跑 axe（那是 `a11y` 层）。
6. **性能**：只记录耗时（证据 JSON 里有时间戳），没有阈值、没有门禁。
7. **多用户 / 权限隔离**：只注册一个账号。
8. **幂等与重跑**：`llm_cache_enabled` 默认开着，重跑同一篇资料时理解阶段会
   命中缓存（`llm_calls` 里记 `cached=True`），因此"第二次跑更快"不代表链路更快。

### 8.7 它花了多少钱、多久

- 一次完整运行：**约 1.4–3.2 分钟**（成功运行的实测）；另有一次因定位式缺陷
  挂到 25 分钟的用例超时。
  理解阶段是耗时大头：38 秒 ~ 2.2 分钟（取决于模型延迟与题量）。
- 一次完整运行的 LLM 用量（实测，来自 `/api/llm/usage`）：
  理解/出题约 2.2–5K token，问答约 0.7K token，**合计每次 < 10K token**。
- `backend/.env` 里**没有配单价**（`LLM_PRICE_*` 为空），因此
  `llm_calls.cost` 记的是 `NULL`、聚合为 0 —— **不要**把那个 0 读成"没花钱"
  （`/api/llm/usage` 会同时返回 `price_configured=false` 提醒这一点）。

### 8.8 与 CI 的关系：**刻意不接入**

三条独立的理由，任何一条都足够：

1. **没有密钥**：CI 上 `DEEPSEEK_API_KEY` 不存在（`backend/.env` 被 gitignore），
   链路第一步就会失败；
2. **要花钱**："每次 CI 都调一次真实 LLM"不是一个可以默默打开的开关；
3. **时间**：分钟级，而现有的 `frontend` job 已经有 10 分钟上限。

要接的话，正确的形态是**单独的、手动的、非阻断的** job（`workflow_dispatch`
+ 仓库 secret），而不是把它塞进 PR 门禁。
