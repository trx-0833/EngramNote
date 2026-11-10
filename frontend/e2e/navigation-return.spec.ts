/**
 * 导航往返：从笔记详情「返回」回到列表后，筛选与位置还在（2026-11-10）
 *
 * ## 为什么要一条 e2e
 *
 * 这件事的判据是**浏览器历史**与**地址栏**：单测里 `MemoryRouter` 没有真实
 * `history.state.idx`、也没有真的 POP，证明不了"后退一步能带着参数回来"。
 * 单位测试守的是 Hook 的判据（`useListScrollRestore.test.tsx` 里那些），
 * 这一条守的是**整条链路**：列表 → 详情 → 后退 → 同一个视图。
 *
 * ## 断言的是什么
 *
 * | 步骤 | 期望 |
 * |---|---|
 * | 在列表页输入关键词 | 地址栏出现 `q=`（筛选写进 URL，可分享/可刷新） |
 * | 点进一篇笔记 | 详情页；地址栏变化 |
 * | 点「返回」 | 回到**带 `q=` 的那个 URL**，搜索框里仍是那个词 |
 *
 * 只断言"搜索框仍有值 + URL 仍带 q"：这两条直接对应"用户不用重新筛一遍"。
 * 滚动位置由 `useListScrollRestore` 的用例覆盖（列表只有两条笔记，页面本来
 * 就不够高，浏览器里也无法真的滚出可恢复的距离）。
 */
import { expect, test } from '@playwright/test';

import { installA11yStubs, loginAs } from './a11y-fixtures';

test.describe('导航往返：返回列表保留筛选', () => {
  test('★ 搜索 → 进详情 → 返回：关键词与 URL 都还在', async ({ page }) => {
    await installA11yStubs(page);
    await loginAs(page, '/notes');

    const search = page.getByRole('searchbox');
    await search.fill('电池');

    // ① 筛选进了 URL（可以复制给别人、也可以在刷新后保留）
    await expect(page).toHaveURL(/[?&]q=/);

    // ② 进详情
    await page.getByRole('link', { name: /锂离子电池的浮充与均充/ }).click();
    await expect(page).toHaveURL(/\/notes\/note-1$/);

    // ③ 返回：走浏览器后退（按钮里也是 navigate(-1)）
    await page.getByRole('button', { name: '返回' }).click();

    await expect(page).toHaveURL(/\/notes\?.*q=/);
    // 关键词仍在搜索框里 —— 用户不用重新输入
    await expect(page.getByRole('searchbox')).toHaveValue('电池');
  });
});
