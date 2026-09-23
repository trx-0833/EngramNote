/**
 * 现状基线探针（visual-refactor-plan 批次 0.1，之后每批沿用）
 *
 * ## 它做两件事
 *
 * 1. 对 22 个场景拍**真实渲染**截图 → `frontend/shots-current/`（不入库）
 * 2. 打印一份**文本化的视觉快照**：关键元素的 computed style
 *
 * 第 2 件事是给"看不见图的人"准备的。改外观的批次必须能逐条核对
 * "标题字距真的从 `-0.01em` 变成 `0` 了吗""主按钮背景真的是纯色了吗"，
 * 而不是只能说"应该好了"。它与截图是同一把尺子的两种读数 ——
 * 这也是 `css-convention.md` §5 雷区 3 那套"用 computed 对账"的轻量版。
 *
 * ## 它不做任何断言，因此不在阻断门禁里
 *
 * `playwright.config.ts` 给它单独注册了 `shots` project，并从 `chromium`
 * 的 `testIgnore` 里排除 —— 否则 `npm run e2e` 会从 10 条用例变成 32 条、
 * 多跑约 40 秒，而那一层的"通过"毫无意义（探针永远不会红）。
 *
 * ## 用法
 *
 *     npm run shots
 *
 * 改前基线（22 张）在 `frontend/shots/`；本探针每次覆盖 `shots-current/`。
 */
import { test, type Page } from '@playwright/test';

import { DAILY_PLAN, installA11yStubs, loginAs } from './a11y-fixtures';

/** 当前一轮的输出目录（基线 `shots/` 永不覆盖，两份并排对比） */
const OUT = 'shots-current';

/** 桌面 1440×900：比门禁的 1280×720 更接近真实办公屏，也给侧栏展开留出余量 */
const DESKTOP = { width: 1440, height: 900 };

interface StyleProbe {
  label: string;
  selector: string;
  props: string[];
}

/**
 * 关键探针。挑的都是"这几批计划要动、且改坏了肉眼未必立刻发现"的元素：
 * 页面标题（A3 字距 / C1 字号）、主按钮（A3 去渐变）、内容区（C2 页宽）、
 * 侧栏图标（B2 换图标）、卡片（A4/D 的圆角与阴影）。
 */
const STYLE_PROBES: StyleProbe[] = [
  {
    label: 'page-title',
    selector: 'main h1',
    props: ['font-size', 'letter-spacing', 'font-family', 'color'],
  },
  {
    label: 'primary-btn',
    selector: 'main .btn-primary',
    props: ['background-image', 'background-color', 'color'],
  },
  {
    label: 'content-main',
    selector: 'main',
    props: ['max-width', 'padding-left', 'padding-right'],
  },
  {
    // 批次 B2 起改为直接量 **svg 本身**（而不是它外面的 20×20 槽）：
    // 换图标前这个槽里装的是 16px 的 Unicode 字符（靠 `font-size: 1rem` 撑），
    // 换后是 `<Icon size={20}>` 渲染的 svg —— 要验的正是"尺寸与线宽由组件统一施加"。
    label: 'sidebar-icon',
    selector: 'nav[aria-label="主导航"] [class*="sidebarItemIcon"] svg',
    props: ['width', 'height', 'stroke-width', 'color'],
  },
  {
    label: 'first-card',
    selector: 'main .card',
    props: ['background-color', 'border-radius', 'box-shadow'],
  },
];

/**
 * 等到页面**真的渲染出内容**，而不是死等一个固定时长。
 *
 * 首次实测（2026-09-23）踩到的坑：固定 `waitForTimeout(1800)` 在 Vite
 * 冷启动的首批场景里根本不够 —— 读到的还是 `RouteFallback`（一个 spinner，
 * 没有 h1），于是 20 个页面里有 18 个报 `page-title[no]`，
 * 而**同一页面的移动端用例**（后跑、Vite 已预热）却读到了 32px。
 * 也就是说那份基线是"探针的等待不足"，不是页面的真实状态。
 *
 * 这类"拍到了另一时刻的页面"的错，比拍不到更危险 —— 它会让人拿着
 * 假的数字去做前后对比。所以这里改成先等一个**内容锚点**出现。
 */
async function waitForRendered(page: Page, scene: string): Promise<void> {
  try {
    await page.waitForFunction(
      () => {
        const main = document.querySelector('main');
        if (!main) return false;
        // 路由 fallback 是一个 spinner（`main` 里只有几个字符），
        // 真实内容都在 40 字符以上 —— 用文本长度区分"渲染完了"与"还在等 chunk"。
        // 这一步是必须的：探针没有断言，等不到就会**静默拍下一张空页面**。
        return (main.textContent ?? '').trim().length > 40;
      },
      null,
      { timeout: 20000 },
    );
  } catch {
    // 响亮地把"这一页没渲染出来"说出来 —— 否则它只会变成一张看着像样子的空截图
    console.log(`WARN|${scene}|main 内容 20 秒内没有超过 40 字符，可能停在路由 fallback`);
  }
  await page.waitForTimeout(600);
}

