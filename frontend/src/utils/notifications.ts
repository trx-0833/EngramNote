/**
 * @file 浏览器通知工具
 * @description 封装浏览器 Notification API，提供桌面通知能力。
 * 包含权限请求、通知发送、免打扰时段判断、已通知题目去重（sessionStorage）。
 */

/** sessionStorage 中存储已通知题目 ID 的 key */
const NOTIFIED_KEY = 'notified_quiz_ids';

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

/**
 * 从 sessionStorage 获取已通知的题目 ID 集合
 * @returns 已通知题目 ID 数组
 */
export function getNotifiedQuizIds(): string[] {
  try {
    const data = sessionStorage.getItem(NOTIFIED_KEY);
    return data ? JSON.parse(data) : [];
  } catch {
    return [];
  }
}

/**
 * 标记题目为已通知，写入 sessionStorage
 * 用于避免同一会话内重复通知
 * @param quizIds - 需标记的题目 ID 数组
 */
export function markNotified(quizIds: string[]): void {
  try {
    const existing = getNotifiedQuizIds();
    const merged = Array.from(new Set([...existing, ...quizIds]));
    sessionStorage.setItem(NOTIFIED_KEY, JSON.stringify(merged));
  } catch (err) {
    console.warn('写入 sessionStorage 失败:', err);
  }
}
