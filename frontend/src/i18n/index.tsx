/**
 * @file i18n 的运行时（Provider + hook + 语言切换）
 *
 * ## 为什么手写而不是引 i18next
 *
 * 项目对依赖有明确口径：「**零新依赖优先**…算法不复杂时（< 100 行），
 * 手写实现比引入依赖更易维护」（`docs/archive/开发时间表.md:475`），
 * `frontend/docs/visual-refactor-plan.md:437` 也把"引入库"列为不做。
 *
 * 本文件 + `locales.ts` + 两张消息表就是全部实现。代价是三件事要自己做：
 * - **复数**：英文界面里需要复数的位置极少（`{count} cards` 直接拼即可），
 *   真需要时再补一个 `plural()` 辅助；
 * - **日期**：用原生 `Intl` / `toLocale*`，见 `utils/datetime.ts`（本来就在用）；
 * - **懒加载**：两份消息表体积可控（阶段 0 只有两个命名空间），
 *   全量进主包比按需加载少一次网络往返。若将来大到影响首屏再改成
 *   动态 `import()` —— 那是可加的优化，不是现在的约束。
 *
 * ## 三处必须同步的地方（漏一处就会有"半截英文"的界面）
 *
 * | 目标 | 谁负责 |
 * |---|---|
 * | React 树里的文案 | 本文件的 state |
 * | `<html lang>` | `locales.ts` 的 `applyLocale()` |
 * | 请求头 `Accept-Language` | 同上（`api/client.ts` 读那个模块级变量） |
 *
 * 状态住在 `locales.ts`（纯数据模块、零 import），不在这里 ——
 * 因为 `api/client.ts` 要读它，而 client **不能** import 本文件
 * （本文件为了 `PUT /auth/locale` 要 import client，会成环）。
 * 详见 `locales.ts` 末尾那段说明。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  LOCALE_STORAGE_KEY,
  applyLocale,
  getCurrentLocale,
  normalizeLocale,
  resolveInitialLocale,
  type Locale,
} from './locales';
// 查表 + 插值 + 键类型都住在 `translate.ts`（零 import 的纯逻辑），
// 这样 `api/client.ts` 能用同一份实现而不形成循环依赖。见那个文件头。
import { lookupMessage, type MessageKey, type TranslateParams } from './translate';

export type { MessageKey, TranslateParams } from './translate';

/** 语言上下文的值 */
interface LocaleContextValue {
  locale: Locale;
  /** 切换语言。写 localStorage / `<html lang>` / 模块级变量，并尽力同步到后端 */
  setLocale: (locale: Locale) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

/**
 * 把语言偏好同步到后端
 *
 * 失败**静默**：未登录（401）/ 网络不通 / 后端还没实现该端点，
 * 都不该让"切换语言"这个动作失败 —— 用户的**界面**已经切过去了，
 * 那是首要的。后端那份偏好会在下次切换或登录时重新同步。
 */
async function syncLocaleToServer(locale: Locale): Promise<void> {
  try {
    // 动态 import 有两个理由：① 避免与 `api/client.ts` 形成**静态**循环依赖；
    // ② 登录页这条路径本来就不该加载它（那时请求必然 401）。
    const { updateLocale } = await import('../api/auth');
    await updateLocale(locale);
  } catch {
    // 见上方说明：刻意吞掉
  }
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = resolveInitialLocale();
    // ⚠️ 在 useState 的**初始化函数**里就 apply：否则首屏那一帧的
    // `<html lang>` 与请求头都还是默认值 —— 读屏会按错误语言发音，
    // 而首个请求会带错语言。初始化函数只在挂载时跑一次，不是每次渲染都写 DOM。
    // `persist: false` —— 初始化不是"用户做了选择"（见 applyLocale 的说明）。
    applyLocale(initial, { persist: false });
    return initial;
  });

  const setLocale = useCallback((next: Locale) => {
    applyLocale(next);
    setLocaleState(next);
    // 刻意**不 await**：见 `syncLocaleToServer` 的说明
    void syncLocaleToServer(next);
  }, []);

  /**
   * 跟随系统语言变化 —— **只在用户没有显式选过时**生效
   *
   * 少了那个判断，"用户在应用里选了英文、但浏览器是中文"的情况下，
   * 浏览器语言一变就会把用户的选择覆盖掉。
   */
  useEffect(() => {
    const onLanguageChange = () => {
      try {
        if (localStorage.getItem(LOCALE_STORAGE_KEY)) return;
      } catch {
        return;
      }
      const next = normalizeLocale(navigator.language);
      if (next && next !== locale) setLocale(next);
    };
    window.addEventListener('languagechange', onLanguageChange);
    return () => window.removeEventListener('languagechange', onLanguageChange);
  }, [locale, setLocale]);

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

