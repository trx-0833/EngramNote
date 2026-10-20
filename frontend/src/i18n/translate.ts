/**
 * @file 非组件版的翻译函数
 *
 * ## 为什么单独一个文件（而不是放进 `i18n/index.tsx`）
 *
 * `api/client.ts` 是**普通模块**，不是组件 —— 它没有 hook 上下文，
 * 却需要翻译它自己造的错误文案（超时、令牌失效、浏览器不支持流式响应）。
 * 那些请求根本没发出去或没拿到响应，**后端无从翻译**。
 *
 * 而 `client.ts` **不能** import `i18n/index.tsx`：后者为了
 * `PUT /auth/locale` 要 import `client.ts`，会形成循环依赖。
 * 所以把"查表 + 插值"这段纯逻辑抽到这里：
 *
 * ```
 * i18n/locales.ts   ← 零 import（语言状态）
 *      ↑        ↑
 * i18n/translate.ts │  ← 零 import（查表 + 插值，本文件）
 *      ↑        │
 * i18n/index.tsx    │  ← Provider + hook
 *      ↑        │
 * api/client.ts ────┘  ← 读 getCurrentLocale + translate
 * ```
 *
 * `index.tsx` 的 `useTranslation()` 也复用这里的 `lookupMessage`，
 * 所以"缺键回退"只有一份实现 —— 两处各写一份必然漂移。
 */

import { DEFAULT_LOCALE, getCurrentLocale, type Locale } from './locales';
import en from './messages.en';
import zhCN from './messages.zh-CN';

/** 消息表集合。键是 `Locale`，所以新增语言时 `tsc` 会要求这里也加 */
export const MESSAGES: Record<Locale, typeof zhCN> = {
  'zh-CN': zhCN,
  en,
};

/** 点号路径的键类型（从消息表递归推导） */
export type DotPaths<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${Prefix}${K}` : DotPaths<T[K], `${Prefix}${K}.`>;
}[keyof T & string];

/** 所有合法的翻译键。`t('auth.logIn')` 这种拼错会被 `tsc` 挡下 */
export type MessageKey = DotPaths<typeof zhCN>;

/** 插值参数。值允许 number —— `{count}` 这类最常见 */
export type TranslateParams = Record<string, string | number>;

/** 按点号路径取值；路径不存在返回 `undefined`（不抛错） */
function lookup(messages: unknown, key: string): string | undefined {
  let node: unknown = messages;
  for (const part of key.split('.')) {
    if (typeof node !== 'object' || node === null) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === 'string' ? node : undefined;
}

/** 把 `{name}` 换成实参；未提供的占位符**原样保留**（便于一眼看出漏传了什么） */
function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}

/**
 * 查一条文案（缺键时三级回退）
 *
 * ## 三级回退（刻意的，不是偷懒）
 *
 * 1. 当前语言有 → 用它；
 * 2. 当前语言没有、中文有 → 用中文（英文用户看到一句中文，
 *    好过看到一片空白或一个键名）；
 * 3. 两者都没有 → **返回键名本身**。
 *
 * 第 3 条为什么不返回空串：空串在界面上是"这里什么都没有"，
 * 而键名（`auth.loginTitle`）会立刻暴露"这里漏了一条翻译"。
 * 两者都难看，但后者能被发现，前者只能靠猜。
 *
 * ⚠️ 有了 `satisfies typeof zhCN`，第 2、3 条在**编译期**几乎不可能发生
 * （漏键直接编译失败）。留着它们是为了挡住运行期才出现的路径
 * （例如将来有人用动态字符串调 `translate()`）。
 */
export function lookupMessage(key: MessageKey, locale: Locale, params?: TranslateParams): string {
  const template = lookup(MESSAGES[locale], key) ?? lookup(MESSAGES[DEFAULT_LOCALE], key);
  return interpolate(template ?? key, params);
}

/**
 * 按**当前语言**翻译（给非组件代码用）
 *
 * ⚠️ 它不是响应式的：语言变了**不会**触发调用方重渲染。
 * 组件里请用 `useTranslation()`（它订阅 context）；本函数只给
 * `api/client.ts` 这类"翻译完就抛出去"的地方用。
 */
export function translate(key: MessageKey, params?: TranslateParams): string {
  return lookupMessage(key, getCurrentLocale(), params);
}
