/**
 * a11y 深层验证：Tab 顺序 / 焦点陷阱 / 焦点环（C4）
 *
 * ## 这一层补的是什么 —— 以及它为什么**不能**放进 `a11y.spec.ts`
 *
 * `docs/a11y-audit.md` §4 自述这是「**当前没有任何一层在守**」的三条：
 * ① Tab 顺序是否*合理*；② 模态/抽屉的焦点陷阱；③ 焦点环画出来长什么样。
 *
 * 已做的两半（`expectReachableByTab` 16 处、`findFakeAffordances` 零容忍扫描）
 * 只回答「**到得了**」，不回答「**顺序对不对**」与「**看不看得见焦点**」。
 *
 * 为什么单独一个 spec 而不是加进 `a11y.spec.ts`：那个文件跑在 `a11y` project 下，
 * 判定尺度是"违规有没有超出登记表"；而这一组是**行为断言**
 * （Tab 序列可复现、焦点不跑到背后、焦点环存在）。混在一起会让
 * "这次红的到底是多了一条对比度问题还是焦点真的漏了"变得含糊 ——
 * 与 `playwright.config.ts` 里 `A11Y_SPEC` 那段记的理由是同一条。
 *
 * ## 三条判据在设计上的共同点：**记录 + 断言**
 *
 * Tab 顺序这种东西，光"断言它等于某个硬编码序列"是不负责任的 ——
 * 加一个导航项就会红，而它**本来就应该**红不了。所以每条断言都遵循
 * 「**先打印实测序列，再断言那几条真正该成立的性质**」：
 *
 * | 断言 | 为什么它该成立 |
 * |---|---|
 * | 同一份代码跑两次序列相同 | 顺序**可复现**。不可复现意味着用户每次 Tab 的路径都不一样 |
 * | 侧边栏导航项**全部**先于 `main` 内容 | 这是 `App.tsx` 的 DOM 顺序决定的**事实**，记录它，别假装它不存在 |
 * | 从页首按到底**能到** `main` 里 | 否则键盘用户永远进不去内容区（这正是 §4.1 担心的那件事） |
 * | 序列里不出现"同一个元素连续占两次" | 那意味着某个元素吃掉了 Tab 却没把焦点交出去 |
 */

import { expect, test, type Page } from '@playwright/test';

import { installA11yStubs, loginAs } from './a11y-fixtures';

/**
 * 从一个元素读出一份**给人看也能给断言用**的描述
 *
 * 为什么不直接用 `outerHTML`：它太长，打印 60 行会把日志淹掉。
 * 为什么不只用 `tagName`：十几个 `BUTTON` 分不出谁是谁，序列就没意义了。
 *
 * 优先级：`aria-label` → 文本（截断）→ `id`/`class` → 仅标签名。
 * 与"读屏用户怎么认出这个元素"同一口径 —— 读屏读出来的也是这几样。
 */
async function describeActive(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return '（焦点不在页面里）';
    if (el === document.body) return 'body';

    const tag = el.tagName.toLowerCase();
    const label = (el.getAttribute('aria-label') || '').trim();
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const inMain = !!el.closest('main');
    const where = inMain
      ? 'main'
      : el.closest('nav')
        ? 'nav'
        : el.closest('[role="dialog"]')
          ? 'dialog'
          : 'shell';

    let name = label || (text.length > 22 ? `${text.slice(0, 22)}…` : text);
    if (!name) name = el.getAttribute('type') || el.getAttribute('href') || tag;
    return `${tag}[${where}] ${name}`;
  });
}

/** 从 `start` 起按 `n` 次 Tab，返回每一步的 `document.activeElement` 描述 */
async function tabSequence(
  page: Page,
  n: number,
  opts: { startFrom?: string } = {},
): Promise<string[]> {
  if (opts.startFrom === undefined) {
    // **文档顺序**的起手式：什么都不点，直接 Tab。
    // ⚠️ 只要点过任何东西，起点就被设定了，量到的就不是文档顺序
    // （`body` 本身不可聚焦，所以点它是安全的"取消起点"手段）。
    await page.locator('body').click({ position: { x: 2, y: 2 } });
  } else {
    // **从一个锚点起**的起手式：点一个不可聚焦的元素，浏览器会把
    // "顺序焦点导航起点"设在它上面 —— 于是 Tab 从它**之后**开始。
    // 见 `expectReachableByTab` 里同一手法的长注释。
    await page.locator(opts.startFrom).first().click();
  }
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press('Tab');
    out.push(await describeActive(page));
  }
  return out;
}

