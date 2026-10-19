/**
 * @file 时间显示工具
 * @description 后端统一以 UTC 存储时间（序列化带 +00:00 偏移），
 * 这里统一转换为浏览器本地时区显示，并显式标注时区（如 GMT+8）。
 *
 * ## i18n（阶段 0）：本模块是**唯一**的时间格式化出口
 *
 * 三个函数都接受可选的 `locale`，**默认取当前界面语言**
 * （`i18n/locales.ts` 的 `getCurrentLocale()`）—— 于是调用方
 * 一行都不用改，界面切到英文后日期自动变成英文格式。
 *
 * ## 为什么必须收口到这里
 *
 * 改之前有 **4 处页面自己写死 `'zh-CN'`**（`DailyMaterials.tsx:91` / `:890`、
 * `Dashboard.tsx:651`、`NotesList.tsx:363`）—— 那些地方在英文界面下会
 * 继续显示 `2026/8/28` 这种中文格式的日期，与旁边的英文文案混排。
 * 这种"半截本地化"比完全不本地化更显眼，所以一并收进本模块。
 *
 * ⚠️ 用原生 `toLocale*` 而不是 `Intl.DateTimeFormat` 的显式实例：
 * 前者本来就是这三个函数在用的东西，换成后者是**行为改动**
 * （格式化选项的默认值不同），而本轮的判据是"只改语言、不改格式"。
 */

import { getCurrentLocale, type Locale } from '../i18n/locales';

/** 完整日期时间：中文 `2026/8/13 13:00:00 GMT+8`、英文 `8/13/2026, 1:00:00 PM GMT+8` */
export function formatDateTime(iso: string, locale: Locale = getCurrentLocale()): string {
  return new Date(iso).toLocaleString(locale, { timeZoneName: 'short' });
}

/** 日期：中文 `2026/8/13`、英文 `8/13/2026` */
export function formatDate(iso: string, locale: Locale = getCurrentLocale()): string {
  return new Date(iso).toLocaleDateString(locale);
}

/** 时间：中文与英文都是 `13:00` / `1:00 PM`（`hour: '2-digit'` 在英文下会带 AM/PM） */
export function formatTime(iso: string, locale: Locale = getCurrentLocale()): string {
  return new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
}

/**
 * 只到「月 日」的短日期：中文 `8月28日`、英文 `Aug 28`
 *
 * ## 为什么单独加这一个
 *
 * `DailyMaterials.tsx:91` 原来自己写 `toLocaleDateString('zh-CN', { month: 'long',
 * day: 'numeric' })` —— 那是**本模块之外唯一一处自定义格式化选项**。
 * 收口时有两个选择：让那个页面自己把 `locale` 传进 `toLocaleDateString`，
 * 或者在这里给它一个名字。
 *
 * 选后者：`{ month: 'long', day: 'numeric' }` 这组选项是**格式决策**，
 * 而格式决策应当和其余三个函数住在一起 —— 否则"日期怎么显示"又变成
 * 每个页面各自决定的事，正是本轮收口要消灭的东西。
 *
 * @param date 已经构造好的 `Date`（调用方通常还要与"昨天"比较，所以收 `Date`
 *   而不是 ISO 字符串 —— 那是 `formatDate` 的接口）
 */
export function formatMonthDay(date: Date, locale: Locale = getCurrentLocale()): string {
  return date.toLocaleDateString(locale, { month: 'long', day: 'numeric' });
}
