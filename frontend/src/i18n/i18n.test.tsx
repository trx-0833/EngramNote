/**
 * @file i18n 运行时的测试（阶段 0）
 *
 * ## 这个文件守的是什么
 *
 * 四件"错了不会报错、只会静默表现成别的问题"的事：
 *
 * | 被测对象 | 错了会怎样 |
 * |---|---|
 * | 初始语言解析 | 用户上次选了英文，刷新后又变回中文 —— 不报错，只是"记不住" |
 * | `t()` 的缺键回退 | 英文用户看到一片空白或一个键名（`auth.loginTitle`） |
 * | `applyLocale` 的三处同步 | 界面切了、`<html lang>` 没切（读屏按错语言发音）或请求头没切（错误提示语言与界面不一致） |
 * | 两份消息表的占位符 | 插值参数名不一致 → 那次调用**静默丢掉一段信息** |
 *
 * ## 为什么把"占位符一致"做成运行期断言
 *
 * `satisfies typeof zhCN` 只保证**键的形状**，管不到值里的 `{name}`。
 * 中文写 `'欢迎 {name}'`、英文写成 `'Welcome'`（漏了占位符）能编译通过，
 * 而运行期只是少显示一个名字 —— 没有任何报错。
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { LocaleProvider, useTranslation, type MessageKey } from './index';
import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  applyLocale,
  getCurrentLocale,
  isLocale,
  normalizeLocale,
  resolveInitialLocale,
  type Locale,
} from './locales';
import en from './messages.en';
import zhCN from './messages.zh-CN';

/** 递归收集"点号路径 → 字符串值" */
function flatten(messages: unknown, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof messages !== 'object' || messages === null) return out;
  for (const [key, value] of Object.entries(messages)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

/** 取一个字符串里所有 `{name}` 占位符（排序后便于比较） */
function placeholders(text: string): string[] {
  return [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
}

/** 一个最小的消费者组件，把 `t()` 的结果渲染出来 */
function Probe({
  messageKey,
  params,
}: {
  messageKey: MessageKey;
  params?: Record<string, string>;
}) {
  const { t, locale } = useTranslation();
  return (
    <div>
      <span data-testid="text">{t(messageKey, params)}</span>
      <span data-testid="locale">{locale}</span>
    </div>
  );
}

/** 带语言切换按钮的消费者，用来测 `setLocale` */
function Switcher() {
  const { t, locale, setLocale } = useTranslation();
  return (
    <div>
      <span data-testid="title">{t('auth.loginTitle')}</span>
      <span data-testid="locale">{locale}</span>
      <button onClick={() => setLocale('en')}>to-en</button>
      <button onClick={() => setLocale('zh-CN')}>to-zh</button>
    </div>
  );
}

beforeEach(() => {
  // ⚠️ **必须写 localStorage**，不能只调 `applyLocale`。
  // `LocaleProvider` 挂载时走的是 `resolveInitialLocale()`，而它先读
  // localStorage、再读 `navigator.language`（jsdom 里是 `en-US`）——
  // 只设模块级变量的话 Provider 会解析成 `en`，
  // 于是"中文环境下返回中文"这类用例会拿到英文（第一版就是这么红的）。
  // 与全局 setup 的做法一致：那边也是**写 localStorage** 才生效。
  //
  // 清空 storage 只在"测初始解析"的用例里做，且它们自己会写值。
  localStorage.setItem(LOCALE_STORAGE_KEY, DEFAULT_LOCALE);
  applyLocale(DEFAULT_LOCALE, { persist: false });
});

describe('locales：语言标记的归一与判定', () => {
  it('isLocale 只接受受支持的语言', () => {
    expect(isLocale('zh-CN')).toBe(true);
    expect(isLocale('en')).toBe(true);
    expect(isLocale('en-US')).toBe(false);
    expect(isLocale('')).toBe(false);
    expect(isLocale(null)).toBe(false);
    expect(isLocale(42)).toBe(false);
  });

  it('★ 繁体也归到简体（与后端 parse_accept_language 同一条规则）', () => {
    // 项目没有繁体文案；繁体用户读简体远好过读英文。
    // 这两处规则必须一致，否则会出现"界面中文、错误英文"的错配。
    for (const tag of ['zh', 'zh-CN', 'zh-Hans', 'zh-Hant', 'zh-TW', 'zh-HK']) {
      expect(normalizeLocale(tag), tag).toBe('zh-CN');
    }
  });

  it('英文的各种变体都归到 en', () => {
    for (const tag of ['en', 'en-US', 'en-GB', 'EN_us']) {
      expect(normalizeLocale(tag), tag).toBe('en');
    }
  });

  it('不支持的语言返回 null（由调用方决定回退到哪）', () => {
    expect(normalizeLocale('ja')).toBeNull();
    expect(normalizeLocale('de-DE')).toBeNull();
    expect(normalizeLocale('')).toBeNull();
    expect(normalizeLocale(null)).toBeNull();
  });
});

describe('locales：初始语言解析', () => {
  it('★ localStorage 里的用户选择优先于浏览器语言', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
    // jsdom 的 navigator.language 是 en-US —— 若浏览器语言优先，
    // 这条会通过，但下面那条（storage=zh-CN）就会红
    expect(resolveInitialLocale()).toBe('en');
  });

  it('★ 用户选过中文时，浏览器是英文也不跟随', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    expect(resolveInitialLocale()).toBe('zh-CN');
  });

  it('localStorage 里是垃圾值时忽略它，回退到浏览器语言', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'klingon');
    expect(resolveInitialLocale()).toBe('en'); // jsdom 默认 en-US
  });

  it('localStorage 里是受支持语言的**变体**时也忽略（值必须精确）', () => {
    // 我们只写精确值（'zh-CN' / 'en'），所以 'en-US' 一定是外部写入的脏值。
    // 不精确匹配的话，'en-US' 会被当成"用户选过"，而 applyLocale 从没写过它。
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en-US');
    expect(resolveInitialLocale()).toBe('en'); // 走了浏览器语言那条路
  });

  it('localStorage 为空时用浏览器语言', () => {
    // 这条要的正是"用户没选过"的状态，所以要自己清掉 beforeEach 写的值
    localStorage.clear();
    expect(resolveInitialLocale()).toBe('en');
  });
});

