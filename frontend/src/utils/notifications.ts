/**
 * @file 浏览器通知工具
 * @description 封装浏览器 Notification API，提供桌面通知能力。
 * 包含权限请求、通知发送、免打扰时段判断。
 * F-25：移除已废弃的 sessionStorage 去重（getNotifiedQuizIds/markNotified），
 * 去重职责由 ReminderBanner 的 lastNotifiedDueRef（按 due_count 值变化）承担。
 */

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
 * 免打扰时间默认为 22:00 - 08:00
 * @param startHour - 免打扰开始小时（默认 22）
 * @param endHour - 免打扰结束小时（默认 8）
 * @returns 是否在免打扰时段
 */
export function isInQuietHours(startHour = 22, endHour = 8): boolean {
  const hour = new Date().getHours();
  if (startHour > endHour) {
    // 跨天，如 22-8
    return hour >= startHour || hour < endHour;
  }
  // 同天，如 13-14
  return hour >= startHour && hour < endHour;
}
