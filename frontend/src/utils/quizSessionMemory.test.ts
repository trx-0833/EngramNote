/**
 * 刷题进度记忆的测试（想法 8 阶段 3）
 *
 * ## 这个模块最该被钉住的是什么
 *
 * 不是"存了能读回来" —— 那是显然的。真正危险的是**读回了不该读的东西**：
 *
 * 1. **题目集合变了却恢复了** —— 昨天做的第 7 题套到今天的第 7 题上，
 *    用户会看到自己的答案出现在一道没做过的题上。这比"从头开始"严重得多，
 *    而且是**静默**的（页面看起来完全正常）。指纹就是为它存在的。
 * 2. **过期了还恢复** —— 隔天打开一轮"昨天的刷题"，页面上是一堆不再到期的题，
 *    且"今日已完成"与页面进度对不上。
 * 3. **localStorage 抛异常时崩掉** —— Safari 隐私模式 / 配额写满。
 *    那应当是"这次不记忆"，而不是白屏。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  QUIZ_SESSION_KEY_PREFIX,
  clearQuizSession,
  loadQuizSession,
  saveQuizSession,
} from './quizSessionMemory';
import type { QuizSessionRecordItem } from './quizSessionMemory';

/** 造一条记录要保存的内容 */
function record(items: QuizSessionRecordItem[], over: Record<string, unknown> = {}) {
  return {
    currentIndex: 0,
    sessionTotal: 0,
    sessionCorrect: 0,
    items,
    ...over,
  };
}

function item(id: string, over: Partial<QuizSessionRecordItem> = {}): QuizSessionRecordItem {
  return { id, userAnswer: '', submitted: false, result: null, ...over };
}

/** 清掉本模块写下的全部键，避免用例之间互相影响 */
function wipe() {
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k?.startsWith(QUIZ_SESSION_KEY_PREFIX)) keys.push(k);
  }
  keys.forEach((k) => localStorage.removeItem(k));
}

beforeEach(wipe);
afterEach(() => {
  wipe();
  vi.restoreAllMocks();
});

