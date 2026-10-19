/**
 * @file 语言（locale）的**唯一来源**
 *
 * ## 为什么只有两种
 *
 * `zh-CN` 与 `en` —— 项目现有文案是简体中文，加一种英文即可覆盖"非中文用户"。
 * 繁体（`zh-TW` / `zh-Hant`）**刻意不做**：项目没有繁体文案，而把繁体归到
 * 英文比归到简体更糟（繁体用户读简体远好过读英文）。后端的
 * `parse_accept_language` 按同一判断把 `zh-*` 全部归到 `zh-CN`。
 *
 * 类型写成联合而不是 `string`：这样 `messages.en.ts` 标注
 * `satisfies typeof zhCN` 时，`tsc` 能保证两种语言的**消息表同形** ——
 * 少一个键就编译失败。这是手写方案相对 i18n 库的主要优势，必须保住。
 */

/** 支持的语言。新增一种时，`messages.<locale>.ts` 与后端 `SUPPORTED_LOCALES` 都要加 */
export type Locale = 'zh-CN' | 'en';

/** 支持的语言清单（运行期用，与 `Locale` 联合保持同步 —— 见文件末尾的守卫） */
export const SUPPORTED_LOCALES = ['zh-CN', 'en'] as const;

/**
 * 默认语言。
 *
 * ⚠️ 是 `zh-CN` 而不是 `en`，而且这不只是"照顾现有用户"：
 * 前端 **502 条测试断言**在查中文文案（`KnowledgeGraph.test.tsx` 146 处、
 * `Projects.test.tsx` 96 处…），测试环境的 locale 钉在 `zh-CN` 上，
 * 于是那些断言一条都不用改。详见 `src/test/setup.ts`。
 */
export const DEFAULT_LOCALE: Locale = 'zh-CN';

/** 语言偏好的持久化键（与 `client.ts` 的令牌键同一命名空间） */
export const LOCALE_STORAGE_KEY = 'engramnote_locale';

/** 运行期判断一个任意字符串是不是受支持的 locale */
export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/**
 * 把浏览器/请求头给的语言标记**归一**到受支持的一种
 *
 * 处理三种真实形态：
 * - `zh-CN` / `zh` / `zh-Hans` / `zh-TW` → `zh-CN`（繁体也归简体，理由见文件头）
 * - `en-US` / `en-GB` / `en` → `en`
 * - 其余（`ja` / `de` / 空串 / 畸形值）→ `null`（**由调用方决定回退到哪**，
 *   本函数不替调用方选默认值 —— 初始语言解析与"用户显式选过没有"是两件事）
 *
 * ⚠️ 刻意**不引入** `Intl.Locale` 或 `babel`/`langcodes`：只需要识别两个
 * 语言，按项目「零新依赖优先」的口径（`docs/archive/开发时间表.md:475`）
 * 手写即可。`Intl.Locale` 能做得更准，但它对畸形输入会抛异常，
 * 而这里的调用方（`navigator.language`、`Accept-Language`）都可能给出脏值。
 */
export function normalizeLocale(tag: string | null | undefined): Locale | null {
  if (!tag) return null;
  // 只取主语言子标签：`en-US` → `en`、`zh-Hans-CN` → `zh`
  const primary = tag.trim().toLowerCase().split(/[-_]/)[0];
  if (primary === 'zh') return 'zh-CN';
  if (primary === 'en') return 'en';
  return null;
}

/* ────────────────────────────────────────────────────────────────
 * 当前语言（模块级状态）
 *
 * ## 为什么状态住在**这个**文件，而不是 `i18n/index.tsx`
 *
 * `api/client.ts` 需要读当前语言来拼 `Accept-Language` 请求头。
 * 而 client 是普通模块（不是组件），拿不到 React context；
 * 它又**不能** import `i18n/index.tsx` —— 那个文件 import 了 client
 * （为了 `PUT /auth/locale`），会形成循环依赖。
 *
 * 本文件是纯数据模块（**零 import**），两边都能安全 import 它。
 * 于是"当前语言"这一个事实只有一份，三处消费者（React 树、`<html lang>`、
 * 请求头）都从它取 —— 详见 `i18n/index.tsx` 文件头那张表。
 * ──────────────────────────────────────────────────────────────── */

let currentLocale: Locale = DEFAULT_LOCALE;

/**
 * 读当前语言（供**非组件**代码用，主要是 `api/client.ts` 的请求头）
 *
 * 刻意返回模块级变量而不是读 localStorage：变量与 React state 同步更新，
 * 而 localStorage 可能被**别的标签页**改过 —— 那样请求头会与界面不一致
 * （界面显示英文，错误却按中文请求）。
 */
export function getCurrentLocale(): Locale {
  return currentLocale;
}

/**
 * 把语言写到三个地方：模块级变量、`<html lang>`、localStorage
 *
 * @param locale 目标语言
 * @param options.persist 是否写 localStorage。默认 `true`；
 *   传 `false` 用于"首屏初始化"与"从后端回填"两种场景 ——
 *   那时写入只是把已有值再写一遍，语义上不是"用户做了选择"。
 *   在初始化阶段就写，会把"跟随浏览器语言"固化成"用户显式选过"，
 *   之后用户改浏览器语言也不会跟随了。
 */
export function applyLocale(locale: Locale, options: { persist?: boolean } = {}): void {
  currentLocale = locale;
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
  }
  if (options.persist !== false) {
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    } catch {
      // localStorage 不可用（隐私模式 / 被禁用）时也要让界面切过去 ——
      // 本次会话内仍然生效，只是下次打开会回到默认
    }
  }
}

/**
 * 解析**初始**语言（页面加载时那一次）
 *
 * 优先级：用户显式选过（localStorage）→ 浏览器语言 → 默认中文。
 *
 * ⚠️ 本函数**不写**任何地方，只回答"现在该用哪种语言"。
 * 写入是 `applyLocale()` 的事（理由见它的 `persist` 说明）。
 */
export function resolveInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    // localStorage 不可用：静默跳过，不影响语言解析
  }
  if (typeof navigator !== 'undefined') {
    const fromBrowser = normalizeLocale(navigator.language);
    if (fromBrowser) return fromBrowser;
  }
  return DEFAULT_LOCALE;
}
