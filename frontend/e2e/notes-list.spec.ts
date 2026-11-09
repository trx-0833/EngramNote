/**
 * 笔记列表：整卡可点（拉伸链接，2026-11-06）
 *
 * ## 这一层为什么不可省
 *
 * "点卡片空白处也能进详情"靠的是标题 `<Link>` 的 `::after` 铺满整张卡片。
 * jsdom 没有布局、没有伪元素命中测试 —— 在单测里**证明不了**那一下点中的
 * 到底是链接、是卡片、还是按钮。`NotesList.test.tsx` 只能守结构前提
 * （卡片里唯一的链接、卡片不是控件），真正的那一下必须在这里点。
 *
 * ## 为什么用 `page.mouse.click(x, y)` 而不是 `locator.click()`
 *
 * 拉伸链接的覆盖层是**链接的伪元素**，不在被覆盖元素的子树里。
 * `locator.click()` 会做"命中目标必须是该元素或其后代"的校验，于是对
 * "点元信息文字"这种操作会报 *intercepts pointer events* —— 那是 Playwright
 * 的守卫，不是产品问题，但会把一个正常行为测成失败。
 * 直接按坐标点，等于**用户的鼠标**：不预设命中谁。
 *
 * 判据也因此更强：坐标落在卡片中部（既不是标题文字，也不是右侧按钮），
 * 导航仍然发生 ⇒ 点击面真的铺满了这一行。
 */
import { expect, test } from '@playwright/test';

import { installA11yStubs, loginAs } from './a11y-fixtures';

const TITLE = '锂离子电池的浮充与均充';

test.describe('笔记列表：整卡可点', () => {
  test('★★ 点卡片中部的空白处（不是标题、不是按钮）也能进详情', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    const row = page.locator('article').filter({ hasText: TITLE });
    await expect(row).toHaveCount(1);

    const box = await row.boundingBox();
    expect(box, '卡片没有渲染出可点击的盒子').not.toBeNull();

    // 横向 45%：标题只占左侧一小段，右侧 1/3 才是「重试/删除/箭头」。
    // 纵向居中：落在元信息行上 —— 一行"看起来不可点"的文字。
    await page.mouse.click(box!.x + box!.width * 0.45, box!.y + box!.height / 2);

    await expect(page).toHaveURL(/\/notes\/note-1$/);
    await expect(page.getByRole('heading', { name: /浮充与均充/ }).first()).toBeVisible();
  });

  test('★ 点「删除」不会顺手进详情（按钮浮在拉伸链接之上）', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    const row = page.locator('article').filter({ hasText: TITLE });
    await row.getByRole('button', { name: /删除/ }).click();

    // 删除是"打开确认框"，不是导航：覆盖层若盖住了按钮，这里会直接跳走
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page).toHaveURL(/\/notes$/);
  });

  test('★ 右箭头常驻：操作按钮还藏着时它已经可见', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    const row = page.locator('article').filter({ hasText: TITLE });
    // 鼠标停在页面左上角（不在任何卡片上）
    await page.mouse.move(0, 0);

    const chevron = row.getByTestId('note-chevron');
    await expect(chevron).toBeVisible();
    await expect(chevron.locator('svg')).toHaveCount(1);

    // 操作按钮此时仍是透明的 —— 判据取**父盒**的 opacity：
    // `opacity` 不是继承属性，按钮自己的计算值永远是 1，只有父盒那层 0
    // 才是"它还藏着"的证据（踩过一次：直接断言按钮的 opacity 拿到 1）。
    const actionsOpacity = await row
      .getByRole('button', { name: /删除/ })
      .evaluate((el) => getComputedStyle(el.parentElement!).opacity);
    expect(actionsOpacity).toBe('0');
  });

  test('★ 键盘焦点在标题链接上，光环画在整张卡上', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');
    await expect(page.getByText('共 2 条')).toBeVisible();

    // 从文档开头一路 Tab 到笔记标题（侧栏在前，约 20 个焦点停靠点）
    let reached = false;
    for (let i = 0; i < 60 && !reached; i++) {
      await page.keyboard.press('Tab');
      reached = await page.evaluate(
        (title) => (document.activeElement?.textContent ?? '').includes(title),
        TITLE,
      );
    }
    expect(reached, 'Tab 到不了笔记标题链接').toBe(true);

    const outline = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const card = el.closest('article')!;
      return {
        linkStyle: getComputedStyle(el).outlineStyle,
        cardWidth: getComputedStyle(card).outlineWidth,
      };
    });

    // 链接自己的盒子只有标题那么大：光环贴着一个词会让人以为"只有标题可点"。
    // ⚠️ 只断言 `outline-style`：`outline-width` 在 style:none 时仍是初始值
    // `medium`（实测计算值 "3px"），拿它当"没有光环"的判据会误判。
    expect(outline.linkStyle).toBe('none');
    expect(outline.cardWidth).toBe('2px');
  });
});