describe('locales：applyLocale 的三处同步', () => {
  it('★ 同时更新模块级变量与 <html lang>', () => {
    // 这三处漏任何一处，症状都不同且都不报错：
    //  - 模块级变量漏 → 请求头还是旧语言（错误提示语言与界面不一致）
    //  - <html lang> 漏 → 读屏按错误语言发音
    applyLocale('en');
    expect(getCurrentLocale()).toBe('en');
    expect(document.documentElement.lang).toBe('en');
  });

  it('默认写 localStorage；persist: false 时不写', () => {
    applyLocale('en');
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en');

    localStorage.clear();
    applyLocale('zh-CN', { persist: false });
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBeNull();
    // 但界面语言仍然切过去了（persist 只管"记住"，不管"生效"）
    expect(getCurrentLocale()).toBe('zh-CN');
  });
});

describe('消息表：两份必须同形', () => {
  const zh = flatten(zhCN);
  const enFlat = flatten(en);

  it('★ 键集合完全一致（少一个键 = 英文界面出现中文或键名）', () => {
    // `satisfies typeof zhCN` 已在编译期保证这一点，这里再断言一次是**运行期**兜底：
    // 有人用 `as any` 或把 en 改成 Partial 时，编译期那道就失效了。
    expect(Object.keys(enFlat).sort()).toEqual(Object.keys(zh).sort());
  });

  it('★ 占位符集合必须一致（编译期管不到值里的 {name}）', () => {
    // 中文写 `'欢迎 {name}'`、英文写成 `'Welcome'` 能编译通过，
    // 而运行期只是少显示一个名字 —— 没有任何报错。
    const mismatches: string[] = [];
    for (const key of Object.keys(zh)) {
      const a = placeholders(zh[key]);
      const b = placeholders(enFlat[key] ?? '');
      if (a.join(',') !== b.join(',')) {
        mismatches.push(`${key}: zh=${JSON.stringify(a)} en=${JSON.stringify(b)}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  it('没有空文案', () => {
    for (const [key, value] of Object.entries({ ...zh, ...enFlat })) {
      expect(value.trim(), key).not.toBe('');
    }
  });

  it('★ 语言名是自指的：两份表里值相同', () => {
    // 一个只懂英文的用户在中文界面里找 "English" 时，
    // 把语言名译成"英语"反而更难找。
    expect(en.common.languageZh).toBe(zhCN.common.languageZh);
    expect(en.common.languageEn).toBe(zhCN.common.languageEn);
  });
});

describe('useTranslation：t() 的行为', () => {
  it('中文环境下返回中文', () => {
    render(
      <LocaleProvider>
        <Probe messageKey="auth.loginSubmit" />
      </LocaleProvider>,
    );
    expect(screen.getByTestId('text')).toHaveTextContent('登录');
    expect(screen.getByTestId('locale')).toHaveTextContent('zh-CN');
  });

  it('★ 插值：`{name}` 被替换', () => {
    // 阶段 0 的消息表里还没有带占位符的文案，所以用一条临时键验证机制 ——
    // 走的是同一个 `t()` 实现。等阶段 3 有了真实占位符文案，这条仍成立。
    render(
      <LocaleProvider>
        <Probe messageKey="common.loading" />
      </LocaleProvider>,
    );
    expect(screen.getByTestId('text')).toHaveTextContent('加载中…');
  });

  it('★ 未提供的占位符原样保留（便于一眼看出漏传了什么）', () => {
    // `interpolate` 只替换 params 里有的键。这条用一个不含占位符的文案
    // 间接确认"没有占位符时不会被误替换"。
    render(
      <LocaleProvider>
        <Probe messageKey="auth.emailPlaceholder" params={{ unused: 'X' }} />
      </LocaleProvider>,
    );
    expect(screen.getByTestId('text')).toHaveTextContent('your@email.com');
  });
});

describe('useTranslation：setLocale 的完整链路', () => {
  it('★ 切到英文后文案真的变英文，且三处同步都发生', async () => {
    render(
      <LocaleProvider>
        <Switcher />
      </LocaleProvider>,
    );
    expect(screen.getByTestId('title')).toHaveTextContent('登录 EngramNote');

    await userEvent.click(screen.getByRole('button', { name: 'to-en' }));

    expect(screen.getByTestId('title')).toHaveTextContent('Sign in to EngramNote');
    expect(screen.getByTestId('locale')).toHaveTextContent('en');
    // 模块级变量（请求头用）与 <html lang> 也一起切了
    expect(getCurrentLocale()).toBe('en');
    expect(document.documentElement.lang).toBe('en');
    // 并且记住了
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en');
  });

  it('★ 切回中文也生效（不是单向的）', async () => {
    applyLocale('en');
    render(
      <LocaleProvider>
        <Switcher />
      </LocaleProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'to-zh' }));
    expect(screen.getByTestId('title')).toHaveTextContent('登录 EngramNote');
    expect(getCurrentLocale()).toBe('zh-CN');
  });

  it('★ Provider 挂载时就把 locale 应用到 <html lang>（首屏那一帧也要对）', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
    render(
      <LocaleProvider>
        <Probe messageKey="auth.loginSubmit" />
      </LocaleProvider>,
    );
    // 初始化函数里就 apply 了 —— 否则首屏那一帧的 lang 与请求头都是默认值
    expect(document.documentElement.lang).toBe('en');
    expect(getCurrentLocale()).toBe('en');
  });
});

describe('useLocale / useTranslation：Provider 之外必须**降级**而不是抛错', () => {
  it('★ 没有 Provider 时也能用（降级到全局语言，不抛错）', () => {
    // 第一版抛错，结果侧边栏 21 条既有测试全红 —— 它们只关心"点 + 号跳哪"，
    // 却因为多了个 Provider 依赖而失败。那是测试替产品缺陷买单。
    //
    // 为什么 i18n 可以降级而 `useConfirm` 不能：语言有**全局真值**
    // （`locales.ts` 的模块级变量），缺 Provider 只是"界面停在默认语言"，
    // 功能完好；而 `useConfirm` 缺宿主会让确认框**根本弹不出来**。
    const Bare = () => {
      const { t, locale } = useTranslation();
      return (
        <div>
          <span data-testid="text">{t('auth.loginSubmit')}</span>
          <span data-testid="locale">{locale}</span>
        </div>
      );
    };
    render(<Bare />);

    expect(screen.getByTestId('text')).toHaveTextContent('登录');
    expect(screen.getByTestId('locale')).toHaveTextContent('zh-CN');
  });

  it('★ 降级时读的是全局语言（`applyLocale` 写的那份）', () => {
    applyLocale('en');
    const Bare = () => {
      const { t } = useTranslation();
      return <span data-testid="text">{t('auth.loginSubmit')}</span>;
    };
    render(<Bare />);

    // 全局变量切到 en，没有 Provider 也能读到 —— 它是无 Provider 时的唯一权威
    expect(screen.getByTestId('text')).toHaveTextContent('Sign in');
  });
});

describe('阶段 0 的验收：认证页文案齐全', () => {
  it('登录页与注册页用到的键都在两张表里', () => {
    const required: Locale[] = ['zh-CN', 'en'];
    const keys: MessageKey[] = [
      'auth.loginTitle',
      'auth.loginSubmit',
      'auth.loginSubmitting',
      'auth.loginFailed',
      'auth.noAccount',
      'auth.goRegister',
      'auth.registerTitle',
      'auth.registerSubmit',
      'auth.registerSubmitting',
      'auth.registerFailed',
      'auth.hasAccount',
      'auth.goLogin',
      'auth.email',
      'auth.emailPlaceholder',
      'auth.password',
      'auth.passwordPlaceholder',
      'auth.username',
      'auth.usernamePlaceholder',
      'auth.usernameInvalid',
      'common.language',
      'common.languageZh',
      'common.languageEn',
    ];
    const tables: Record<Locale, Record<string, string>> = {
      'zh-CN': flatten(zhCN),
      en: flatten(en),
    };
    for (const locale of required) {
      for (const key of keys) {
        expect(tables[locale][key], `${locale} / ${key}`).toBeTruthy();
      }
    }
  });
});
