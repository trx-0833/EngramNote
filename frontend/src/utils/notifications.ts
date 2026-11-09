/**
 * @file 浏览器通知工具
 * @description 封装浏览器 Notification API，提供桌面通知能力。
 * 包含权限请求、通知发送、免打扰时段判断。
 * 去重只按 due_count 值变化判断，不再依赖已废弃的 sessionStorage 去重，见 docs/decisions.md#F-25。
 */

import { getDevicePrefs } from './devicePrefs';

export type NotificationPermissionState = 'unsupported' | 'default' | 'granted' | 'denied';

/** 当前浏览器通知权限状态（不用 `Notification.permission` 直接渲染，避免不支持时抛错） */
export function getNotificationPermission(): NotificationPermissionState {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission;
}

/** 发送一条测试通知；返回是否真的发出（未授权/不支持时 false） */
export function sendTestNotification(title: string, body: string): boolean {
  if (getNotificationPermission() !== 'granted') return false;
  showNotification(title, body);
  return true;
}

/**
 * 请求浏览器通知权限
 * @returns 是否已授权
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (!('Notification' in window)) {
    console.warn('当前浏览器不支持桌面通知');
    return false;
  }
  if (Notification.permission === 'granted') {
    return true;
  }
  if (Notification.permission !== 'denied') {
    const result = await Notification.requestPermission();
    return result === 'granted';
  }
  return false;
}

/**
 * 显示桌面通知
 * @param title - 通知标题
 * @param body - 通知正文
 */
export function showNotification(title: string, body: string): void {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return;
  }
  try {
    new Notification(title, {
      body,
      icon: '/favicon.ico',
      tag: 'engramnote-review',
    });
  } catch (err) {
    console.warn('显示通知失败:', err);
  }
}

/**
 * 检查当前是否在免打扰时段
 *
 * 起止小时默认来自设备本地偏好（设置页可改）；调用方仍可显式传参，
 * 保留给测试与将来的"临时静默"场景。
 * @param startHour - 免打扰开始小时（默认读 devicePrefs）
 * @param endHour - 免打扰结束小时（默认读 devicePrefs）
 * @returns 是否在免打扰时段
 */
export function isInQuietHours(
  startHour: number = getDevicePrefs().quietStart,
  endHour: number = getDevicePrefs().quietEnd,
): boolean {
  const hour = new Date().getHours();
  if (startHour === endHour) return false;
  if (startHour > endHour) {
    // 跨天，如 22-8
    return hour >= startHour || hour < endHour;
  }
  // 同天，如 13-14
  return hour >= startHour && hour < endHour;
}
