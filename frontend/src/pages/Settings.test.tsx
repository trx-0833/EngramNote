/**
 * 设置页（PC）交互测试
 *
 * 重点不是"渲染出文字"，而是**写入是否真的带着正确的段与字段**：
 * 设置页的每一步都会打到 `PATCH /api/settings`，服务层据此联动 review /
 * reminder；这里用 mock 把请求形状钉住。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Settings from './Settings';
import { renderWithProviders } from '../test/render';
import { LocaleProvider } from '../i18n';
import { ToastProvider } from '../components/Toast';
import { ConfirmProvider } from '../components/ConfirmProvider';
import { AuthProvider } from '../contexts/AuthContext';
import { getSettings, getSettingsUsage, patchSettings } from '../api/settings';
import { setDevicePref } from '../utils/devicePrefs';

vi.mock('../api/settings', () => ({
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getSettingsUsage: vi.fn(),
}));

const mockedGet = vi.mocked(getSettings);
const mockedPatch = vi.mocked(patchSettings);
const mockedUsage = vi.mocked(getSettingsUsage);

const BASE = {
  locale: 'zh-CN',
  study: { daily_review_limit: 10 },
  reminders: { email_enabled: true, email_hour: 9, email_configured: true },
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
  mockedPatch.mockImplementation(async (body) => ({
    ...BASE,
    study: {
      daily_review_limit:
        typeof body.study?.daily_review_limit === 'number'
          ? body.study.daily_review_limit
          : BASE.study.daily_review_limit,
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
  }));
});

describe('Settings 页', () => {
  it('渲染设置快照（账号、版本、存储用量）', async () => {
    renderSettings();
    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习节奏' })).toBeInTheDocument();
    expect(await screen.findByText('lin@example.com')).toBeInTheDocument();
    expect(screen.getByText('0.1.0')).toBeInTheDocument();
    expect(screen.getByText('1.00 GB / 5.00 GB')).toBeInTheDocument();
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

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith({ reminders: { email_hour: 7 } }),
    );
  });

  it('切换字号只写设备本地，不调用后端', async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole('button', { name: '大' }));

    expect(document.documentElement.style.fontSize).toBe('18px');
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
});
