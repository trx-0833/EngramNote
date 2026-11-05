/**
 * 设备本地偏好（不落库）
 *
 * ## 为什么这些不进后端
 *
 * 字号、减少动效、侧栏折叠、免打扰、桌面通知开关只对**这台机器**有意义：
 * 同一账号在台式机和笔记本上想要的字号可能不同；把它们写进 `user_settings`
 * 会让"在 A 机调大字号"变成"B 机也被迫变大"。
 *
 * ## 为什么不用 React Context
 *
 * 这些值需要被三处读：`App`（侧栏折叠）、`Settings`（设置页控件）、
 * `useAdhdReader`（笔记页初始态）。Context 要求所有消费者都在 Provider 子树里，
 * 而 `useAdhdReader` 是普通 hook、`applyDevicePrefs` 还要在模块加载时跑一次。
 * 用 `useSyncExternalStore` + 模块级订阅，任何地方都能读、且跨标签页同步。
 */

import { useSyncExternalStore } from 'react';

export type FontScale = 'sm' | 'md' | 'lg';

export interface DevicePrefs {
  /** 根字号档位：14 / 16 / 18 px */
  fontScale: FontScale;
  /** 手动关闭过渡与动画（与系统 prefers-reduced-motion 叠加，不是替代） */
  reduceMotion: boolean;
  /** 打开笔记时默认进入 ADHD Reader */
  adhdDefault: boolean;
  /** 侧栏是否折叠（App 与设置页共用同一份） */
  sidebarCollapsed: boolean;
  /** 桌面通知总开关（浏览器权限仍在浏览器侧） */
  desktopNotifications: boolean;
  /** 免打扰起止小时（设备本地时间） */
  quietStart: number;
  quietEnd: number;
}

const STORAGE_KEY = 'engramnote:device-prefs:v1';

const DEFAULTS: DevicePrefs = {
  fontScale: 'md',
  reduceMotion: false,
  adhdDefault: false,
  sidebarCollapsed: false,
  desktopNotifications: true,
  quietStart: 22,
  quietEnd: 8,
};

const FONT_PX: Record<FontScale, string> = { sm: '14px', md: '16px', lg: '18px' };

function read(): DevicePrefs {
  if (typeof window === 'undefined') return DEFAULTS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<DevicePrefs>;
    return {
      fontScale:
        parsed.fontScale === 'sm' || parsed.fontScale === 'lg' ? parsed.fontScale : 'md',
      reduceMotion: Boolean(parsed.reduceMotion),
      adhdDefault: Boolean(parsed.adhdDefault),
      sidebarCollapsed: Boolean(parsed.sidebarCollapsed),
      desktopNotifications:
        parsed.desktopNotifications === undefined ? true : Boolean(parsed.desktopNotifications),
      quietStart:
        typeof parsed.quietStart === 'number' && parsed.quietStart >= 0 && parsed.quietStart <= 23
          ? parsed.quietStart
          : DEFAULTS.quietStart,
      quietEnd:
        typeof parsed.quietEnd === 'number' && parsed.quietEnd >= 0 && parsed.quietEnd <= 23
          ? parsed.quietEnd
          : DEFAULTS.quietEnd,
    };
  } catch {
    // 坏 JSON / 隐私模式禁用 localStorage：回到默认值而不是让整个 App 崩
    return { ...DEFAULTS };
  }
}

let cache: DevicePrefs = read();
const listeners = new Set<() => void>();

/** 把偏好写到根节点；CSS 只认 `data-reduce-motion` 与根字号 */
export function applyDevicePrefs(prefs: DevicePrefs = cache): void {
  if (typeof document === 'undefined') return;
  document.documentElement.style.fontSize = FONT_PX[prefs.fontScale];
  document.documentElement.dataset.reduceMotion = prefs.reduceMotion ? 'true' : 'false';
}

export function getDevicePrefs(): DevicePrefs {
  return cache;
}

export function setDevicePref<K extends keyof DevicePrefs>(key: K, value: DevicePrefs[K]): void {
  cache = { ...cache, [key]: value };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  } catch {
    // 写不进去（隐私模式/配额）时至少本次会话生效
  }
  applyDevicePrefs(cache);
  listeners.forEach((fn) => fn());
}

export function subscribeDevicePrefs(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 组件里读偏好 + 写入；所有消费者共享同一份 cache */
export function useDevicePrefs(): {
  prefs: DevicePrefs;
  setPref: typeof setDevicePref;
} {
  const prefs = useSyncExternalStore(subscribeDevicePrefs, getDevicePrefs, getDevicePrefs);
  return { prefs, setPref: setDevicePref };
}

// 跨标签页同步：storage 事件只在**别的**标签页写入时触发
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return;
    cache = read();
    applyDevicePrefs(cache);
    listeners.forEach((fn) => fn());
  });
  // 模块加载即生效：首屏不该先闪一下默认字号
  applyDevicePrefs();
}