describe('刷题进度记忆', () => {
  it('存了能原样读回来', () => {
    const ids = ['q1', 'q2', 'q3'];
    saveQuizSession(
      'review',
      ids,
      // ⚠️ items 的长度必须与 ids 一致：恢复时要求逐题对齐，
      // 只存一道而 ids 有三道的话会被判为形状不符（见 load 的实现）
      record([item('q1', { userAnswer: '甲', submitted: true }), item('q2'), item('q3')], {
        currentIndex: 2,
        sessionTotal: 1,
        sessionCorrect: 1,
      }),
    );

    const got = loadQuizSession('review', ids);
    expect(got).not.toBeNull();
    expect(got?.currentIndex).toBe(2);
    expect(got?.sessionTotal).toBe(1);
    expect(got?.items[0].userAnswer).toBe('甲');
    expect(got?.items[0].submitted).toBe(true);
  });

  it('★ 题目集合变了就**读不到**（否则昨天的答案会套到今天的题上）', () => {
    const before = ['q1', 'q2', 'q3'];
    saveQuizSession(
      'review',
      before,
      record([item('q1', { userAnswer: '昨天的答案' }), item('q2'), item('q3')]),
    );
    expect(loadQuizSession('review', before)).not.toBeNull();

    // 今天到期的是另一批
    expect(loadQuizSession('review', ['q1', 'q2', 'q9'])).toBeNull();
    // 成员相同但顺序不同 —— 也算不同集合
    expect(loadQuizSession('review', ['q3', 'q2', 'q1'])).toBeNull();
    // 少一道
    expect(loadQuizSession('review', ['q1', 'q2'])).toBeNull();
  });

  it('★ 过期就不恢复，并顺手删掉记录', () => {
    const ids = ['q1'];
    saveQuizSession('review', ids, record([item('q1')]));

    // 模拟 13 小时后（TTL 是 12 小时）
    const later = Date.now() + 13 * 60 * 60 * 1000;
    expect(loadQuizSession('review', ids, later)).toBeNull();

    // 顺手删了：用"当前时间"再读也是 null（而不是又读到那条过期记录）
    expect(loadQuizSession('review', ids)).toBeNull();
  });

  it('不同的 scope 互不干扰（不同笔记不能互相覆盖）', () => {
    saveQuizSession('quick:note-a', ['q1'], record([item('q1', { userAnswer: 'A 的' })]));
    saveQuizSession('quick:note-b', ['q1'], record([item('q1', { userAnswer: 'B 的' })]));

    expect(loadQuizSession('quick:note-a', ['q1'])?.items[0].userAnswer).toBe('A 的');
    expect(loadQuizSession('quick:note-b', ['q1'])?.items[0].userAnswer).toBe('B 的');
  });

  it('★ localStorage 抛异常时静默降级，不冒泡（隐私模式 / 配额写满）', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    expect(() => saveQuizSession('review', ['q1'], record([item('q1')]))).not.toThrow();
    spy.mockRestore();

    const spy2 = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => loadQuizSession('review', ['q1'])).not.toThrow();
    expect(loadQuizSession('review', ['q1'])).toBeNull();
    spy2.mockRestore();
  });

  it('★ 下标越界当作没有记忆（题目变少时不能停在空白页）', () => {
    const ids = ['q1', 'q2'];
    saveQuizSession('review', ids, record([item('q1'), item('q2')], { currentIndex: 5 }));
    expect(loadQuizSession('review', ids)).toBeNull();

    saveQuizSession('review', ids, record([item('q1'), item('q2')], { currentIndex: -1 }));
    expect(loadQuizSession('review', ids)).toBeNull();
  });

  it('坏数据（不是合法记录）当作没有记忆', () => {
    const ids = ['q1'];
    saveQuizSession('review', ids, record([item('q1')]));
    // 找到**刚才那条**记录的键（只会有它一个：beforeEach 清过场）
    const keys = Object.keys(localStorage).filter((k) =>
      k.startsWith(`${QUIZ_SESSION_KEY_PREFIX}review:`),
    );
    expect(keys).toHaveLength(1);

    // 形状不对：缺 items / 缺 at
    localStorage.setItem(keys[0], JSON.stringify({ currentIndex: 0 }));
    expect(loadQuizSession('review', ids)).toBeNull();

    // 根本不是 JSON
    localStorage.setItem(keys[0], 'not json');
    expect(loadQuizSession('review', ids)).toBeNull();
  });

  it('clear 能删掉记录', () => {
    const ids = ['q1', 'q2'];
    saveQuizSession('review', ids, record([item('q1'), item('q2')]));
    expect(loadQuizSession('review', ids)).not.toBeNull();
    clearQuizSession('review', ids);
    expect(loadQuizSession('review', ids)).toBeNull();
  });

  it('空题目列表不写也不读（避免写出一条无意义的记录）', () => {
    saveQuizSession('review', [], record([]));
    expect(Object.keys(localStorage).filter((k) => k.startsWith(QUIZ_SESSION_KEY_PREFIX))).toEqual(
      [],
    );
    expect(loadQuizSession('review', [])).toBeNull();
  });

  it('超过上限时淘汰最旧的，且只淘汰本 scope 的', () => {
    const ids = ['q1'];
    // 造 25 条不同指纹的记录（靠不同的 id 列表）
    for (let i = 0; i < 25; i++) {
      saveQuizSession('review', [`q${i}`], record([item(`q${i}`)]));
    }
    const mine = Object.keys(localStorage).filter((k) =>
      k.startsWith(`${QUIZ_SESSION_KEY_PREFIX}review:`),
    );
    expect(mine.length).toBeLessThanOrEqual(20);

    // 别的 scope 不受影响
    saveQuizSession('today', ids, record([item('q1')]));
    for (let i = 0; i < 25; i++) {
      saveQuizSession('review', [`r${i}`], record([item(`r${i}`)]));
    }
    expect(loadQuizSession('today', ids)).not.toBeNull();
  });
});
