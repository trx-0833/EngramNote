/**
 * 全链路 E2E：**上传 → 理解 → 复习 → 问答**（overhaul-plan 5.13 的后半段）
 *
 * ## 这一层与另外两层的区别（先把它说清楚，否则很容易被当成重复劳动）
 *
 * | 层 | 命令 | 后端 | LLM | 断言的是什么 |
 * |---|---|---|---|---|
 * | `chromium`（5.13） | `npm run e2e` | 全部 `page.route` 桩掉 | 无 | 前端链路：表单 → api client → 路由切换 → 外壳渲染 |
 * | `a11y`（5.9） | `npm run a11y` | 桩掉 | 无 | 可访问性违规有没有超出登记表 |
 * | **`e2e-full`（本文件）** | `npm run e2e:full` | **真实 uvicorn + Celery worker** | **真实（OpenCode 网关）** | 上传的文件真的变成了卡片与题目、复习真的推进了调度、SSE 真的吐出了 token |
 *
 * 前两层能证明"界面没写错"，证明不了"这条链路真的能跑"——
 * 上一轮审计就是靠"真的问了一次"才发现问答页每次提问都直接失败的
 * （读取器被 `getReader()` 两次，见 `docs/a11y-audit.md` §10）。
 *
 * ## 为什么默认不跑（`ENGRAMNOTE_E2E_FULL=1` 才跑）
 *
 * 这条链路**要花钱、要联网、要几分钟**，CI 上没有密钥：
 *
 * 1. 理解管道会对每个章节调 LLM（摘要 + 抽卡），题目按卡片逐条生成；
 * 2. 清洗阶段要在 worker 里加载 bge-m3（~2.2GB）；
 * 3. 首次上传的端到端耗时是**分钟级**，不是秒级。
 *
 * 把这种用例塞进阻塞的 `npm run e2e` 会让"跑一下 E2E"变成一件需要犹豫的事，
 * 而犹豫的结果就是没人跑。因此它是独立 project + 独立脚本 + 显式开关。
 *
 * ## 跑起来需要什么
 *
 * * 网络能到 `https://opencode.ai`（`backend/.env` 的 `DEEPSEEK_BASE_URL`）；
 * * `backend/.env` 里有可用的 `DEEPSEEK_API_KEY`（**不要**提交、不要打印）；
 * * 本机已缓存嵌入模型 `backend/data/models/BAAI/bge-m3`（没有的话首次会去下载）；
 * * Python >= 3.10 且装有后端依赖的解释器（脚本自己找 `minerua_env`，见
 *   `backend/scripts/_e2e_full_runner.py` 的 `resolve_python`）。
 *
 * ## 隔离
 *
 * 后端与 worker 由 `backend/scripts/_e2e_full_runner.py` 拉起，数据库、vault、
 * Celery broker/结果、日志全部落在 `%TEMP%/engramnote-e2e-full`，
 * `backend/data/**`（真实知识库）一个字节都不写。用例在最后**自己校验**这件事
 * （真实库的 mtime 不变），因为"约定隔离"和"真的隔离"是两件事。
 *
 * ## 断言原则
 *
 * 每一步都断言**真实产出**，而不是"没报错"：
 * 上传 → 笔记标题与正文出现在详情页；理解 → 卡片数/题目数 > 0；
 * 复习 → 提交后出现判分结果；问答 → `token` 事件流出了**非空**文本且
 * 长度在流式过程中**增长**（否则"一次性返回"也能骗过 `> 0` 的断言）。
 */
import { expect, test, type Page } from '@playwright/test';
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { blockThirdParty } from './support';
import { cleanAnswerText, expectAnswerCardText } from './e2e-full-locators';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..');
const REAL_DB = join(REPO_ROOT, 'backend', 'data', 'db', 'engramnote.db');
const OUT_DIR = join(HERE, '..', 'test-results-e2e-full');
const REPORT_PATH = join(OUT_DIR, 'evidence.json');

