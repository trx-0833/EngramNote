/**
 * 设置页（PC）交互测试
 *
 * 重点不是"渲染出文字"，而是**写入是否真的带着正确的段与字段**：
 * 设置页的每一步都会打到 `PATCH /api/settings`，服务层据此联动 review /
 * reminder；这里用 mock 把请求形状钉住。
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Settings from './Settings';
import { renderWithProviders } from '../test/render';
import { LocaleProvider } from '../i18n';
import { ToastProvider } from '../components/Toast';
import { ConfirmProvider } from '../components/ConfirmProvider';
import { AuthProvider } from '../contexts/AuthContext';
import { getSettings, getSettingsUsage, patchSettings } from '../api/settings';
import { getReviewStats } from '../api/review';
import { getLlmUsage } from '../api/llm';
import { getDevicePrefs, setDevicePref } from '../utils/devicePrefs';

vi.mock('../api/settings', () => ({
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getSettingsUsage: vi.fn(),
}));
vi.mock('../api/review', () => ({ getReviewStats: vi.fn() }));
vi.mock('../api/llm', () => ({ getLlmUsage: vi.fn() }));

const mockedGet = vi.mocked(getSettings);
const mockedPatch = vi.mocked(patchSettings);
const mockedUsage = vi.mocked(getSettingsUsage);
const mockedStats = vi.mocked(getReviewStats);
const mockedLlm = vi.mocked(getLlmUsage);

const BASE = {
  locale: 'zh-CN',
  study: {
    daily_review_limit: 10,
    desired_retention: 0.9,
    max_interval_days: 3650,
  },
  reminders: { email_enabled: true, email_hour: 9, email_configured: true },
  content: {
    output_locale: 'follow' as const,
    card_max_new_per_run: 500,
    clean_simplify_formula: false,
    clean_normalize_spacing: true,
    clean_normalize_heading_levels: true,
  },
  account: { email: 'lin@example.com', username: 'lin' },
  about: { app_version: '0.1.0', app_env: 'prod' },
};

function Providers({ children }: { children: React.ReactNode }) {
  return (
    <LocaleProvider>
      <ToastProvider>
        <ConfirmProvider>
          <AuthProvider>{children}</AuthProvider>
        </ConfirmProvider>
      </ToastProvider>
    </LocaleProvider>
  );
}

function renderSettings() {
  return renderWithProviders(
    <Providers>
      <Settings />
    </Providers>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem('engramnote:device-prefs:v1');
  document.documentElement.style.fontSize = '';
  window.history.replaceState(null, '', '/');
  Element.prototype.scrollIntoView = vi.fn();
  setDevicePref('fontScale', 'md');
  setDevicePref('reduceMotion', false);
  setDevicePref('sidebarCollapsed', false);
  setDevicePref('adhdDefault', false);
  setDevicePref('desktopNotifications', true);
  setDevicePref('quietStart', 22);
  setDevicePref('quietEnd', 8);
  mockedGet.mockResolvedValue(BASE);
  mockedUsage.mockResolvedValue({
    storage: { used_bytes: 1024 ** 3, quota_bytes: 5 * 1024 ** 3 },
  });
  mockedStats.mockResolvedValue({
    today_done: 3,
    daily_limit: 10,
    due_count: 4,
    today_correct: 2,
    today_accuracy: 66.7,
    total_reviews: 30,
    total_correct: 20,
    total_accuracy: 66.7,
    total_quizzes: 40,
  } as never);
  mockedLlm.mockResolvedValue({
    since: '2026-10-06T00:00:00Z',
    until: '2026-11-05T00:00:00Z',
    group_by: 'none',
    currency: null,
    totals: {
      calls: 12,
      total_tokens: 150,
      cost: 0,
    },
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
  } as never);
  mockedPatch.mockImplementation(async (body) => ({
    ...BASE,
    study: {
      ...BASE.study,
      daily_review_limit:
        typeof body.study?.daily_review_limit === 'number'
          ? body.study.daily_review_limit
          : BASE.study.daily_review_limit,
      desired_retention:
        typeof body.study?.desired_retention === 'number'
          ? body.study.desired_retention
          : BASE.study.desired_retention,
      max_interval_days:
        typeof body.study?.max_interval_days === 'number'
          ? body.study.max_interval_days
          : BASE.study.max_interval_days,
    },
    reminders: {
      ...BASE.reminders,
      email_enabled:
        typeof body.reminders?.email_enabled === 'boolean'
          ? body.reminders.email_enabled
          : BASE.reminders.email_enabled,
      email_hour:
        typeof body.reminders?.email_hour === 'number'
          ? body.reminders.email_hour
          : BASE.reminders.email_hour,
    },
    content: {
      ...BASE.content,
      output_locale: body.content?.output_locale ?? BASE.content.output_locale,
      card_max_new_per_run:
        typeof body.content?.card_max_new_per_run === 'number'
          ? body.content.card_max_new_per_run
          : BASE.content.card_max_new_per_run,
      clean_simplify_formula:
        typeof body.content?.clean_simplify_formula === 'boolean'
          ? body.content.clean_simplify_formula
          : BASE.content.clean_simplify_formula,
      clean_normalize_spacing:
        typeof body.content?.clean_normalize_spacing === 'boolean'
          ? body.content.clean_normalize_spacing
          : BASE.content.clean_normalize_spacing,
      clean_normalize_heading_levels:
        typeof body.content?.clean_normalize_heading_levels === 'boolean'
          ? body.content.clean_normalize_heading_levels
          : BASE.content.clean_normalize_heading_levels,
    },
  }));
});

describe('Settings 页', () => {
  it('渲染设置快照（账号、版本、存储用量）', async () => {
    renderSettings();
    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习节奏' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '内容与生成' })).toBeInTheDocument();
    expect(await screen.findByText('lin@example.com')).toBeInTheDocument();
    expect(screen.getByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByText('1.00 GB / 5.00 GB')).toBeInTheDocument();
    expect(screen.getByText('已结算 3 / 10 道 · 还剩 7 道')).toBeInTheDocument();
    expect(screen.getByText('150 tokens · 12 次调用')).toBeInTheDocument();
    expect(screen.getByText('未配置单价')).toBeInTheDocument();
    expect(screen.getByText('当前浏览器不支持桌面通知。')).toBeInTheDocument();
  });

  it('修改每日上限：PATCH 只带 study.daily_review_limit', async () => {
    const user = userEvent.setup();
    renderSettings();

    const input = await screen.findByLabelText('每日复习上限');
    await user.clear(input);
    await user.type(input, '20');
    await user.tab();

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({ study: { daily_review_limit: 20 } }),
    );
    expect(await screen.findByText('已保存')).toBeInTheDocument();
  });

  it('越界值不发请求，输入框回到服务端值', async () => {
    const user = userEvent.setup();
    renderSettings();

    const input = (await screen.findByLabelText('每日复习上限')) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, '4');
    await user.tab();

    expect(mockedPatch).not.toHaveBeenCalled();
    expect(input.value).toBe('10');
  });

  it('关闭邮件提醒：PATCH 只带 reminders.email_enabled', async () => {
    const user = userEvent.setup();
    renderSettings();

    const toggle = await screen.findByRole('checkbox', { name: '邮件提醒' });
    await user.click(toggle);

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({ reminders: { email_enabled: false } }),
    );
  });

  it('修改发送时间：PATCH 只带 reminders.email_hour', async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.selectOptions(await screen.findByLabelText('发送时间'), '7');

    await waitFor(() => expect(mockedPatch).toHaveBeenCalledWith({ reminders: { email_hour: 7 } }));
  });

  it('高级排期：留存率和最长间隔分别提交', async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.selectOptions(await screen.findByLabelText('目标留存率'), '0.95');
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({
        study: { desired_retention: 0.95 },
      }),
    );

    await user.selectOptions(await screen.findByLabelText('最长间隔'), '365');
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({
        study: { max_interval_days: 365 },
      }),
    );
  });

  it('内容与生成：语言、卡片上限、清洗开关都走 content 段', async () => {
    const user = userEvent.setup();
    renderSettings();

    const contentRegion = await screen.findByRole('region', { name: '内容与生成' });
    await user.click(within(contentRegion).getByRole('button', { name: 'English' }));
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({
        content: { output_locale: 'en' },
      }),
    );

    const cap = await screen.findByLabelText('单次生成卡片上限');
    await user.clear(cap);
    await user.type(cap, '50');
    await user.tab();
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({
        content: { card_max_new_per_run: 50 },
      }),
    );

    await user.click(await screen.findByRole('checkbox', { name: '简化公式' }));
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({
        content: { clean_simplify_formula: true },
      }),
    );
  });

  it('切换字号只写设备本地，不调用后端', async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole('button', { name: '大' }));

    expect(document.documentElement.style.fontSize).toBe('18px');
    expect(mockedPatch).not.toHaveBeenCalled();
  });

  it('ADHD 默认开关写入设备偏好（供笔记页初始化）', async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole('checkbox', { name: 'ADHD Reader 默认开启' }));
    expect(getDevicePrefs().adhdDefault).toBe(true);
    expect(mockedPatch).not.toHaveBeenCalled();
  });

  it('点左栏分组：滚动到对应卡片，更新 hash 与 aria-current', async () => {
    const user = userEvent.setup();
    renderSettings();

    const account = await screen.findByRole('button', { name: /账号与安全/ });
    await user.click(account);

    expect(window.location.hash).toBe('#account');
    expect(account).toHaveAttribute('aria-current', 'page');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('深链接 #account：数据到位后高亮账号组', async () => {
    window.location.hash = '#account';
    renderSettings();

    const account = await screen.findByRole('button', { name: /账号与安全/ });
    await waitFor(() => expect(account).toHaveAttribute('aria-current', 'page'));
  });

  it('保存失败时把输入框恢复为服务端值', async () => {
    mockedPatch.mockRejectedValueOnce(new Error('boom'));
    const user = userEvent.setup();
    renderSettings();

    const input = (await screen.findByLabelText('每日复习上限')) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, '30');
    await user.tab();

    await waitFor(() => expect(input.value).toBe('10'));
  });

  it('三处只读数据失败时显示错误与重试，而不是永久加载中', async () => {
    mockedStats.mockRejectedValueOnce(new Error('stats down'));
    mockedUsage.mockRejectedValueOnce(new Error('usage down'));
    mockedLlm.mockRejectedValueOnce(new Error('llm down'));
    renderSettings();

    const alerts = await screen.findAllByRole('alert');
    expect(alerts).toHaveLength(3);
    for (const alert of alerts) {
      expect(alert).toHaveTextContent('读取失败，请重试。');
    }
    expect(screen.getAllByRole('button', { name: '重试' })).toHaveLength(3);
    expect(screen.queryByText('读取中…')).not.toBeInTheDocument();
  });
});
