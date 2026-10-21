/**
 * 疑似重复卡片面板（A1 P0-3）
 *
 * ## 这一组盯的是什么
 *
 * 面板自身的逻辑很简单（拉一次、渲染清单），但它有三条**错了不会报错**的分支：
 *
 * 1. **`null` 与 `[]` 必须表现不同** —— `null` 是"后端没统计到"，
 *    `[]` 是"真的没有重复"。混起来会把"统计挂了"说成"你的数据很干净"，
 *    而那是**假的好消息**（与 `/ready` 的 `pending_embeddings` 同一条原则）。
 * 2. **只报不处理** —— 面板里不许出现任何合并/删除按钮。合并会动
 *    `review_states`（复习历史变孤儿行），已拍板要另开一轮单独批。
 * 3. **按需加载** —— 没打开面板时**不许**发请求（它要读向量，挂在常规轮询上
 *    会让每次打开图谱页都付这个代价）。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/graph', () => ({
  getGraphStats: vi.fn(),
}));

import { getGraphStats } from '../../api/graph';
import { SuspectedDuplicatesPanel } from './SuspectedDuplicatesPanel';

const mockedStats = vi.mocked(getGraphStats);

function pair(over: Record<string, unknown> = {}) {
  return {
    card_id_1: 'c1',
    card_id_2: 'c2',
    title: '操作术语',
    similarity: 0.9987,
    note_id_1: 'n1',
    note_id_2: 'n2',
    ...over,
  };
}

function statsWith(duplicates: unknown) {
  return {
    total_nodes: 10,
    total_edges: 3,
    confirmed_edges: 3,
    suggested_edges: 0,
    relation_type_distribution: [],
    isolated_nodes: 7,
    suspected_duplicates: duplicates,
    suspected_duplicate_pairs: Array.isArray(duplicates) ? duplicates.length : null,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('加载时机', () => {
  it('★ 没打开时**不发请求**（按需加载，不挂在常规轮询上）', () => {
    render(<SuspectedDuplicatesPanel open={false} />);
    expect(mockedStats).not.toHaveBeenCalled();
  });

  it('打开时带上 include_duplicates 拉一次', async () => {
    mockedStats.mockResolvedValue(statsWith([]));
    render(<SuspectedDuplicatesPanel open />);

    await waitFor(() => expect(mockedStats).toHaveBeenCalledTimes(1));
    expect(mockedStats).toHaveBeenCalledWith({ includeDuplicates: true });
  });
});

describe('三种结果必须表现不同', () => {
  it('有重复卡时列出标题与相似度', async () => {
    mockedStats.mockResolvedValue(statsWith([pair()]));
    render(<SuspectedDuplicatesPanel open />);

    expect(await screen.findByText('操作术语')).toBeInTheDocument();
    // 相似度按百分比展示（0.9987 → 99.9%）
    expect(screen.getByText(/99\.9%/)).toBeInTheDocument();
    expect(screen.getByText(/共 1 对/)).toBeInTheDocument();
  });

  it('★ 空数组 = 真的没有重复', async () => {
    mockedStats.mockResolvedValue(statsWith([]));
    render(<SuspectedDuplicatesPanel open />);

    expect(await screen.findByText('没有发现疑似重复的卡片')).toBeInTheDocument();
  });

  it('★★ null = 没统计到，**不能**说成"没有重复"', async () => {
    // 这是这一组最重要的一条：把"统计挂了"渲染成"你的数据很干净"
    // 是假的好消息，而用户没有任何办法发现它。
    mockedStats.mockResolvedValue(statsWith(null));
    render(<SuspectedDuplicatesPanel open />);

    expect(await screen.findByText(/暂时无法统计/)).toBeInTheDocument();
    expect(screen.queryByText('没有发现疑似重复的卡片')).not.toBeInTheDocument();
  });

  it('请求失败时显示错误（role=alert）而不是空白', async () => {
    mockedStats.mockRejectedValue(new Error('网络断了'));
    render(<SuspectedDuplicatesPanel open />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('网络断了');
  });
});

describe('只报不处理（已拍板的边界）', () => {
  it('★ 面板里**没有任何操作按钮**', async () => {
    // 合并会动 review_states（复习历史变孤儿行），属产品语义变更，
    // 已拍板要另开一轮单独批。面板里出现按钮就等于把那个决定偷偷做了。
    mockedStats.mockResolvedValue(statsWith([pair(), pair({ card_id_1: 'c3', card_id_2: 'c4' })]));
    render(<SuspectedDuplicatesPanel open />);
    await screen.findByText(/共 2 对/);

    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });

  it('★ 明确告诉用户"当前只做提示、不自动处理"', async () => {
    mockedStats.mockResolvedValue(statsWith([pair()]));
    render(<SuspectedDuplicatesPanel open />);

    expect(await screen.findByText(/不自动处理/)).toBeInTheDocument();
  });

  it('说明里讲清"为什么不连边"（重复 ≠ 相关）', async () => {
    mockedStats.mockResolvedValue(statsWith([]));
    render(<SuspectedDuplicatesPanel open />);

    // 用户看到"标题相同却没连边"时，这一句是唯一的解释
    expect(await screen.findByText(/因为那不是.相关.而是.重复/)).toBeInTheDocument();
  });
});