/**
 * 上传资料在**界面上的**标题。
 *
 * ⚠️ 它必须与"后端从文件名推出的标题"逐字一致：后端取的是上传文件名的
 * **主干**（`变压器运行技术标准.md` → `变压器运行技术标准`，
 * 见 `api/upload.py` 的 `safe_stem`），文件名的圆括号不会进标题。
 * 第一次把这里写成带"（E2E 全链路）"后缀时，断言在**详情页已经渲染出正文**
 * 的情况下失败（截图/`error-context.md` 里 `heading "变压器运行技术标准" [level=1]`
 * 和整段正文都在），失败信息指向"没上传成功"，真实原因只是一个字符串对不上。
 */
const NOTE_TITLE = '变压器运行技术标准';
const DOC_FILE = join(OUT_DIR, 'transformer-standard.md');
const DOC_CONTENT = `# 变压器运行技术标准

## 顶层油温
主变压器顶层油温正常运行不得超过 85 摄氏度。当顶层油温达到 95 摄氏度时，
必须立即降低负荷并启动全部冷却装置。

## 冷却方式
主变压器采用强迫油循环风冷方式，共配置三组冷却器。任意一组冷却器故障时，
必须降低负荷运行。

## 巡检要求
每班至少巡检一次，记录油温、绕组温度、油位与冷却器运行状态。
`;

/** 一次运行内的串行状态（4 条用例共享，`describe.serial` 保证顺序） */
interface ChainState {
  token: string;
  noteId: string;
  cardCount: number;
  questionCount: number;
  quizId: string;
  realDbMtime: number | null;
  /** 跑之前 `backend/data/tmp/upload` 里的条目（用来证明本次运行**没有新增**） */
  realTmpEntries: string[] | null;
}

const state: ChainState = {
  token: '',
  noteId: '',
  cardCount: 0,
  questionCount: 0,
  quizId: '',
  realDbMtime: null,
  realTmpEntries: null,
};

/** 证据：跑完写进 `frontend/test-results/e2e-full/evidence.json`（报告引用它） */
const evidence: Record<string, unknown> = {
  startedAt: new Date().toISOString(),
  steps: [] as unknown[],
  qa: {},
};

function record(step: string, detail: unknown): void {
  (evidence.steps as unknown[]).push({ step, at: new Date().toISOString(), detail });
  // ⚠️ 每次都要确保目录在：Playwright 在**每次运行开始时清空** `test-results/`
  // （`.last-run.json` 也在那儿），于是"上一轮建好的目录"不保证还在。
  // 实测症状是 `ENOENT: no such file or directory, open '.../evidence.json'`，
  // 而它出现在 `record()` 里 —— 看起来像"证据写不进去"，其实只是目录没了。
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(REPORT_PATH, JSON.stringify(evidence, null, 2), 'utf-8');
}

function fail(message: string): never {
  // 截图由 Playwright 的 `screenshot: 'only-on-failure'` 负责；这里补齐"为什么"
  throw new Error(message);
}

/**
 * 直接打后端 API：用 **`page.request`**（Playwright 自己的 HTTP 客户端），
 * 不用页面里的 `fetch`。
 *
 * ## 为什么（踩过一次）
 *
 * 页面里 `fetch('/api/...')` 需要有**文档基准地址**。第 ④ 条用例在
 * `page.goto` 之前就要查"有哪些到期题目"（先看队列是否为空，再决定是否值得
 * 打开复习页），此时页面还停在 `about:blank`，于是报：
 *
 *     TypeError: Failed to execute 'fetch' on 'Window':
 *     Failed to parse URL from /api/review/due?limit=50
 *
 * 失败信息看起来像"接口地址写错了"，真实原因是"这时候还没有页面。
 * `page.request` 没有这个问题：它由 Playwright 发起，直接吃 `use.baseURL`
 * （本 project 指向 4381 的 Vite，`/api` 由它代理到真实后端），
 * 因此走的是与浏览器**同一条**代理路径。
 *
 * 需要浏览器自己发请求的地方（问答的 SSE 取证）仍然在页面里 fetch —— 那里测的
 * 就是"前端这条链路"，不能换掉。
 */
