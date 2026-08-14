/**
 * @file 时间显示工具
 * @description 后端统一以 UTC 存储时间（序列化带 +00:00 偏移），
 * 这里统一转换为浏览器本地时区显示，并显式标注时区（如 GMT+8）。
 */

/** 完整日期时间：2026/8/13 13:00:00 GMT+8 */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { timeZoneName: 'short' })
}

/** 日期：2026/8/13 */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN')
}

/** 时间：13:00 */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