/** 焦点此刻在不在这个选择器里 */
async function focusIsInside(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((sel) => {
    const root = document.querySelector(sel);
    const el = document.activeElement;
    if (!root) return false;
    // 面板自身（`tabIndex={-1}` 的容器）也算"在里面"
    return el === root || root.contains(el);
  }, selector);
}

/** 焦点指示的快照：`outline` 与 `box-shadow` 两样（外加 `:focus-visible` 是否命中） */
interface FocusPaint {
  outlineStyle: string;
  outlineWidth: string;
  outlineColor: string;
  boxShadow: string;
  focusVisible: boolean;
}

/**
 * ★★ 量"聚焦**改变**了什么"—— 这是 WCAG 2.4.7 的正确判据
 *
 * ## 为什么必须比"聚焦前 vs 聚焦后"，而不能只看"聚焦后有没有 box-shadow"
 *
 * 第一版就是这么写的：`shadowVisible = shadow !== 'none'`。它看着合理，
 * 实测却是个**空转断言** —— 打印出来的搜索框是：
 *
 *     outline = none 3px rgb(26, 26, 46)          ← outline-style 是 none，没画
 *     box-shadow = rgba(15, 52, 96, 0.075) 0px 0px 0px 2.87px, …   ← 7.5% 不透明度
 *
 * `outline-style: none` ⇒ outline 那一路不算数；于是判据落到 box-shadow 上，
 * 而那一层的 `rgba(…, 0.075)` 是**卡片自带的装饰阴影**，与聚焦毫无关系。
 * 也就是说：**哪怕把焦点环彻底删掉，这条断言照样绿。**
 *
 * 正确的判据是"聚焦**导致**了可见变化"：先量聚焦态，再 `blur()` 量失焦态，
 * 两者不同 ⇒ 焦点是可感知的。这条对"用 outline 实现"与"用 box-shadow 实现"
 * 一视同仁 —— 本项目两种都有（`Dialog.tsx` 的焦点环就走 box-shadow）。
 *
 * ## 为什么不写成"环的具体值"
 *
 * 具体色值/半径属于视觉设计，改配色就会红（`--color-accent` 一改就红），
 * 那种红没有信息量。判据只问"看不看得出来"。
 */
async function measureFocusPaint(
  page: Page,
): Promise<{ focused: FocusPaint; blurred: FocusPaint }> {
  return page.evaluate(() => {
    const read = (el: HTMLElement) => {
      const cs = getComputedStyle(el);
      return {
        outlineStyle: cs.outlineStyle,
        outlineWidth: cs.outlineWidth,
        outlineColor: cs.outlineColor,
        boxShadow: cs.boxShadow,
        focusVisible: el.matches(':focus-visible'),
      };
    };
    const el = document.activeElement as HTMLElement;
    const focused = read(el);
    el.blur();
    const blurred = read(el);
    return { focused, blurred };
  });
}

/** 把一份焦点指示快照渲染成一行，便于日志与失败信息 */
function paintLine(p: FocusPaint): string {
  return (
    `outline=${p.outlineStyle} ${p.outlineWidth} ${p.outlineColor} | ` +
    `box-shadow=${p.boxShadow} | :focus-visible=${p.focusVisible}`
  );
}

/**
 * 聚焦是否**产生**了可见变化
 *
 * 两路任一成立即可（与 `Dialog.tsx` 用 box-shadow 实现焦点环的事实对齐）：
 *   1. `outline-style` 从 none/hidden 变成了别的（真的画了外圈）；
 *   2. `box-shadow` 两态不同（用阴影画的环）。
 */
function focusPaintChanged(before: FocusPaint, after: FocusPaint): boolean {
  const outlineAppeared =
    /^(none|hidden)/.test(after.outlineStyle) === false &&
    (/^(none|hidden)/.test(before.outlineStyle) || before.outlineWidth !== after.outlineWidth);
  const shadowChanged = before.boxShadow !== after.boxShadow;
  return outlineAppeared || shadowChanged;
}

