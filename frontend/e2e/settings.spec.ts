/**
 * 设置页 E2E（stub API）
 *
 * 这里验证的是**浏览器链路**：登录态 → 路由 → 六组渲染 → 控件把 PATCH
 * 发成什么形状 → 页面用服务端返回更新。后端是否真的联动由
 * `backend/tests/test_settings_api.py` 与任务级测试负责。
 */
import { expect, test, type Page } from '@playwright/test';

const BASE = {
  locale: 'zh-CN',
  study: {
    daily_review_limit: 10,
    desired_retention: 0.9,
    max_interval_days: 3650,
  },
  reminders: { email_enabled: true, email_hour: 9, email_configured: true },
  content: {
    output_locale: 'follow',
    card_max_new_per_run: 500,
    clean_simplify_formula: false,
    clean_normalize_spacing: true,
    clean_normalize_heading_levels: true,
  },
  account: { email: 'e2e@example.com', username: 'e2e' },
  about: { app_version: '0.1.0', app_env: 'test' },
};

async function setup(page: Page): Promise<{ patches: unknown[] }> {
  const patches: unknown[] = [];
  let current = structuredClone(BASE);

  await page.addInitScript(() => {
    localStorage.setItem('engramnote_token', 'e2e-access-token');
    localStorage.setItem('engramnote_refresh_token', 'e2e-refresh-token');
  });

  await page.route(
    (url) => url.pathname === '/api/settings' || url.pathname.startsWith('/api/settings/'),
    async (route) => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const json = (body: unknown) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

      if (url.pathname === '/api/settings/usage') {
        await json({ storage: { used_bytes: 1024 ** 2, quota_bytes: 5 * 1024 ** 3 } });
        return;
      }
      if (url.pathname !== '/api/settings') {
        await json({});
        return;
      }
      if (method === 'PATCH') {
        const body = route.request().postDataJSON() as {
          study?: Partial<typeof BASE.study>;
          reminders?: Partial<typeof BASE.reminders>;
          content?: Partial<typeof BASE.content>;
        };
        patches.push(body);
        current = {
          ...current,
          study: { ...current.study, ...(body.study ?? {}) },
          reminders: { ...current.reminders, ...(body.reminders ?? {}) },
          content: { ...current.content, ...(body.content ?? {}) },
        };
      }
      await json(current);
    },
  );

  await page.route('**/api/review/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        due_count: 4,
        today_done: 3,
        today_correct: 2,
        today_accuracy: 66.7,
        total_reviews: 30,
        total_correct: 20,
        total_accuracy: 66.7,
        total_quizzes: 40,
        daily_limit: current.study.daily_review_limit,
      }),
    });
  });

  await page.route('**/api/llm/usage**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        since: '2026-10-01T00:00:00Z',
        until: '2026-11-01T00:00:00Z',
        group_by: 'none',
        currency: null,
        totals: { calls: 12, total_tokens: 150, cost: 0 },
        groups: [],
        price_configured: false,
        quota: {
          cost_enforceable: false,
          cost_limit: 0,
          cost_used: 0,
          token_limit: 0,
          token_used: 0,
          unlimited: true,
        },
        cache: {},
      }),
    });
  });

  return { patches };
}

test('设置页六组渲染，改动发 PATCH 并回写', async ({ page }) => {
  const { patches } = await setup(page);
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();

  const nav = page.getByRole('navigation', { name: '设置分组' });
  await expect(nav.getByRole('button')).toHaveCount(6);
  await expect(page.getByRole('region', { name: '内容与生成' })).toBeVisible();
  await expect(page.getByText('已结算 3 / 10 道 · 还剩 7 道')).toBeVisible();

  const daily = page.getByLabel('每日复习上限');
  await daily.fill('25');
  await daily.blur();
  await expect.poll(() => patches).toContainEqual({ study: { daily_review_limit: 25 } });

  const content = page.getByRole('region', { name: '内容与生成' });
  await content.getByRole('button', { name: 'English' }).click();
  await expect.poll(() => patches).toContainEqual({ content: { output_locale: 'en' } });

  await page.getByRole('button', { name: '大' }).click();
  await expect(page.locator('html')).toHaveCSS('font-size', '18px');
});

test('深链接直接高亮账号组', async ({ page }) => {
  await setup(page);
  await page.goto('/settings#account');
  await expect(
    page.getByRole('navigation', { name: '设置分组' }).getByRole('button', { name: /账号与安全/ }),
  ).toHaveAttribute('aria-current', 'page');
});