/**
 * 取语言上下文
 *
 * ## ⚠️ Provider 缺失时**降级**而不是抛错（与 `useConfirm` 不同，这是刻意的）
 *
 * 第一版抛错，结果侧边栏的 21 条既有测试全红 —— 它们本来只关心"点 + 号跳哪"，
 * 却因为多了个 Provider 依赖而失败。那是**测试替产品缺陷买单**的形态。
 *
 * 为什么这里可以降级、而 `useConfirm` 必须抛错：
 *
 * | | `useConfirm` | i18n |
 * |---|---|---|
 * | 有没有全局真值 | 没有 —— 必须有个宿主组件才能渲染对话框 | **有** —— `locales.ts` 的模块级变量，任何地方都能读 |
 * | 缺了会怎样 | 确认框**根本弹不出来**（静默吞掉用户的操作） | 界面停在默认语言（功能完好，只是语言不对） |
 * | 该不该抛错 | 该 —— 静默失败比崩溃更难查 | 不该 —— 降级是可接受的，崩溃不是 |
 *
 * 而且本模块的 `t()` 在**缺键**时都会优雅降级（回退中文 → 回退键名），
 * 缺 Provider 却直接抛错是自相矛盾的。
 *
 * 降级时用的是 `getCurrentLocale()` —— 那是 `applyLocale()` 维护的**全局真值**，
 * 在没有 Provider 的情况下它就是唯一权威（测试环境的 `src/test/setup.ts`
 * 正是靠写它来钉住语言的）。
 */
export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (ctx) return ctx;
  return {
    locale: getCurrentLocale(),
    // 没有 Provider 时 `setLocale` 只能改全局变量 —— 本次会话内有效，
    // 但不会触发 React 重渲染（没有 state 可改）。真实应用里 Provider
    // 一定存在（`main.tsx` 最外层），所以这条只在测试里被走到。
    setLocale: (next: Locale) => applyLocale(next),
  };
}

/** 点号路径的键类型、`MessageKey`、插值参数都住在 `translate.ts`（见那里的说明） */

/**
 * 翻译（组件版）。
 *
 * 缺键时的三级回退与插值规则都在 `translate.ts` 的 `lookupMessage` 里 ——
 * 这里只负责"取当前语言"并订阅它的变化。把规则写在两处必然漂移，
 * 所以组件版与非组件版（`translate()`）共用同一份实现。
 */
export function useTranslation(): {
  t: (key: MessageKey, params?: TranslateParams) => string;
  locale: Locale;
  /** 切换语言。与 `useLocale().setLocale` 是同一个函数 —— 这里一并给出，
   *  因为"取文案"与"换语言"在使用处几乎总是一起出现（切换器就是
   *  `t()` + `setLocale` 两件事），让调用方 import 一个 hook 就够 */
  setLocale: (locale: Locale) => void;
} {
  const { locale, setLocale } = useLocale();

  const t = useCallback(
    (key: MessageKey, params?: TranslateParams): string => lookupMessage(key, locale, params),
    [locale],
  );

  return { t, locale, setLocale };
}

export { getCurrentLocale, DEFAULT_LOCALE, type Locale } from './locales';
export { translate } from './translate';