/**
 * ★★ 焦点指示的**强度**够不够 —— 这条才是能抓住"项目自己的焦点环被删掉"的判据
 *
 * ## 为什么必须要有这一条（实测踩到的）
 *
 * 只用上面那条"绘制有没有变化"做判据时，把项目里的全局规则
 *
 *     :focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
 *
 * **整条注释掉**，用例照样全绿 —— 因为浏览器有 **UA 默认**焦点环
 * （实测 `outline = auto 1px rgb(16, 16, 16)`）。也就是说：
 * 绘制确实变了、焦点确实看得见，但项目**精心调过的那一圈不见了**，
 * 只剩 1px 的黑边。**"能看出焦点"与"焦点环是项目设计的那个"是两件事。**
 *
 * ## 判据为什么取"≥2px 或 阴影有变化"，而不是钉死颜色
 *
 * 钉死 `rgb(15, 52, 96)` 会让改主题色时红一片，那种红没有信息量。
 * 而**宽度**是个有意义的门槛：
 *
 * - UA 默认 = `auto 1px` ⇒ 不达标（纸白底上一圈 1px 黑边，是"最低限度"）；
 * - 项目全局规则 = `2px solid` ⇒ 达标；
 * - 用 box-shadow 画的环（如 `Dialog.tsx`）⇒ 只要两态不同就算达标。
 *
 * 换句话说：这条判据问的是"**聚焦指示有没有被项目加强过**"，
 * 而不是"它是不是某个具体颜色"。
 */
function focusIndicatorIsStrong(before: FocusPaint, after: FocusPaint): boolean {
  const shadowChanged = before.boxShadow !== after.boxShadow;
  if (shadowChanged) return true;
  if (/^(none|hidden)/.test(after.outlineStyle)) return false;
  const width = Number.parseFloat(after.outlineWidth);
  return Number.isFinite(width) && width >= 2;
}

// ---------------------------------------------------------------------------