async function apiFetch(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<{ status: number; body: unknown }> {
  const response = await page.request.fetch(path, {
    method: init.method ?? 'GET',
    headers: {
      Authorization: `Bearer ${state.token}`,
      ...(init.body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    data: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const text = await response.text();
  let parsed: unknown = text;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    /* 保留原始文本 */
  }
  return { status: response.status(), body: parsed };
}

/** 真实库的 mtime：用来证明这次运行没有写生产库 */
function realDbMtime(): number | null {
  try {
    return statSync(REAL_DB).mtimeMs;
  } catch {
    return null;
  }
}

/**
 * 真实数据目录里"临时上传"的子条目（**内容**，不是"目录存不存在"）
 *
 * `backend/data/tmp/upload` 本来就可能存在（历史运行、pytest、别的手工操作都会
 * 留下它），所以"目录存在"不是判据。判据是"**条目集合有没有变化**"：
 * 本次 E2E 的两阶段上传必须写到 `ENGRAMNOTE_E2E_BROKER_DIR/tmp/upload` 下
 * （见 `_e2e_full_bootstrap.patch_upload_module`），真实目录里一个新条目都不该出现。
 *
 * ⚠️ 这条断言是"修过一次"的：最初写的是"目录不存在才算通过"，
 * 第一次真跑就失败了 —— 而那次失败**不是产品的错**（17:57 的历史残留），
 * 属于"用错的判据制造假警报"。假警报会让人把断言删掉，所以判据必须选对。
 */
function realTmpUploadEntries(): string[] | null {
  const dir = join(REPO_ROOT, 'backend', 'data', 'tmp', 'upload');
  try {
    if (!existsSync(dir)) return null;
    return readdirSync(dir).sort();
  } catch {
    return null;
  }
}

function ensureDocFile(): void {
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(DOC_FILE, DOC_CONTENT, 'utf-8');
}

test.describe.configure({ mode: 'serial' });

test.describe('全链路（真实后端 + 真实 LLM）', () => {
  test.beforeAll(() => {
    ensureDocFile();
    state.realDbMtime = realDbMtime();
    state.realTmpEntries = realTmpUploadEntries();
  });

  test('① 注册登录（真实账号）', { tag: '@chain' }, async ({ page }) => {
    await blockThirdParty(page);
    const user = `e2efull${Date.now().toString().slice(-10)}`;
    const password = 'E2eFull!2026x';

    await page.goto('/register');
    await page.getByLabel('邮箱').fill(`${user}@example.com`);
    await page.getByLabel('用户名').fill(user);
    await page.getByLabel('密码').fill(password);
    await page.getByRole('button', { name: '注册' }).click();

    // 注册成功后应用切到已登录外壳（侧边栏出现），这是真实路由切换而非桩。
    //
    // ⚠️ 侧边栏的导航项是 `<button>`（`Sidebar.tsx` 用 `useNavigate` 做编程式跳转），
    // 不是 `<a>` —— 第一次写成 `getByRole('link')` 时失败信息是
    // "element(s) not found"，而 `error-context.md` 的 DOM 快照里
    // 每一项都赫然写着 `button "⌂ 仪表盘"`。**失败信息指向"没登录成功"，
    // 真实原因只是角色写错了**，所以这里留一行说明。
    await expect(page.getByRole('button', { name: /仪表盘/ }).first()).toBeVisible({
      timeout: 30_000,
    });

    state.token = await page.evaluate(() => localStorage.getItem('engramnote_token') ?? '');
    if (!state.token) fail('注册后 localStorage 里没有访问令牌');

    // 令牌真的能用（而不是只写进了 localStorage）
    const me = await apiFetch(page, '/api/auth/me');
    if (me.status !== 200) fail(`/api/auth/me 返回 ${me.status}：${JSON.stringify(me.body)}`);

    record('register', { user, status: me.status, me: me.body });
  });

  test('② 上传 → 转换 → 清洗（真实管道，轮询 UI 文案）', { tag: '@chain' }, async ({ page }) => {
    await blockThirdParty(page);
    await page.addInitScript((token) => {
      localStorage.setItem('engramnote_token', token);
    }, state.token);

    await page.goto('/upload');
    await expect(page.getByRole('heading', { name: '上传学习资料' })).toBeVisible();

    // 走真实的拖放路径：构造 DataTransfer 触发 drop（与用户拖文件进来同一条链路）
    const dataTransfer = await page.evaluateHandle(
      async ({ name, content }) => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([content], name, { type: 'text/markdown' }));
        return transfer;
      },
      { name: '变压器运行技术标准.md', content: DOC_CONTENT },
    );
    await page.getByRole('button', { name: '点击或拖拽文件上传' }).dispatchEvent('drop', {
      dataTransfer,
    });

    // prepare 成功 → 出现"上传设置"卡片（文件名可改），确认上传
    await expect(page.getByLabel('文件名')).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: '确认上传' }).click();

    // 转换 + 清洗：状态文案来自 GET /upload/{id}/status 的轮询（每 5s 一次）
    await expect(page.getByText(/^状态: /)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('转换完成！')).toBeVisible({ timeout: 900_000 });

    // 自动跳转到笔记详情页（真实路由）
    await page.waitForURL(/\/notes\/[0-9a-f-]{36}$/, { timeout: 120_000 });
    state.noteId = page.url().split('/').pop() ?? '';
    if (!state.noteId) fail(`无法从 URL 取到笔记 ID：${page.url()}`);

    // 详情页真的渲染出了这篇资料（标题 + 正文），而不是"没报错"
    await expect(page.getByText(NOTE_TITLE, { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText('顶层油温', { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });

    const status = await apiFetch(page, `/api/upload/${state.noteId}/status`);
    record('upload', { noteId: state.noteId, status: status.body, url: page.url() });
  });

  test('③ 理解管道：真的产出卡片与题目', { tag: '@chain' }, async ({ page }) => {
    await blockThirdParty(page);
    await page.addInitScript((token) => {
      localStorage.setItem('engramnote_token', token);
    }, state.token);
    await page.goto(`/notes/${state.noteId}`);

    // 触发理解（产品里由理解入口触发；5.13 这一层要验的是**管道**，
    // 不是"那个按钮的文案"）。等待期间用**产品自己的**状态接口观察进度，
    // 而不是 sleep 一个猜出来的时长。
    const started = await apiFetch(page, `/api/understanding/${state.noteId}/start`, {
      method: 'POST',
      body: { confirm: false },
    });
    if (started.status !== 200) {
      fail(`触发理解失败 ${started.status}：${JSON.stringify(started.body)}`);
    }

    const deadline = Date.now() + 1_500_000;
    let noteStatus = '';
    const seen: string[] = [];
    while (Date.now() < deadline) {
      const res = await apiFetch(page, `/api/understanding/${state.noteId}/status`);
      noteStatus = String((res.body as { status?: string })?.status ?? '');
      if (seen[seen.length - 1] !== noteStatus) seen.push(noteStatus);
      if (noteStatus === 'archived' || noteStatus === 'learning_failed') break;
      await page.waitForTimeout(5_000);
    }
    if (noteStatus !== 'archived') {
      fail(`理解未完成：final=${noteStatus} 观察到的状态序列=${seen.join(' → ')}`);
    }

    const cards = await apiFetch(page, `/api/understanding/${state.noteId}/cards`);
    state.cardCount = Number((cards.body as { total?: number })?.total ?? 0);
    if (state.cardCount <= 0) fail(`理解完成但卡片数为 0：${JSON.stringify(cards.body)}`);

    // 题目生成是理解之后**另一个**任务，要单独等
    const qDeadline = Date.now() + 600_000;
    while (Date.now() < qDeadline) {
      const questions = await apiFetch(page, `/api/understanding/${state.noteId}/questions`);
      state.questionCount = Number((questions.body as { total?: number })?.total ?? 0);
      if (state.questionCount > 0) break;
      await page.waitForTimeout(5_000);
    }
    if (state.questionCount <= 0) fail('理解完成但题目数为 0（出题任务没有产出）');

    // 界面侧：知识卡片页能看到这篇笔记产出的卡片（后端有数据 ≠ 界面看得见）
    await page.goto('/cards');
    await expect(page.getByText(NOTE_TITLE, { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });

    record('understand', {
      noteStatus,
      statusSequence: seen,
      cardCount: state.cardCount,
      questionCount: state.questionCount,
    });
  });

  test('④ 复习：提交答案并推进调度', { tag: '@chain' }, async ({ page }) => {
    await blockThirdParty(page);
    await page.addInitScript((token) => {
      localStorage.setItem('engramnote_token', token);
    }, state.token);

    // 新题目 next_review_at 为 NULL = 立即可复习（models/quiz_item.py），
    // 因此这里应当真的有到期题目；没有就是链路缺口，不能跳过。
    //
    // ⚠️ 这一次查询发生在 `page.goto` **之前**（先看队列空不空，再决定值不值得
    // 打开复习页）—— 早先这里用的是页面里的 fetch，在 `about:blank` 上会报
    // "Failed to parse URL from /api/review/due"，见 `apiFetch` 的说明。
    const due = await apiFetch(page, '/api/review/due?limit=50');
    const dueItems = (due.body as { items?: Array<{ id: string }> })?.items ?? [];
    if (dueItems.length === 0) {
      fail(`复习队列为空：${JSON.stringify(due.body)}（理解产出的题目没有进入复习调度）`);
    }
    state.quizId = dueItems[0].id;

    await page.goto('/review');
    // 出现"提交答案"说明有题可答（到期题目列表非空）
    const submit = page.getByRole('button', { name: '提交答案' });
    await expect(submit).toBeVisible({ timeout: 60_000 });

    // 简答题用 textarea，填空/选择用 input 或选项按钮：三种都覆盖
    const textarea = page.getByPlaceholder('请输入你的回答...');
    const fillInput = page.getByPlaceholder('请输入答案...');
    if (await textarea.count()) {
      await textarea
        .first()
        .fill('顶层油温正常运行不得超过 85 摄氏度，达到 95 摄氏度必须降低负荷。');
    } else if (await fillInput.count()) {
      await fillInput.first().fill('85');
    } else {
      const option = page
        .locator('button')
        .filter({ hasText: /^[A-D][.、)]/ })
        .first();
      if (await option.count()) {
        await option.click();
      } else {
        // 兜底：点第一个非"提交答案"的选项按钮
        await page.locator('button:not([disabled])').nth(0).click();
      }
    }

    await submit.click();

    // 提交后的真实产出：判分区出现（正确/错误，或"请对照答案，给自己的回忆程度打分"）
    const verdict = page.getByText(/回答正确|回答错误|请对照答案，给自己的回忆程度打分/);
    await expect(verdict.first()).toBeVisible({ timeout: 60_000 });

    // 后端侧：这次提交真的落库了（复习历史里能看到）
    const history = await apiFetch(page, '/api/review/history?page=1&page_size=5');
    const items = (history.body as { items?: unknown[] })?.items ?? [];
    if (items.length === 0) fail(`提交后复习历史为空：${JSON.stringify(history.body)}`);

    const stats = await apiFetch(page, '/api/review/stats');
    record('review', {
      quizId: state.quizId,
      verdictText: (await verdict.first().innerText()).trim(),
      history: items[0],
      stats: stats.body,
    });
  });

  test('⑤ 问答：SSE 真的逐段吐出 token', { tag: '@chain' }, async ({ page }) => {
    await blockThirdParty(page);
    await page.addInitScript((token) => {
      localStorage.setItem('engramnote_token', token);
    }, state.token);

    // 流式取证：在**用户代码之前**包一层 fetch，把 SSE 响应体旁路一份。
    // `Response.clone()` 是关键 —— 应用读原流、探针读副本，互不干扰。
    // 有了它，"流式"就不是靠"最终文本非空"猜的：
    // 到达分段数 / 首段与末段的时间差 / 长度增长都能直接断言。
    await page.addInitScript(() => {
      const w = window as unknown as { __sse: Record<string, unknown> };
      w.__sse = {
        events: 0,
        tokens: [],
        meta: null,
        sources: null,
        done: false,
        error: null,
        status: 0,
      };
      const original = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const response = await original(input, init);
        const type = response.headers.get('content-type') ?? '';
        if (!type.includes('text/event-stream') || !response.body) return response;
        w.__sse.status = response.status;
        const copy = response.clone();
        void (async () => {
          const reader = copy.body!.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split('\n\n');
            buffer = frames.pop() ?? '';
            for (const frame of frames) {
              const eventLine = frame.split('\n').find((l) => l.startsWith('event:'));
              const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
              if (!eventLine) continue;
              const name = eventLine.slice(6).trim();
              w.__sse.events = (w.__sse.events as number) + 1;
              let payload: Record<string, unknown> = {};
              try {
                payload = dataLine ? JSON.parse(dataLine.slice(5).trim()) : {};
              } catch {
                /* 坏帧忽略 */
              }
              if (name === 'token') {
                (w.__sse.tokens as Array<{ at: number; content: string }>).push({
                  at: Date.now(),
                  content: String(payload.content ?? ''),
                });
              } else if (name === 'meta') {
                w.__sse.meta = payload;
              } else if (name === 'sources') {
                w.__sse.sources = payload;
              } else if (name === 'done') {
                w.__sse.done = true;
              } else if (name === 'error') {
                w.__sse.error = payload;
              }
            }
          }
        })();
        return response;
      };
    });

    await page.goto('/qa');
    await expect(page.getByRole('heading', { name: '智能问答' })).toBeVisible();

    const question = '主变压器顶层油温的正常运行上限是多少？超过多少必须降低负荷？';
    await page.getByPlaceholder(/输入你的问题/).fill(question);
    await page.getByRole('button', { name: '提问' }).click();

    // 提问气泡出现（用户动作真的生效）
    await expect(page.getByText(question, { exact: true })).toBeVisible({ timeout: 30_000 });

    const sse = () =>
      page.evaluate(() => {
        const raw = (window as unknown as { __sse: Record<string, unknown> }).__sse;
        const tokens = raw.tokens as Array<{ at: number; content: string }>;
        return {
          events: raw.events as number,
          tokenCount: tokens.length,
          text: tokens.map((t) => t.content).join(''),
          firstAt: tokens.length ? tokens[0].at : 0,
          lastAt: tokens.length ? tokens[tokens.length - 1].at : 0,
          meta: raw.meta,
          sources: raw.sources,
          done: raw.done as boolean,
          error: raw.error,
          status: raw.status as number,
        };
      });

    // 1) 流真的开始了（拿到 token），且真的结束了（done 事件）
    const deadline = Date.now() + 300_000;
    let snapshot = await sse();
    while (Date.now() < deadline && !snapshot.done) {
      await page.waitForTimeout(1_000);
      snapshot = await sse();
    }

    // UI 侧：答案文本从空变成非空（首字之前显示"AI 正在思考..."）
    //
    // 定位式与它踩过的三次坑写在 `e2e-full-locators.ts`，并由**不需要后端**的
    // `e2e-full-probe.spec.ts` 秒级守护（`npm run e2e:full:probe`）：
    //   1. `getByText` 返回最内层元素 → 读到问题气泡 → 清成"0 字"；
    //   2. `getByText(整句, {exact:true}).locator('..')` → `innerText()` 用**用例超时**
    //      （25 分钟）而不是 expect 的 60 秒 → 挂满整条链路；
    //   3. `filter({ has })` 会让祖先也满足条件 → 取到页面上那个"提问"输入卡片。
    // 三次都花掉一整条链路（含真实 LLM 调用）才暴露 —— 所以现在选择器先过探针。
    const cardText = await expectAnswerCardText(page, question);

    const firstVisible = cleanAnswerText(cardText, question).length;
    await page.waitForTimeout(400);
    const secondVisible = cleanAnswerText(
      await expectAnswerCardText(page, question),
      question,
    ).length;

    // 等流结束（provider 行只在 `sources` 事件之后出现）
    await expect(page.getByText(/由 (DeepSeek|GLM) 提供支持/)).toBeVisible({ timeout: 300_000 });
    const finalCardText = cleanAnswerText(await expectAnswerCardText(page, question), question);
    const providerText = (await page.getByText(/由 (DeepSeek|GLM) 提供支持/).innerText()).trim();
    const sourcesVisible = await page.getByText(/引用来源:/).count();

    const streamDuration = snapshot.lastAt - snapshot.firstAt;

    // 用量：证明这**真的**是一次 LLM 调用（而不是降级/兜底路径）。
    // 断言放在下一条用例里（那时 `rag_answer_stream` 的记账一定已经落库 ——
    // 流式路径是在**流读完之后**才 `record_call`，这里查得太早会看不到它）。
    const usage = await apiFetch(page, '/api/llm/usage?days=1&group_by=scene');

    record('qa', {
      question,
      httpStatus: snapshot.status,
      sseEvents: snapshot.events,
      tokenCount: snapshot.tokenCount,
      streamDurationMs: streamDuration,
      meta: snapshot.meta,
      sources: Array.isArray((snapshot.sources as { sources?: unknown[] })?.sources)
        ? (snapshot.sources as { sources: unknown[] }).sources.length
        : 0,
      done: snapshot.done,
      error: snapshot.error,
      textLength: snapshot.text.length,
      textPreview: snapshot.text.slice(0, 200),
      uiFirstVisibleLength: firstVisible,
      uiSecondVisibleLength: secondVisible,
      uiFinalLength: finalCardText.length,
      providerText,
      sourcesVisible,
      usage: usage.body,
    });

    if (snapshot.error) fail(`SSE 返回 error 事件：${JSON.stringify(snapshot.error)}`);
    if (!snapshot.done) fail(`SSE 没有 done 事件（events=${snapshot.events}）`);
    if (snapshot.tokenCount < 2) {
      fail(`token 事件只有 ${snapshot.tokenCount} 个 —— 不是流式，而是一次性返回`);
    }
    if (snapshot.text.length < 10) {
      fail(`SSE 文本过短（${snapshot.text.length} 字）：${snapshot.text}`);
    }
    if (finalCardText.length < 10) {
      fail(`界面上没有渲染出答案（${finalCardText.length} 字）—— 后端流了但 UI 没显示`);
    }
    // 界面上看到的答案应当与流里的文本一致（前 20 字足够判定"同一份内容"）
    if (!finalCardText.includes(snapshot.text.slice(0, 20))) {
      fail(
        `界面答案与 SSE 文本对不上。\n  SSE: ${snapshot.text.slice(0, 80)}\n  UI : ${finalCardText.slice(0, 80)}`,
      );
    }
    // 流式渲染的长度增长只作**证据**记录、不作硬断言：要稳定观察到"过程中"
    // 增长就得让模型慢下来（4090 tokens 有可能不到 1 秒就推送完，实测 699ms
    // 推了 107 个 token）。"是不是流式"由上面的 token 事件数与分段到达时间判定。
    if (secondVisible > 0 && firstVisible > 0 && secondVisible < firstVisible) {
      fail(`界面答案长度回退（${firstVisible} → ${secondVisible}），渲染不是单调增长`);
    }
  });

  test(
    '⑥ LLM 记账 + 隔离校验：真的调了模型，且没有碰真实库',
    { tag: '@chain' },
    async ({ page }) => {
      // ── 记账：整条链路应当留下**多个场景**的调用记录 ──
      //     这是"真的走到了模型"的最后一道证据：降级/兜底路径不会有这些行。
      const usage = await apiFetch(page, '/api/llm/usage?days=1&group_by=scene');
      const totals = (usage.body as { totals?: { calls?: number; failed_calls?: number } })?.totals;
      const groups =
        (usage.body as { groups?: Array<{ key: string; calls: number }> })?.groups ?? [];
      const scenes = groups.map((group) => group.key);

      if ((totals?.calls ?? 0) <= 0) {
        fail(`LLM 记账里没有调用记录：${JSON.stringify(usage.body)}（链路没有真的走到模型）`);
      }
      if ((totals?.failed_calls ?? 0) > 0) {
        fail(`有 ${totals?.failed_calls} 次失败的 LLM 调用：${JSON.stringify(groups)}`);
      }

      // ── 隔离：真实库一个字节都不该被写 ──
      const after = realDbMtime();
      if (state.realDbMtime !== null && after !== state.realDbMtime) {
        fail(
          `真实数据库被改写了！before=${state.realDbMtime} after=${after}（${REAL_DB}）—— ` +
            'E2E 的隔离失效，必须先修隔离再跑这条链路。',
        );
      }
      // 运行用的临时库必须真的存在（否则"没碰真实库"可以因为"根本没跑"而成立）
      const tmpRoot =
        process.env.ENGRAMNOTE_E2E_TMP || join(process.env.TEMP ?? '', 'engramnote-e2e-full');
      const tmpDb = join(tmpRoot, 'db', 'engram.db');
      if (!existsSync(tmpDb)) {
        fail(`临时库不存在（${tmpDb}）—— 本次运行的后端并不在隔离环境里`);
      }

      // 本次运行的上传暂存不该落到真实数据目录里（见 `_e2e_full_bootstrap` 的说明）。
      // 判据是**条目集合的变化**，不是"目录存不存在"——后者会因历史残留产生假警报。
      const afterTmp = realTmpUploadEntries();
      const beforeTmp = state.realTmpEntries ?? [];
      const newEntries = (afterTmp ?? []).filter((name) => !beforeTmp.includes(name));
      if (newEntries.length > 0) {
        fail(
          `真实数据目录里出现了本次运行的上传暂存：${newEntries.join(', ')}` +
            `（${join(REPO_ROOT, 'backend', 'data', 'tmp', 'upload')}）—— ` +
            '`TMP_UPLOAD_DIR` 的隔离没有生效。',
        );
      }

      record('llm_usage', {
        totals,
        scenes,
        groups,
        priceConfigured: (usage.body as { price_configured?: boolean })?.price_configured,
      });
      record('isolation', {
        realDb: REAL_DB,
        realDbMtimeBefore: state.realDbMtime,
        realDbMtimeAfter: after,
        tmpDb,
        tmpDbSize: statSync(tmpDb).size,
        databaseUrl: 'sqlite+aiosqlite:///<temp>/db/engram.db',
        storageDir: join(tmpRoot, 'vault'),
        brokerDir: join(tmpRoot, 'celery', 'broker'),
        backendDataTmpUploadBefore: beforeTmp,
        backendDataTmpUploadAfter: afterTmp,
      });
    },
  );

  test.afterAll(() => {
    evidence.finishedAt = new Date().toISOString();
    mkdirSync(OUT_DIR, { recursive: true });
    writeFileSync(REPORT_PATH, JSON.stringify(evidence, null, 2), 'utf-8');
  });
});
