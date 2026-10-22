/**
 * 共享的测试渲染助手（迁移 5.2 / C1，2026-10-22）
 *
 * ## 为什么必须有它
 *
 * 页面组件从"自己 `fetch` + `useEffect`"改成 `useQuery` 之后，
 * **渲染它们就必须有 `QueryClientProvider`** —— 否则 `useQuery` 直接抛
 * "No QueryClient set"。改动前有 ~10 个测试文件各自写了本地 `renderX()` 助手
 * （实测 `renderX(` 22 处），每个都要各自补 Provider 的话，
 * 补法一旦不一致就会出"某些文件绿、某些文件红"的假象。
 *
 * 所以先有这一个助手，再**逐个文件**改过去（计划 §2.2：不批量重写）。
 *
 * ## 三个刻意的设计
 *
 * 1. **每个调用新建一份 `QueryClient`**（`createTestQueryClient()`）——
 *    共用一份会让用例之间串缓存：前一个用例的数据喂给后一个，
 *    表现为**假绿**（看起来功能正常，实际缓存串味）。
 * 2. **`retry: false` / `gcTime: 0`**（见 `api/queryClient.ts` 的说明）。
 * 3. **不默认套 `MemoryRouter`** —— 只有需要路由的页面才套。
 *    给纯展示组件套上会掩盖"这个组件其实在偷偷用路由/取数"。
 *    需要路由时显式传 `route`。
 *
 * ## 纯展示组件**不要**用这个助手
 *
 * 计划 §2.2 第 3 条：`StatCard` / `SelfRatingButtons` / `ReviewProgress` /
 * `SourceContext` 这类不取数的组件保持裸 `render()` ——
 * 给它们接上 Provider 会**掩盖**"这个组件其实在偷偷取数"这件事。
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';

import { createTestQueryClient } from '../api/queryClient';

export interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  /**
   * 传入即套 `MemoryRouter` 并把初始路径设成它。
   *
   * 不传 = 不套路由。需要 `useNavigate` / `useParams` / `<Link>` 的页面必须传，
   * 否则报 "useNavigate() may be used only in the context of a <Router>"。
   */
  route?: string;
}

/** 渲染时套上取数层（+ 可选路由层） */
export function renderWithProviders(
  ui: ReactElement,
  { route, ...options }: RenderWithProvidersOptions = {},
): RenderResult {
  const client = createTestQueryClient();

  const inner =
    route === undefined ? ui : <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>;

  return render(<QueryClientProvider client={client}>{inner}</QueryClientProvider>, options);
}

/**
 * 只要 `QueryClientProvider`、不要路由的包装器（给 `render(ui, { wrapper })` 用）
 *
 * 与 `renderWithProviders` 的差别只是"调用形态"：有些用例需要在
 * `render` 之外还要 `rerender`，用 `wrapper` 更顺手。
 */
export function QueryWrapper({ children }: { children: React.ReactNode }) {
  const client = createTestQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