test.describe('Tab 顺序（记录 + 三条该成立的性质）', () => {
  /**
   * ★★ 这一条回答的是 `a11y-audit.md` §4.1 里那句
   * 「**本轮没有量过**」的问题：
   *
   * > 已登录外壳里侧边栏在 DOM 中先于 `main`（`App.tsx:91` 的 `<Sidebar/>`
   * > 在 `<main>` 之前），所以每个页面都要先 Tab 过 15 个导航项才能到内容
   * > —— 这可能是问题，也可能是有意，**本轮没有量过**。
   *
   * ## 实测答案（2026-10-22）：**是的，先过 19 个导航项**
   *
   * 文档顺序（什么都不点、直接 Tab）：
   * 第 1–19 步全是 `nav` 里的元素，**第 20 步**才进 `main`。
   * 侧边栏从 15 个长到了 19 个（多了知识卡片/知识图谱/问答/问题集等），
   * 所以文档里那个"15 个"也一起过时了。
   *
   * ## ⚠️ 那个数字为什么是 19 而不是"硬编码断言"
   *
   * 硬编码 `expect(firstMain).toBe(20)` 会让**每加一个导航项**都变红，
   * 而那**本来就不该**是红的（加导航是正常产品行为）。
   * 所以断言的是**两条真正的性质**：
   *   1. `nav` 元素**全部**排在第一个 `main` 元素之前（顺序事实）；
   *   2. 从页首按到底**真的能进** `main`（可达性，§4.1 担心的那件事）。
   * 具体步数打进日志，供人核对趋势。
   */
  test('已登录外壳：文档顺序里侧边栏先于内容，且内容确实到得了', async ({ page }) => {
    await installA11yStubs(page, { '/api/goals/daily-plan': undefined });
    await loginAs(page, '/');
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible();

    const N = 40;
    const first = await tabSequence(page, N);
    const second = await tabSequence(page, N);

    // ── 记录：这份序列本身就是交付物（§4.1 说"本轮没有量过"）──
    console.log(`\n   [文档顺序] 什么都不点、直接按 ${N} 次 Tab：`);
    first.forEach((d, i) => console.log(`     ${String(i + 1).padStart(2)}. ${d}`));

    // ── 性质 1：可复现 ──
    expect(
      second,
      '同一份代码两次 Tab 序列不同 —— 顺序不可复现，用户每次 Tab 走的路径都不一样',
    ).toEqual(first);

    const firstNav = first.findIndex((d) => d.includes('[nav]'));
    const firstMain = first.findIndex((d) => d.includes('[main]'));
    // ⚠️ 只数**第一个 main 之前**的 nav 项。第一版数的是整段 40 步里的 nav 项，
    // 而序列在走到 `body` 之后会**回卷**到页首再来一遍 nav —— 于是计数把
    // 第二遍也算进去了（28 个），断言 `firstMain > navCount` 变成 `19 > 28` 假失败。
    // 教训：这种"记录型"断言里的每个派生量都要问一句"它数的是哪一段"。
    const navBeforeMain = first.slice(0, firstMain).filter((d) => d.includes('[nav]')).length;
    console.log(
      `\n   首个 nav 在第 ${firstNav + 1} 步；首个 main 在第 ${firstMain + 1} 步；` +
        `main 之前有 ${navBeforeMain} 个 nav 项`,
    );

    // ── 性质 2：nav 全部先于 main（记录 DOM 顺序这个事实）──
    expect(firstNav, 'Tab 序列里一个 nav 元素都没有 —— 键盘到不了侧边栏').toBe(0);
    expect(
      firstMain,
      `从页首按了 40 次 Tab 都没进到 main 内容区 —— 键盘用户进不去正文（§4.1 担心的那件事真的发生了）`,
    ).toBeGreaterThan(0);
    expect(
      navBeforeMain,
      `第 ${firstMain + 1} 步才进 main，但它之前的 ${firstMain} 步里只有 ${navBeforeMain} 个 nav 项 ——` +
        ' 说明侧边栏与内容之间还夹着别的东西（键盘路径比"先过导航"更绕）',
    ).toBe(firstMain);
    const navAfterMain = first.slice(firstMain).filter((d) => d.includes('[nav]')).length;
    console.log(
      `   main 之后（含回卷后的第二遍）遇到 nav 元素 ${navAfterMain} 个 —— ` +
        '回卷是浏览器的正常行为，不是缺陷',
    );

    // ── 性质 3：没有元素连续占两次 body（那意味着某处吞掉了 Tab）──
    const stuck = first.filter((d, i) => i > 0 && d === 'body' && first[i - 1] === 'body');
    expect(stuck.length, 'Tab 期间焦点连续两次落在 body 上 —— 中间有元素吞掉了 Tab').toBe(0);
  });

  /**
   * 与上一条**成对**的另一半：从 `main h1` 起算的"内容可达性"。
   *
   * ## 为什么两条都要有（第一版只写了这一条，而且写错了）
   *
   * 第一版用"点 `main h1` 当锚点"来量**文档顺序**，量到的序列是
   * "1–39 全在 main，第 40 步 body，41–45 才轮到 nav" —— 当时差点据此
   * 写下"侧边栏其实排在内容之后"。**那是错的**：点 `main h1` 会把
   * 这个不可聚焦元素设成"顺序焦点导航起点"，于是 Tab 从 main **内部**
   * 往后走、绕过 nav，走完 main 才回卷到页首。
   *
   * 两个问题要两种起手式：
   *
   * | 问题 | 起手式 |
   * |---|---|
   * | 文档顺序是什么 | 什么都不点，直接 Tab |
   * | 从内容区起还能不能走完/回到导航 | 点 `main h1` 当锚点 |
   */
  test('从 main h1 起：内容区可完整走完并回卷到导航', async ({ page }) => {
    await installA11yStubs(page, { '/api/goals/daily-plan': undefined });
    await loginAs(page, '/');
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible();

    const seq = await tabSequence(page, 45, { startFrom: 'main h1' });

    // 锚点在 main 里 ⇒ 前若干步应当都在 main
    const firstMain = seq.findIndex((d) => d.includes('[main]'));
    expect(firstMain, '从 main h1 起按 Tab 却没进 main —— 锚点没起作用').toBe(0);

    // 走完内容区要能回卷到导航（不能永远困在 main 里）
    const navCount = seq.filter((d) => d.includes('[nav]')).length;
    expect(navCount, '从 main h1 起走了 45 步都没回卷到侧边栏 —— 焦点被困在内容区').toBeGreaterThan(
      0,
    );

    console.log(
      `\n   [从 main h1 起] 前 ${firstMain + 1} 步在 main；45 步内遇到 nav 元素 ${navCount} 个`,
    );
  });

  test('笔记列表：Tab 能穿过表格到达行级操作', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    const seq = await tabSequence(page, 40, { startFrom: 'main h1' });
    console.log(`\n   [Tab 顺序] 笔记列表 ${seq.length} 步：`);
    seq.forEach((d, i) => console.log(`     ${String(i + 1).padStart(2)}. ${d}`));

    // 行级操作（删除/重试）带 `aria-label="删除 <标题>"`，它出现在序列里
    // 就说明"键盘能操作这一行" —— 这正是列表页最要紧的可达性。
    const hasRowAction = seq.some((d) => d.includes('删除') || d.includes('重试'));
    expect(hasRowAction, `40 次 Tab 都没到行级操作按钮。序列：\n${seq.join('\n')}`).toBe(true);
  });
});

