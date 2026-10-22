/**
 * TanStack Query 的全局客户端（迁移 5.2 / C1，2026-10-22）
 *
 * ## 为什么实例是**模块级常量**
 *
 * `QueryClient` 承载整个缓存。放在组件里（`useState(() => new QueryClient())`
 * 或每次渲染 `new`）会**每次渲染换一份缓存** —— 表现是"刚拉到的数据立刻又没了、
 * 每次都重新打请求"，而它不会报任何错。模块级常量是这里唯一正确的形态。
 *
 * ## 默认值的理由（计划 §2.3，已批准）
 *
 * | 选项 | 取值 | 理由 |
 * |---|---|---|
 * | `staleTime` | 30 秒 | 单机应用、数据变动来自本人操作；30 秒内切页面不重复打请求 |
 * | `retry`（query） | 1 | 网络抖动重试一次；再多会拖长"看起来卡住"的时间 |
 * | `retry`（mutation） | **0** | 写操作**不能盲目重试** —— 上传/提交/删笔记都不是幂等的 |
 * | `refetchOnWindowFocus` | `false` | 单机单窗口；开着会让"切回来就刷新"变成不可预期的行为变化 |
 *
 * ⚠️ `retry: 0`（mutation）这一条不是"保守"而是**正确性**：
 * 一个超时但实际成功的 `POST /notes` 被自动重试会建出两篇笔记，
 * 而用户只会看到"多了一篇一模一样的"。
 *
 * ## 测试环境为什么要单独一份
 *
 * `createTestQueryClient()` 用 `retry: false` + `gcTime: 0`：
 * - `retry: false` —— 否则一条"请求失败"的用例要等 1 次退避重试才变红，慢且掩盖真实时序；
 * - `gcTime: 0` —— 每个用例之间不留缓存，避免"上一个用例的数据喂给下一个"
 *   造成的**假绿**（那种绿看起来像功能正常，实际是缓存串味）。
 *
 * 它与生产配置**分开导出**而不是"用参数区分"：测试里显式写
 * `createTestQueryClient()` 比 `new QueryClient({ ...prod, retry: false })`
 * 更难写错 —— 后者要求调用方记得展开生产默认值。
 */
import { QueryClient } from '@tanstack/react-query';

/** 生产用的默认值（计划 §2.3，已由用户批准） */
export const QUERY_DEFAULTS = {
  queries: {
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  },
  mutations: {
    // 写操作不重试：见文件头（上传/提交/删笔记都不是幂等的）
    retry: 0,
  },
} as const;

/** 全局唯一实例。见文件头"为什么是模块级常量"。 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { ...QUERY_DEFAULTS.queries },
    mutations: { ...QUERY_DEFAULTS.mutations },
  },
});

/**
 * 测试专用实例：不重试、不留缓存（理由见文件头）
 *
 * 每个测试文件调一次，让用例之间完全隔离。
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { ...QUERY_DEFAULTS.queries, retry: false, gcTime: 0 },
      mutations: { ...QUERY_DEFAULTS.mutations },
    },
  });
}
