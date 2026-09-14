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
