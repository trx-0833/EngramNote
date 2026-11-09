/**
 * 免打扰时段：跨天/同天/起止相同
 *
 * `ReminderBanner` 用它决定要不要弹桌面通知；设置页把起止小时写进
 * `devicePrefs` 后，这里必须按新的默认值生效。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { isInQuietHours, getNotificationPermission, sendTestNotification } from './notifications';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('isInQuietHours', () => {
  it('跨天 22–8：23 点在免打扰内，12 点不在', () => {
    vi.setSystemTime(new Date(2026, 10, 4, 23, 0));
    expect(isInQuietHours(22, 8)).toBe(true);

    vi.setSystemTime(new Date(2026, 10, 4, 12, 0));
    expect(isInQuietHours(22, 8)).toBe(false);
  });

  it('边界：22:00 算开始，08:00 算结束（左闭右开）', () => {
    vi.setSystemTime(new Date(2026, 10, 4, 22, 0));
    expect(isInQuietHours(22, 8)).toBe(true);

    vi.setSystemTime(new Date(2026, 10, 4, 8, 0));
    expect(isInQuietHours(22, 8)).toBe(false);
  });

  it('同天 13–14 只覆盖这一小时', () => {
    vi.setSystemTime(new Date(2026, 10, 4, 13, 30));
    expect(isInQuietHours(13, 14)).toBe(true);

    vi.setSystemTime(new Date(2026, 10, 4, 14, 0));
    expect(isInQuietHours(13, 14)).toBe(false);
  });

  it('起止相同表示关闭免打扰，而不是全天静默', () => {
    vi.setSystemTime(new Date(2026, 10, 4, 23, 0));
    expect(isInQuietHours(22, 22)).toBe(false);
  });

  it('浏览器不支持 Notification 时状态是 unsupported，测试通知返回 false', () => {
    expect(getNotificationPermission()).toBe('unsupported');
    expect(sendTestNotification('t', 'b')).toBe(false);
  });
});