/** 读 computed style 并打成一行（`found=no` 表示该页没有这个元素，同样是信息） */
async function printStyleSnapshot(page: Page, scene: string): Promise<void> {
  const result = await page.evaluate((probes: StyleProbe[]) => {
    const out: Record<string, Record<string, string>> = {};
    probes.forEach((probe) => {
      const el = document.querySelector(probe.selector);
      if (!el) {
        out[probe.label] = { found: 'no' };
        return;
      }
      const computed = getComputedStyle(el);
      const record: Record<string, string> = { found: 'yes' };
      probe.props.forEach((prop) => {
        record[prop] = computed.getPropertyValue(prop);
      });
      out[probe.label] = record;
    });

    // 诊断：`page-title[no]` 有两种完全不同的成因 ——
    // ① 页面还没渲染出内容（停在路由 fallback）；
    // ② 渲染好了，但 h1 不在 `main` 里。
    // 不区分就会拿着假数字做前后对比（首次实测已经踩过一次）。
    const main = document.querySelector('main');
    const diagnostics = {
      h1Total: String(document.querySelectorAll('h1').length),
      h1InMain: String(document.querySelectorAll('main h1').length),
      mainCount: String(document.querySelectorAll('main').length),
      mainTextLen: String(main?.textContent?.length ?? 0),
      bodyTextLen: String(document.body.textContent?.length ?? 0),
    };

    return { out, diagnostics };
  }, STYLE_PROBES);

  const parts = Object.entries(result.out).map(([label, record]) => {
    const body = Object.entries(record)
      .filter(([key]) => key !== 'found')
      .map(([key, value]) => `${key}=${value}`)
      .join(' ');
    return `${label}[${record.found}]${body ? ` ${body}` : ''}`;
  });
  const diag = Object.entries(result.diagnostics)
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
  console.log(`STYLE|${scene}|${parts.join(' | ')}`);
  console.log(`DIAG|${scene}|${diag}`);
}

/** 走"落地即见"的页面（不做展开/点击，截的是用户第一眼） */
const PAGES: { name: string; path: string }[] = [
  { name: '02-dashboard', path: '/' },
  { name: '03-notes', path: '/notes' },
  { name: '04-note-detail', path: '/notes/note-1' },
  { name: '05-cards', path: '/cards' },
  { name: '06-card-detail', path: '/cards/card-1' },
  { name: '07-graph', path: '/graph' },
  { name: '08-questions', path: '/questions' },
  { name: '09-review', path: '/review' },
  { name: '10-review-cards', path: '/review/cards' },
  { name: '11-review-quick', path: '/review/quick/note-1' },
  { name: '12-today', path: '/today' },
  { name: '13-daily', path: '/daily' },
  { name: '14-qa', path: '/qa' },
  { name: '15-upload', path: '/upload' },
  { name: '16-goals', path: '/goals' },
  { name: '17-projects', path: '/projects' },
  { name: '18-assessment', path: '/assessment' },
  { name: '19-trash', path: '/trash' },
  { name: '20-notfound', path: '/no-such-page' },
];

test.describe('现状截图与样式快照（桌面）', () => {
  test.use({ viewport: DESKTOP });

  test('01-login', async ({ page }) => {
    await installA11yStubs(page);
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await waitForRendered(page, '01-login');
    await printStyleSnapshot(page, '01-login');
    await page.screenshot({ path: `${OUT}/01-login.png` });
  });

  for (const { name, path } of PAGES) {
    test(name, async ({ page }) => {
      await installA11yStubs(page, { '/api/goals/daily-plan': DAILY_PLAN });
      await loginAs(page, path);
      await waitForRendered(page, name);
      await printStyleSnapshot(page, name);
      await page.screenshot({ path: `${OUT}/${name}.png` });
    });
  }
});

test.describe('现状截图与样式快照（移动端 375×667）', () => {
  test.use({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true });

  test('90-mobile-dashboard', async ({ page }) => {
    await installA11yStubs(page, { '/api/goals/daily-plan': DAILY_PLAN });
    await loginAs(page, '/');
    await waitForRendered(page, '90-mobile-dashboard');
    await printStyleSnapshot(page, '90-mobile-dashboard');
    await page.screenshot({ path: `${OUT}/90-mobile-dashboard.png` });
  });

  test('91-mobile-nav-open', async ({ page }) => {
    await installA11yStubs(page, { '/api/goals/daily-plan': DAILY_PLAN });
    await loginAs(page, '/');
    await waitForRendered(page, '91-mobile-nav-open');
    // 汉堡按钮：移动端侧栏是抽屉，先让它打开 —— 这是手机上唯一的导航形态
    await page
      .getByRole('button', { name: /菜单|导航/ })
      .first()
      .click();
    await page.waitForTimeout(900);
    await printStyleSnapshot(page, '91-mobile-nav-open');
    await page.screenshot({ path: `${OUT}/91-mobile-nav-open.png` });
  });
});
