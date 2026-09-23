/**
 * @file 确认框的 Promise 化封装（visual-refactor-plan 批次 D3 后半）
 *
 * ## 它要解决的形状问题
 *
 * `window.confirm` **同步返回 boolean**，调用方可以原地写：
 *
 * ```ts
 * if (!confirm('确定删除？')) return;   // 一行就是一个岔路
 * await deleteThing();
 * ```
 *
 * 批次 D3 前半把这套换成了 `ConfirmDialog`（受控的 `open` + `onConfirm`/`onCancel`），
 * 那对**组件**可行 —— 组件能渲染 JSX，"打开/关闭"用 `useState` 就装得下。
 * 但全站还有 7 处确认在**自定义 hook** 里（`useProjects` / `useGraphMutations` /
 * `useNoteActions` / `useNoteAnnotations`）—— hook 不能渲染 JSX，
 * 它唯一的出路是"把确认请求交给上层、拿回一个 Promise"：
 *
 * ```ts
 * const confirm = useConfirm();
 * if (!(await confirm({ title: '确定删除？' }))) return;   // 仍是"一行一个岔路"
 * await deleteThing();
 * ```
 *
 * 于是调用点的**形状保持不变**：那里原来是一句同步的 `if (!confirm(...)) return`，
 * 现在是一句 `await` 过的同义句。要改的只有"这个 `confirm` 从哪来"。
 *
 * ## 为什么 Provider 必须自己持有 resolver
 *
 * Promise 的 `resolve` 只能从"用户点了哪一边"那一刻取到，而那一刻发生在
 * **Provider 渲染的那棵树**里。所以：`confirm()` 造一个 Promise、把 `resolve`
 * 存进 ref、再把请求写进 state 触发渲染；用户点完，从 ref 里取出 `resolve` 结账。
 * 存 ref 而不是 state 是必须的 —— 函数身份不稳定会让每次渲染都换一个 resolver。
 *
 * ## ⚠️ 最险的一处：`onCancel` 不等于「取消」
 *
 * `ConfirmDialog.handleConfirm` 是**先调 `onCancel()` 关框、再调 `onConfirm()`**
 * （见那个文件头的「点确认：先关对话框，再执行」—— 它必须这样，
 * 否则用户会被锁在一个已点完的框里）。也就是说 `onCancel` 在"确认"这条路径上
 * **也会被调一次**。
 *
 * 如果这里把 Promise 的结账直接挂在 `onCancel` 上，点「确认」会先把 Promise
 * 结成一个 `false` —— 调用方于是**永远走取消分支**，而框看起来是正常关掉的。
 * 这是本文件唯一会静默吃掉用户操作的地方。
 *
 * 解法是把这两件事拆开：`ConfirmDialog` 的 `onConfirmClose` prop 承担"确认后的
 * 关闭"，`onCancel` 只承担"取消"。`settle(true)` 与 `settle(false)` 各自独立。
 *
 * ## 同时只允许一个确认框
 *
 * 第二个 `confirm()` 到来时，前一个若还挂着，先把它按**取消**结掉。
 * 理由：它对应的那个框马上就会被新请求覆盖（state 里只有一个 `request`），
 * 用户看不到、也点不到它了；不结掉的话它的 `await` 会**永久悬挂**，
 * 调用点后面那整段代码再也不会执行 —— 静默失效比"当成取消"更糟。
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import ConfirmDialog from './ConfirmDialog';

/** 一个确认请求的全部外观参数（与 `ConfirmDialog` 的同名 props 一一对应） */
export interface ConfirmOptions {
  /** 标题。⚠️ 原来 `window.confirm` 的文案要**原样**放进来，不要借机改写 */
  title: string;
  /** 补充说明（可选）。长文案放这里，短问句留在 `title` */
  message?: string;
  /** 影响范围清单（可选），逐条渲染成列表项 */
  impactList?: string[];
  /** 确认按钮文案，默认「确认」 */
  confirmText?: string;
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string;
  /** 危险操作：确认按钮用红色实底 */
  danger?: boolean;
}

/** 确认结果。`true` = 用户点了确认，`false` = 取消 / Esc / 点遮罩 */
export type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * 确认框宿主
 *
 * 挂在应用最外层（`main.tsx`，与 `ToastProvider` 同级）。
 * 它渲染一个**常驻**的 `ConfirmDialog`，`open` 由"当前有没有请求"决定。
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  /** 当前请求。`null` = 没有框要显示 */
  const [request, setRequest] = useState<ConfirmOptions | null>(null);
  /** 当前请求的 Promise 结账函数；与 `request` 同生共死 */
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  /**
   * 结账并关框
   *
   * 先取空 ref 再 resolve：`resolve` 会同步把调用方的 `await` 之后那段代码
   * 排进微任务，那段代码里可能又调一次 `confirm()` —— 若 ref 还没清空，
   * 新请求会把 `resolve` 覆盖掉，两个 Promise 一起悬挂。
   */
  const settle = useCallback((ok: boolean) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setRequest(null);
    resolve?.(ok);
  }, []);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      // 上一个请求还在挂着：先按取消结掉它（见文件头「同时只允许一个确认框」）
      resolverRef.current?.(false);
      resolverRef.current = resolve;
      setRequest(options);
    });
  }, []);

  /**
   * `confirm` 是 `useCallback([])` 的稳定引用，所以这份 value 也恒定 ——
   * 用 `useMemo` 是为了把这个不变量写进代码（消费它的 hook 依赖 `confirm`
   * 时不会每次渲染都失效）。
   */
  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <ConfirmDialog
        open={request !== null}
        title={request?.title ?? ''}
        message={request?.message}
        impactList={request?.impactList}
        confirmText={request?.confirmText}
        cancelText={request?.cancelText}
        danger={request?.danger}
        // 确认路径：先关框（不结账），再由 onConfirm 结 true。
        // 两者顺序与 ConfirmDialog.handleConfirm 一致，见文件头。
        onConfirmClose={() => setRequest(null)}
        onConfirm={() => settle(true)}
        // 取消路径：点取消 / Esc / 点遮罩三条路都到这里，结 false 并关框
        onCancel={() => settle(false)}
      />
    </ConfirmContext.Provider>
  );
}

/**
 * 取确认函数
 *
 * ```ts
 * const confirm = useConfirm();
 * if (!(await confirm({ title: '确定删除此批注？', danger: true }))) return;
 * ```
 *
 * ⚠️ **调用它的组件必须在 `<ConfirmProvider>` 之内**。Provider 外调用会抛错
 * 而不是返回一个永远返回 `false` 的实现 —— 后者会让"确认了但什么都没发生"
 * 变成一个查不出原因的 bug（与 `useToast` 的判据一致）。
 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error('useConfirm 必须在 <ConfirmProvider> 内部使用（见 main.tsx）');
  }
  return ctx;
}

export default ConfirmProvider;
