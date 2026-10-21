/**
 * @file `retrievalNotice` 的判据锁
 *
 * 这个文件守两件事：
 *
 * 1. **`hybrid` 必须有提示** —— 这是本轮修的那个缺口：实际观测到的是 `hybrid`
 *    （全链路 e2e 里上传 48 秒后提问），而提示只挂在 `bm25_only` 上。
 *    没有这条用例，将来有人"顺手精简"掉 `hybrid` 分支不会有人发现。
 * 2. **两条提示都不暴露实现词**（2026-10-21 改）—— 原口径是"`bm25_only` 的文案
 *    逐字不变，用户已经见过这句话"，但走查报告 §2.2 实测到界面上写着
 *    「已降级为关键词检索（**向量服务**不可用）」：`向量服务` 是内部实现词。
 *    本仓库的判据是"代码实测 > 文档"（AGENTS.md §1），所以改的是**结论**：
 *    现在钉住的是"说清检索方式 + 说清后果 + 不出现实现词"。
 */
import { describe, expect, it } from 'vitest';

import { retrievalNotice } from './retrievalNotice';

describe('retrievalNotice', () => {
  it('★ hybrid 有提示（本轮修的缺口：实际发生的就是它）', () => {
    const notice = retrievalNotice('hybrid');
    expect(notice).toBeTruthy();
    expect(notice).toContain('关键词检索');
  });

  it('★ bm25_only 说清"本次是关键词检索 + 后果"，且不含实现词', () => {
    const notice = retrievalNotice('bm25_only') ?? '';
    expect(notice).toContain('关键词检索');
    // 说清后果（用户要据此决定要不要换个问法），而不是只宣布"降级了"
    expect(notice).toContain('可能不够准');
    // 走查报告 §2.2：这三个是实现词，不该出现在界面上
    expect(notice).not.toContain('向量服务');
    expect(notice).not.toContain('embedding');
    expect(notice).not.toContain('bm25');
  });

  it('full_vector 不提示（正常路径不该出现警告）', () => {
    expect(retrievalNotice('full_vector')).toBeNull();
  });

  it('缺省与未知取值都不提示（不编造警告）', () => {
    expect(retrievalNotice(undefined)).toBeNull();
    expect(retrievalNotice('')).toBeNull();
    expect(retrievalNotice('something_new_from_backend')).toBeNull();
  });

  it('hybrid 的文案不断言单一成因（语料没向量 / 没过阈值 都会得到 hybrid）', () => {
    const notice = retrievalNotice('hybrid') ?? '';
    // 说"可能"而不是"就是"：界面上不写自己没验证过的因果
    expect(notice).toContain('可能');
  });
});
