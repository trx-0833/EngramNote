/**
 * @file 统一确认框（visual-refactor-plan 批次 D3）
 *
 * ## 它替换的是什么
 *
 * 全站此前用 `window.confirm` / 裸 `confirm` 做二次确认（grep 实测 14 处：
 * 组件内 7 处 + 自定义 hook 里 7 处）。原生确认框的问题不在外观 —— 它
 * **不显示上下文**（用户看不到自己要删的是哪一条）、**不可样式化**、
 * **不可测**（jsdom 里的 `confirm` 永远返回 `undefined`，只能 `spyOn` 桩掉），
 * 而且它在移动端是一句带域名的系统弹窗。
 *
 * 本批次（D3 前半）只做**组件内**那 7 处：组件能渲染 JSX，所以
 * "打开/关闭"用一个 `useState` 就能承载。**hook 里那 7 处不在本批**
 * —— hook 不能渲染 JSX，需要另设计一个 `useConfirm()` 模式（下一步）。
 *
 * ## 为什么是"同步 → 异步"的改写，以及调用方要负责什么
 *
 * `window.confirm` **同步返回 boolean**，项目内的对话框**异步等用户点击**。
 * 于是原来那种
 *
 * ```ts
 * async function handleDelete() {
 *   if (!confirm('确定删除？')) return;   // 这一行之后就往下走
 *   await deleteThing();                  // ① 确认后执行
 * }                                        // ② 取消后什么也不发生
 * ```
 *
 * 必须拆成两半：**触发**（打开对话框）与**执行**（`onConfirm` 里那段）。
 * 四条路径要逐条原地保留：
 *
 * | 路径 | 原来 | 现在 |
 * |---|---|---|
 * | ① 确认后执行什么 | `confirm` 为真后的那段 | `onConfirm` 里那段（逐字） |
 * | ② 取消后什么也不发生 | `return` | `onCancel` 只关对话框 |
 * | ③ 执行中有没有 loading/禁用 | 同步阻塞，天然没有 | **不新增**：原来没有就仍然没有 |
 * | ④ 执行失败怎么提示 | `catch` 里的 `toast.error` / `setError` | 原样留在执行段里 |
 *
 * ⚠️ **不要在 `onConfirm` 里"顺手"加 await 之后的收尾逻辑**：`onConfirm` 是
 * 事件回调、不是 `await` 上下文。原来那段是 async 的，就用一个内部
 * async 函数包住，并把原来的 `catch` 一起搬进去（见 D3 的 7 处调用点）。
 *
 * ## 为什么不自己实现焦点陷阱 / Esc / 遮罩
 *
 * 那七件事由 `Dialog`（批次 D1）负责，本文件一个字都不重复实现 ——
 * 两套实现的必然结果是"其中一套先坏，而没人知道是哪一套在生效"。
 *
 * ## 初始焦点为什么钉在"取消"上
 *
 * 危险操作不该让"确认"成为回车/空格的默认目标：弹窗刚出现时用户手还在键盘上，
 * 一次误按就是不可恢复的删除。实现有两层（都要留着）：
 *   1. **按钮顺序**：操作区里先渲染"取消"，`Dialog` 的初始焦点取面板内第一个
 *      可聚焦元素，拿到的就是它；
 *   2. **显式 `initialFocusRef`**：把意图写成代码。`Dialog` 的初始焦点 effect
 *      在面板首次挂载时跑，那时 ref 还没赋值，所以真正的落点是第 1 层
 *      —— 这一条是给"以后有人调换按钮顺序"留的护栏。
 *
 * ## 操作区为什么走 `Dialog` 的 `footer`
 *
 * 两枚按钮放进 `footer` 而不是 `children`：底部右对齐的间距与上边距由
 * `Dialog.module.css` 的 `.dialogActions` 统一给（`.btn` 自己的 `gap` 管的是
 * 按钮**内部**图标与文字的间距，管不到两枚按钮之间）。这也让"按钮在哪里"
 * 只有一个答案 —— 7 个调用点各写一遍内联布局正是旧实现长歪的原因。
 *
 * ## 样式
 *
 * 本文件**没有** `ConfirmDialog.module.css`：按钮是全局 `.btn` 家族
 * （`components.css`）、面板与操作区是 `Dialog.module.css` 的模块类，
 * 这里没有一个自己的类名。为"整齐"造一个空模块只会多一个永不被引用的文件。
 */
import { useCallback, useRef } from 'react';
import Dialog from './Dialog';

interface ConfirmDialogProps {
  /** 是否打开。`false` 时 `Dialog` **完全不渲染**（连遮罩都不留） */
  open: boolean;
  /** 标题。⚠️ 原来 `window.confirm` 的文案要**原样**放进来，不要借机改写 */
  title: string;
  /** 补充说明（可选）。长文案放这里，短问句留在 `title` */
  message?: string;
  /** 影响范围清单（可选），逐条渲染成列表项（例如 ['3 张知识卡片', '2 道题目']） */
  impactList?: string[];
  /** 确认按钮文案，默认「确认」 */
  confirmText?: string;
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string;
  /** 危险操作（删除这类不可恢复的）：确认按钮用红色实底 `.btn-danger` */
  danger?: boolean;
  /** 用户确认。执行段放这里（逐字搬原来 `confirm` 为真之后那段） */
  onConfirm: () => void;
  /** 用户取消（点取消 / Esc / 点遮罩三条路都走它）：什么都不做，只关对话框 */
  onCancel: () => void;
}

/**
 * 统一确认框
 *
 * ```tsx
 * const [confirmingDelete, setConfirmingDelete] = useState(false);
 *
 * async function performDelete() {
 *   try {
 *     await deleteThing(id);
 *     navigate('/things');
 *   } catch (err) {
 *     toast.error(err instanceof Error ? err.message : '删除失败');
 *   }
 * }
 *
 * <ConfirmDialog
 *   open={confirmingDelete}
 *   title="确定删除？"
 *   danger
 *   onConfirm={() => { setConfirmingDelete(false); void performDelete(); }}
 *   onCancel={() => setConfirmingDelete(false)}
 * />
 * ```
 */
export function ConfirmDialog({
  open,
  title,
  message,
  impactList,
  confirmText = '确认',
  cancelText = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  /**
   * 点"确认"：**先关对话框，再执行**。
   *
   * 顺序不能反 —— 执行段是异步的（网络请求），反过来会把用户锁在一个已经
   * 点完的对话框里，而 `Dialog` 的焦点陷阱正把焦点困在里面（期间连 Esc 都
   * 只是再关一次）。原来的 `window.confirm` 是同步的：用户点完，弹窗**立刻**
   * 消失、背后的按钮才进入 loading —— "先关后执行"才是逐字保留原行为。
   */
  const handleConfirm = useCallback(() => {
    onCancel();
    onConfirm();
  }, [onCancel, onConfirm]);

  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      initialFocusRef={cancelRef}
      footer={
        <>
          {/* ⚠️ 顺序是行为：这个按钮必须排在确认按钮**之前**（见文件头"初始焦点"） */}
          <button ref={cancelRef} type="button" className="btn btn-secondary" onClick={onCancel}>
            {cancelText}
          </button>
          <button
            type="button"
            className={danger ? 'btn btn-danger' : 'btn btn-primary'}
            onClick={handleConfirm}
          >
            {confirmText}
          </button>
        </>
      }
    >
      {message ? <p>{message}</p> : null}
      {impactList && impactList.length > 0 ? (
        <ul>
          {impactList.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
    </Dialog>
  );
}

export default ConfirmDialog;