// ---------------------------------------------------------------------------

test.describe('焦点陷阱：模态打开时焦点不能跑到背后', () => {
  /**
   * `DeleteNoteDialog` 用的是共享基座 `components/Dialog.tsx`
   * （`focusTrap` 在 `Dialog.tsx:279-330` 的 `handleKeyDown` 里实现）。
   *
   * ⚠️ 这个陷阱的实现方式是**面板上的 React `onKeyDown`** ——
   * 也就是说：它只在"事件从面板内部冒上来"时才有机会生效。
   * jsdom 里 `Dialog.test.tsx` 已经把这条逻辑测过（17 条），
   * 但**真浏览器里的 Tab 行为与 `fireEvent.keyDown` 不是一回事**
   * （真实 Tab 会先移动焦点再派发事件，且经过 UA 的焦点顺序计算）。
   * 所以这一条只能在真 Chromium 里量。
   */
  test('DeleteNoteDialog：Tab / Shift+Tab 各走一圈，焦点始终在面板内', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    await page
      .getByRole('button', { name: /^删除 / })
      .first()
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    // 打开后焦点应当已经在面板里（基座会主动移入）
    expect(
      await focusIsInside(page, '[role="dialog"]'),
      '对话框刚打开时焦点就不在面板里 —— 读屏用户不会被告知弹了什么',
    ).toBe(true);

    const forward: string[] = [];
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press('Tab');
      const desc = await describeActive(page);
      forward.push(desc);
      expect(
        await focusIsInside(page, '[role="dialog"]'),
        `第 ${i + 1} 次 Tab 之后焦点跑到了面板外（${desc}）—— 焦点陷阱漏了`,
      ).toBe(true);
    }

    const backward: string[] = [];
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Shift+Tab');
      const desc = await describeActive(page);
      backward.push(desc);
      expect(
        await focusIsInside(page, '[role="dialog"]'),
        `第 ${i + 1} 次 Shift+Tab 之后焦点跑到了面板外（${desc}）—— 焦点陷阱漏了`,
      ).toBe(true);
    }

    console.log('\n   [焦点陷阱] DeleteNoteDialog 面板内的循环：');
    console.log(`     正向：${[...new Set(forward)].join(' → ')}`);
    console.log(`     反向：${[...new Set(backward)].join(' → ')}`);
  });

  test('Esc 关闭后焦点回到触发它的那个按钮上', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    const trigger = page.getByRole('button', { name: /^删除 / }).first();
    await trigger.click();
    await expect(page.getByRole('dialog')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    // 焦点"交给 body"是常见的退化结果：用户按 Tab 会从**页首**重新开始，
    // 而不是从刚才那一行继续 —— 在一张 200 行的列表里这是灾难性的。
    const restored = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return {
        tag: el?.tagName.toLowerCase() ?? '(none)',
        label: el?.getAttribute('aria-label') ?? '',
      };
    });
    expect(
      restored.label,
      `Esc 关闭后焦点落到了 ${restored.tag}${restored.label ? `[${restored.label}]` : ''}，` +
        '而不是刚才那个「删除 …」按钮上 —— 键盘用户会从页首重新开始 Tab',
    ).toMatch(/^删除 /);
  });
});

// ---------------------------------------------------------------------------

