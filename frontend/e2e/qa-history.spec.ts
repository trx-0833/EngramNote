/**
 * @file 问答页的历史抽屉与追问提示（docs/qa-history-plan.md §8.9）
 *
 * ## 这一层测什么、为什么单元测试不够
 *
 * `QA.history.test.tsx` 已经把文案与交互钉住了，但它跑在 jsdom 里：
 * 抽屉用的是真 `<dialog>`（`Dialog.tsx`），焦点陷阱、`Esc` 关闭、真实布局可见性
 * 在 jsdom 里都是近似实现。
 *
 * 所以这里用真浏览器只验证四件"jsdom 测不准"的事：
 * 1. 抽屉能打开、且**可见**（`toBeVisible` 走真实布局）；
 * 2. `Esc` 能关，且焦点回到「历史」按钮（键盘用户的回路）；
 * 3. 会话项可以 **Tab 到达**（不是"有 role=button"这种自证）；
 * 4. 两句提示在真实排版下不换行到看不见的地方（`toBeVisible` 而非"在 DOM 里"）。
 *
 * 桩全部来自 `a11y-fixtures.ts` —— 与 a11y 审计共用同一份，避免两处数据漂移。
 */
import { expect, test, type Locator, type Page } from '@playwright/test';

import { installA11yStubs, loginAs } from './a11y-fixtures';

/**
 * 从"抽屉标题"这个不可聚焦的锚点开始 Tab，断言 `target` 能被键盘到达
 *
 * 为什么不用 `a11y.spec.ts` 里那个 `main h1` 锚点：抽屉是模态的，
 * 遮罩盖住了 h1，点它会被拦截（那份文件里 `learning-goals-create` 场景
 * 记过同一条限制）。抽屉内部同样需要一个不可聚焦的锚点，这里用它的标题 `h2`。
 */
async function expectReachableByTabInsideDialog(
  page: Page,
  dialog: Locator,
  target: Locator,
  label: string,
  maxTabs = 40,
): Promise<void> {
  const handle = await target.first().elementHandle();
  expect(handle, `${label}：目标元素不在 DOM 里，键盘门禁不成立`).not.toBeNull();

  await dialog.getByRole('heading').first().click();
  for (let i = 0; i < maxTabs; i++) {
    await page.keyboard.press('Tab');
    if (await handle!.evaluate((el) => el === document.activeElement)) return;
  }
  expect(
    await handle!.evaluate((el) => el === document.activeElement),
    `${label}：按了 ${maxTabs} 次 Tab 仍未到达（键盘用户点不到它）`,
  ).toBe(true);
}

test.describe('问答历史抽屉', () => {
  test('打开 → 列出会话 → Esc 关闭且焦点回到「历史」', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/qa');

    const historyButton = page.getByRole('button', { name: '历史' });
    await expect(historyButton).toBeVisible();

    // 追问提示常驻可见（计划 §5.4 第 1 句）
    await expect(page.getByText(/追问会带上最近 \d+ 轮对话/)).toBeVisible();

    await historyButton.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('浮充和均充有什么区别？')).toBeVisible();
    // `title: null` 的那条显示「未命名对话」而不是空白行
    await expect(dialog.getByText('未命名对话')).toBeVisible();
    // 保留期提示（计划 §5.4 第 2 句）
    await expect(dialog.getByText(/问答历史保留 \d+ 天/)).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    // 关闭后焦点必须回到触发按钮（否则键盘用户要从头 Tab 一遍）
    await expect(historyButton).toBeFocused();
  });

  test('会话项可键盘到达', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/qa');

    await page.getByRole('button', { name: '历史' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    await expectReachableByTabInsideDialog(
      page,
      dialog,
      dialog.getByRole('button', { name: /浮充和均充有什么区别？/ }),
      '问答历史：会话项',
    );
  });
});
