/**
 * 设备偏好：持久化、CSS 应用、跨组件共享
 *
 * 这些值不经过后端，但它们是设置页真正"生效"的部分（字号/动效/侧栏），
 * 所以至少要把"写进去 → 根节点真的变了 → 重新读取还在"钉住。
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { getDevicePrefs, setDevicePref, subscribeDevicePrefs, useDevicePrefs } from './devicePrefs';
import { act, renderHook } from '@testing-library/react';

beforeEach(() => {
  localStorage.removeItem('engramnote:device-prefs:v1');
  setDevicePref('fontScale', 'md');
  setDevicePref('reduceMotion', false);
  setDevicePref('sidebarCollapsed', false);
  setDevicePref('adhdDefault', false);
  setDevicePref('desktopNotifications', true);
  setDevicePref('quietStart', 22);
  setDevicePref('quietEnd', 8);
});

describe('devicePrefs', () => {
  it('修改字号会立刻落到根节点，并写进 localStorage', () => {
    setDevicePref('fontScale', 'lg');
    expect(document.documentElement.style.fontSize).toBe('18px');
    expect(getDevicePrefs().fontScale).toBe('lg');
    expect(localStorage.getItem('engramnote:device-prefs:v1')).toContain('"fontScale":"lg"');
  });

  it('减少动效切换成 html[data-reduce-motion]（CSS 规则只认这个属性）', () => {
    setDevicePref('reduceMotion', true);
    expect(document.documentElement.dataset.reduceMotion).toBe('true');
    setDevicePref('reduceMotion', false);
    expect(document.documentElement.dataset.reduceMotion).toBe('false');
  });

  it('订阅者收到变更；取消订阅后不再收到', () => {
    let calls = 0;
    const unsubscribe = subscribeDevicePrefs(() => {
      calls += 1;
    });
    setDevicePref('quietStart', 23);
    expect(calls).toBe(1);
    unsubscribe();
    setDevicePref('quietStart', 21);
    expect(calls).toBe(1);
  });

  it('useDevicePrefs 与模块级 setDevicePref 共享同一份状态', () => {
    const { result } = renderHook(() => useDevicePrefs());
    act(() => setDevicePref('sidebarCollapsed', true));
    expect(result.current.prefs.sidebarCollapsed).toBe(true);
  });
});