test.describe('焦点环：键盘聚焦时**看得见**焦点', () => {
  /**
   * `a11y-audit.md` §4.1 写着「"焦点环画出来长什么样"仍然没有断言 ——
   * 本次是在真 Chromium 里读 `getComputedStyle` 人工核对」。
   * 这一条把那件"人工核对"变成门禁。
   *
   * ## 判据为什么是"有环"而不是"环等于某个具体值"
   *
   * 具体值属于视觉设计，改了配色就会变（`--color-accent` 一改就红）——
   * 那种红没有信息量。而"**键盘聚焦时到底有没有可见的环**"是 a11y 判据：
   * 没有它，键盘用户不知道焦点在哪（WCAG 2.4.7）。
   *
   * ## ⚠️ 必须用**键盘**聚焦，不能用 `.focus()`
   *
   * `:focus-visible` 的匹配规则由浏览器决定：程序化 `focus()` 通常**不**匹配
   * `:focus-visible`（除非元素本来就该显示）。所以这里真的按 Tab，
   * 再读样式 —— 读的才是键盘用户看到的那一份。
   */
  test('键盘 Tab 到控件时，聚焦**确实改变**了绘制（WCAG 2.4.7）', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    /**
     * ## ⚠️ 这一版为什么不再"测一个就 blur 一下、然后重新定位"
     *
     * 第一版那样写，实测**不稳定**：`blur()` 会把焦点丢掉，而"顺序焦点导航起点"
     * 随之改变，于是重新"点 h1 + 按 i 次 Tab"落到的不一定是同一个元素。
     * 同一次改动两次运行，量到的控件集合都不一样（第一次报了一个失败、
     * 第二次六个全过）—— **一个会自己变绿变红的用例比没有用例更糟**。
     *
     * 改成：**先一次性读出失焦态**（此刻大家都在失焦，读到的就是基准），
     * 再从头 Tab 一遍、**中途不 blur**，走到谁就把聚焦态与基准对齐比较。
     * 顺序由浏览器决定且只走一遍，因此可复现。
     */
    const CANDIDATE_SEL =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])';

    // 基准：给待测元素打标记并读失焦态（此时焦点在 body，没人在聚焦态）
    const baseline = await page.evaluate((sel) => {
      const els = Array.from(document.querySelectorAll(`main ${sel}`)).filter(
        (el) => !el.closest('[inert]'),
      );
      return els.slice(0, 6).map((el, i) => {
        el.setAttribute('data-fp-index', String(i));
        const cs = getComputedStyle(el);
        const name = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 20);
        return {
          index: i,
          name,
          tag: el.tagName.toLowerCase(),
          outlineStyle: cs.outlineStyle,
          outlineWidth: cs.outlineWidth,
          outlineColor: cs.outlineColor,
          boxShadow: cs.boxShadow,
        };
      });
    }, CANDIDATE_SEL);

    expect(baseline.length, 'main 里没有找到任何可聚焦控件').toBeGreaterThan(0);
    console.log(`\n   [焦点环] main 里待测控件 ${baseline.length} 个（按 DOM 顺序）`);
    for (const b of baseline) {
      console.log(
        `     ${b.index + 1}. ${b.tag}「${b.name}」失焦态：${paintLine({ ...b, focusVisible: false })}`,
      );
    }

    // 从头 Tab 一遍，中途不 blur（保持浏览器自己的顺序）
    await page.locator('main h1').first().click();

    const seen = new Map<number, FocusPaint>();
    for (let i = 0; i < 60 && seen.size < baseline.length; i++) {
      await page.keyboard.press('Tab');
      const hit = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el) return null;
        const idx = el.getAttribute('data-fp-index');
        if (idx === null) return null;
        const cs = getComputedStyle(el);
        return {
          index: Number(idx),
          focusVisible: el.matches(':focus-visible'),
          outlineStyle: cs.outlineStyle,
          outlineWidth: cs.outlineWidth,
          outlineColor: cs.outlineColor,
          boxShadow: cs.boxShadow,
        } as FocusPaint & { index: number };
      });
      if (hit && !seen.has(hit.index)) {
        const { index, ...paint } = hit;
        seen.set(index, paint as FocusPaint);
      }
    }

    const failures: string[] = [];
    for (const b of baseline) {
      const focused = seen.get(b.index);
      if (!focused) {
        failures.push(`${b.tag}「${b.name}」：60 次 Tab 都没走到它（顺序/scoping 变了？）`);
        continue;
      }
      const before = { ...b, focusVisible: false };
      const changed = focusPaintChanged(before, focused);
      const strong = focusIndicatorIsStrong(before, focused);
      console.log(`\n   [焦点环] ${b.tag}「${b.name}」`);
      console.log(`     失焦：${paintLine(before)}`);
      console.log(`     聚焦：${paintLine(focused)}`);
      console.log(`     聚焦是否改变绘制：${changed ? '是' : '★ 否'}`);
      console.log(`     指示是否被项目加强（≥2px 或有阴影变化）：${strong ? '是' : '★ 否'}`);
      if (!changed || !strong) {
        failures.push(
          `${b.tag}「${b.name}」${!changed ? '【绘制没变化】' : ''}${!strong ? '【只剩 UA 默认的 1px，项目焦点环没生效】' : ''}` +
            `\n      失焦：${paintLine(before)}\n      聚焦：${paintLine(focused)}`,
        );
      }
    }

    expect(
      failures,
      `下面这些控件的键盘焦点指示不达标（WCAG 2.4.7）：\n  ${failures.join('\n  ')}`,
    ).toEqual([]);
  });

  test('★ 侧边栏导航项聚焦时也能看出变化（它们是文档顺序里最先被 Tab 到的一批）', async ({
    page,
  }) => {
    await installA11yStubs(page);
    await loginAs(page, '/');
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible();

    /**
     * ⚠️ 这里**不能**点 `main h1` 当锚点。
     *
     * 第一版就是那么写的，结果探针打印出来的是
     * `侧边栏首站 button「上传新资料」inNav=false` —— 它测的其实是 **main 里**的元素，
     * 而用例名字却写着"侧边栏"。踩的正是本文件上面记过的那个坑：
     * 点 `main h1` 会把顺序导航起点设到 **main 内部**，Tab 从那里往后走，
     * 于是**永远绕开了侧边栏**。
     *
     * 要测侧边栏就得用"文档顺序"的起手式：什么都不点，第一次 Tab 就是页首第一个
     * 可聚焦元素 —— 实测就是 `nav` 里的 `EngramNote` 按钮。
     */
    await page.locator('body').click({ position: { x: 2, y: 2 } });
    await page.keyboard.press('Tab');

    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      return {
        tag: el.tagName.toLowerCase(),
        label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 20),
        inNav: !!el.closest('nav'),
      };
    });

    expect(info, '第一次 Tab 没有落到任何元素上').not.toBeNull();
    // 这条断言本身也是判据：第一次 Tab 必须落在 nav 里（文档顺序事实）
    expect(
      info!.inNav,
      `第一次 Tab 落到了 ${info!.tag}「${info!.label}」，它**不在 nav 里** —— ` +
        '文档顺序变了，侧边栏不再是 Tab 路径的第一站',
    ).toBe(true);

    const { focused, blurred } = await measureFocusPaint(page);

    console.log(`\n   [焦点环] 文档顺序首站 ${info!.tag}「${info!.label}」inNav=${info!.inNav}`);
    console.log(`     聚焦：${paintLine(focused)}`);
    console.log(`     失焦：${paintLine(blurred)}`);

    expect(
      focusPaintChanged(blurred, focused),
      `侧边栏第一个可聚焦元素（${info!.tag}「${info!.label}」）被键盘聚焦时**绘制没有变化** ——` +
        ` 键盘用户看不出焦点在哪：\n  聚焦：${paintLine(focused)}\n  失焦：${paintLine(blurred)}`,
    ).toBe(true);

    // 与上面那条同源：指示强度也要够（只靠 UA 默认的 1px 不算项目焦点环生效）
    expect(
      focusIndicatorIsStrong(blurred, focused),
      `侧边栏第一个可聚焦元素（${info!.tag}「${info!.label}」）的焦点指示**没有被项目加强** ——` +
        ` 只剩 UA 默认的 1px，项目那条 :focus-visible 规则没生效：\n` +
        `  聚焦：${paintLine(focused)}\n  失焦：${paintLine(blurred)}`,
    ).toBe(true);
  });
});
