/**
 * @file 对话框基座（visual-refactor-plan 批次 D1）
 *
 * ## 它是给谁用的
 *
 * 全站有 8 套各写各的 modal / popover 内联实现（`DeleteNoteDialog` /
 * `VersionHistory` / `LinkManagerModal` / `Trash` / `LearningGoals` …），
 * 现状实测：`focusTrap` grep **0 命中**、`role="dialog"` 全站只有 1 处、
 * Esc 关闭只有 2/8 处有、遮罩色有 `0.5` 与 `0.4` 两种、`z-index` 全是手写的
 * `1000` / `99`。这个文件把"对话框该有的行为"收拢成一处，
 * 供**批次 D2** 逐个迁移那 5 套 modal（`ConfirmDialog` 也在 D3 基于它实现）。
 *
 * ⚠️ **本批次只新建基座，不迁移任何现有对话框** —— 工作区里没有第二个文件
 * import 它。这是刻意的：迁移与新建混在一批里，"行为没变"这件事就无法单独验收。
 * （批次 D3 起有第一个消费者：`ConfirmDialog.tsx`，它才把这套基座带进产物。）
 *
 * ## 七项能力，逐条对应到下面的哪个 useEffect
 *
 * | 能力 | 实现位置 |
 * |---|---|
 * | 焦点陷阱（Tab / Shift+Tab 循环） | `handleKeyDown` |
 * | Esc 关闭（可关） | `handleKeyDown` |
 * | 点遮罩关闭（可关）／点内容不关 | `handleOverlayClick`（内容区 `stopPropagation`） |
 * | `role` + `aria-modal` + `aria-labelledby` | 渲染段的 `panel` |
 * | 打开锁 body 滚动、关闭/卸载必然解除 | 滚动锁 effect |
 * | 关闭后焦点归位到触发元素 | 焦点归位 effect |
 * | 初始焦点（ref → 第一个可聚焦 → 容器本身） | 初始焦点 effect |
 *
 * ## 为什么焦点陷阱是一个 `onKeyDown` 而不是 document 上的监听器
 *
 * 监听器挂在**遮罩上**：焦点在对话框里时，keydown 会冒泡到遮罩；焦点若因为
 * 任何原因跑到对话框外，那说明焦点陷阱已经被别的东西破坏了 ——
 * 这时"Tab 一律拉回第一个可聚焦元素"是唯一安全的兜底（见 `handleKeyDown` 末段）。
 * 挂在 document 上会让"谁先处理 Esc"变成隐形的层级竞争（嵌套弹窗时尤其明显）。
 *
 * ## 滚动锁为什么挂在 effect 的**清理函数**上
 *
 * 见下方滚动锁 effect 前的注释：这是本批次最容易漏的一条 ——
 * 只在 `onClose` 里解，组件被直接卸载（切路由、父组件条件渲染）时锁会留在
 * `body` 上，整页再也滚不动。同样的坑 `Sidebar.tsx:78-82` 已经踩过一次。
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, type RefObject } from 'react';

// 类名从模块取（哈希后是 `_dialogOverlay_hash` 这类）—— 见 css-convention.md §3
import styles from './Dialog.module.css';

/**
 * 可聚焦元素的选择器（批次 D1 的点名要求）
 *
 * `[tabindex]` 那一支刻意排除了 `-1`：`tabindex="-1"` 能被 `focus()` 主动聚焦，
 * 但**不在 Tab 序列里** —— 它是"初始焦点的兜底落点"，不是循环的一环
 * （对话框容器自己就带 `tabIndex={-1}`）。
 */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select',
  'textarea',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/** 容器内当前所有可聚焦元素，文档顺序（`querySelectorAll` 就是文档顺序） */
function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute('hidden'),
  );
}

/** `Element` 上的 `contains` 只吃 `Node`；这个窄化顺便表达"焦点在不在容器里" */
function isInside(container: HTMLElement, node: EventTarget | null): boolean {
  return node instanceof Node && container.contains(node);
}

/** 不靠 `tabindex` 就能被 `focus()` 聚焦的标签 */
const NATIVELY_FOCUSABLE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY']);

/**
 * 这个元素**此刻**能不能真的吃到焦点
 *
 * 光看"在不在 DOM 里"不够 —— `focus()` 对下面这些元素是**静默 no-op**：
 * disabled 的表单控件、`inert` 子树里的任何东西。调用方看不见失败，
 * 只看到焦点莫名掉到了 `body` 上（按 Tab 会从页首重新开始）。
 */
function canRestoreFocus(el: HTMLElement): boolean {
  if (!el.isConnected) return false;
  if (el.matches(':disabled')) return false;
  if (el.closest('[inert]')) return false;
  // 带 `tabindex` 的（含 `-1`）可以被主动聚焦
  if (el.hasAttribute('tabindex')) return true;
  if (el.tagName === 'A') return el.hasAttribute('href');
  return NATIVELY_FOCUSABLE.has(el.tagName);
}

/**
 * 从 `el` 起向上找第一个能吃到焦点的元素（含自身）
 *
 * 向上找是因为触发元素常常是"某个容器里的一枚按钮"：按钮自己被禁用了，
 * 但它所在的工具栏 / 列表项还在，把焦点交给那一层比丢给 `body` 有用得多
 * （用户接着按 Tab 会从那个位置继续，而不是整页重来）。
 */
function findRestoreTarget(el: HTMLElement | null): HTMLElement | null {
  for (let node = el; node; node = node.parentElement) {
    if (canRestoreFocus(node)) return node;
  }
  return null;
}

interface DialogProps {
  /** 是否打开。`false` 时**完全不渲染**（连遮罩都不留） */
  open: boolean;
  /** 请求关闭（Esc / 点遮罩 / 调用方自己的关闭按钮都走它） */
  onClose: () => void;
  /** 标题文案，渲染成面板顶部的一级块标题，并用 `useId()` 生成 `id` 给 `aria-labelledby` */
  title?: string;
  /** 调用方自己给标题元素 `id`（与 `title` 二选一；给了就优先用它） */
  labelledBy?: string;
  /**
   * 标题语气，默认 `'default'`（墨色标题）。
   *
   * `'danger'` 只做一件事：给标题加 `.dialogTitleDanger`，把颜色换成
   * `--color-error`（批次 D2 收尾，见 `Dialog.module.css` 那条的注释）。
   *
   * ⚠️ **留给真正不可恢复的操作**。迁移前只有两处红字标题
   * （`DeleteNoteDialog.tsx` 的「彻底删除」、`Trash.tsx` 的「清空回收站」），
   * 而 `DeleteNoteDialog.tsx` 的「移入回收站」**不是** —— 软删除可恢复。
   * 红色一旦到处都是就不再承载语义，加之前先问"这一步能不能撤销"。
   */
  titleTone?: 'default' | 'danger';
  /** 点遮罩是否关闭，默认 `true` */
  closeOnOverlayClick?: boolean;
  /** Esc 是否关闭，默认 `true` */
  closeOnEsc?: boolean;
  /** 打开时优先聚焦的元素（例如表单的第一个输入框） */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /**
   * 底部的操作区（确认/取消这类按钮）。
   *
   * 与 `children` 分开是为了让焦点陷阱的**首元素**是可预期的：只写 children 时，
   * 谁第一个被渲染谁就吃到初始焦点；调用方要指定初始焦点得自己造 ref。
   *
   * ⚠️ 它渲染在 `children` **之后** —— 首元素仍然取决于调用方在 children 里
   * 先渲染哪个按钮（`ConfirmDialog` 正是靠"取消在前"来满足这条）。
   */
  footer?: React.ReactNode;
  /** 面板内容 */
  children?: React.ReactNode;
}

/**
 * 对话框基座
 *
 * ```tsx
 * <Dialog open={open} onClose={close} title="移入回收站">
 *   <p>确定吗？</p>
 *   <button className="btn btn-secondary" onClick={close}>取消</button>
 * </Dialog>
 * ```
 *
 * 面板的外形（最大宽度 / 内边距 / 圆角 / 阴影）由模块里的 `.dialogPanel` 给，
 * 调用方**不要**再传内联几何样式 —— 那是旧实现里 8 套 modal 各自长歪的原因。
 *
 * 调用方能改的**只有标题语气**这一个开关（`titleTone`，批次 D2 收尾）：它是
 * "危险/不可恢复"这一层语义的载体，红的稀缺性是它成立的前提。其余一律按基座来
 * —— 想加第二个外观 prop 之前，先看 `Dialog.module.css` 文件头的规矩。
 */
export function Dialog({
  open,
  onClose,
  title,
  labelledBy,
  titleTone = 'default',
  closeOnOverlayClick = true,
  closeOnEsc = true,
  initialFocusRef,
  footer,
  children,
}: DialogProps) {
  /** 遮罩（fixed 全屏那层）：点空白处关闭、keydown 冒泡到这里 */
  const overlayRef = useRef<HTMLDivElement>(null);
  /** 面板（`role="dialog"` 那个 div）：可聚焦元素的搜索范围 + 初始焦点的兜底落点 */
  const panelRef = useRef<HTMLDivElement>(null);
  /** 打开前拿着焦点的元素（通常是触发按钮）—— 关闭时还给它 */
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  /** `useId()` 生成的标题 `id`；调用方用 `labelledBy` 覆盖它时就落选 */
  const generatedTitleId = useId();
  const titleId = labelledBy ?? generatedTitleId;

  /**
   * 能力 5：打开时锁 `body` 滚动，**关闭与卸载都必然解除**
   *
   * 用 `useLayoutEffect` 而不是 `useEffect`：它在浏览器绘制前跑完，
   * 打开的那一帧不会先滚一下再锁住。
   *
   * 三条路径都覆盖了：① `open` 变 false → 清理函数跑；② 组件被卸载 →
   * React 同样跑清理函数（这是最容易漏的一条：只在 `onClose` 里解，
   * 组件是被父级直接卸载的 —— 切路由、条件渲染 —— 锁就永久留在 body 上）；
   * ③ 从未打开过（`open: false`）→ 提前 return，连加都不加。
   *
   * 类名从模块取（`styles.dialogScrollLock`），样式侧的 `body.dialogScrollLock`
   * 用的是同一个模块类名，两边不可能分叉（与 `Sidebar.tsx:73-82` 同一套做法）。
   */
  useLayoutEffect(() => {
    if (!open) return;
    document.body.classList.add(styles.dialogScrollLock);
    return () => document.body.classList.remove(styles.dialogScrollLock);
  }, [open]);

  /**
   * 能力 6：关闭后把焦点还给"打开前的那个元素"
   *
   * 这个 effect **在 effect 声明顺序上必须排在初始焦点那个之前**：
   * 打开的那一次渲染里，它先把 `document.activeElement`（= 触发按钮）记下来，
   * 紧接着初始焦点 effect 才会把焦点搬进对话框。顺序反了记到的就是对话框自己。
   *
   * 归位放在清理函数里，于是与滚动锁同理：卸载也会归位。
   *
   * ⚠️ **触发元素还在 DOM 里、但已经不可聚焦**是一条真实路径（批次 D3 后半
   * 修的就是它）：确认删除之后，那一帧里「关框」与「按钮进入 loading/disabled」
   * 是同一批 state 更新，等这个清理函数跑到时按钮已经 `disabled` 了。
   * 而 `focus()` 对 disabled 元素是**静默 no-op** —— 焦点于是掉到 `body` 上，
   * 用户按 Tab 会从页首重新开始，看起来像"焦点丢了"。
   * 所以这里不直接对触发元素调 `focus()`，先向上找一个真的能收焦点的落点。
   */
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => {
      const origin = restoreFocusRef.current;
      restoreFocusRef.current = null;
      // 找不到（触发元素及其祖先链都不可聚焦）：什么都不做，焦点由浏览器放在
      // `body` 上。这是基座层的边界 —— 要精确到"被删掉的下一行"，
      // 得由调用方给出锚点，那属于调用方的语义，基座不该猜。
      findRestoreTarget(origin)?.focus();
    };
  }, [open]);

  /**
   * 能力 7：初始焦点
   *
   * 优先级：`initialFocusRef` → 容器内第一个可聚焦元素 → 容器本身
   * （容器带 `tabIndex={-1}`，所以"里面什么都没有"时焦点也有个去处，
   * 不会留在背后的页面上 —— 那会让焦点陷阱从第一下 Tab 起就失效）。
   */
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const target = initialFocusRef?.current ?? getFocusable(panel)[0] ?? panel;
    target.focus();
  }, [open, initialFocusRef]);

  /**
   * 能力 3：点遮罩关闭 / 点内容不关
   *
   * 内容区的 `onClick` 会 `stopPropagation`，所以冒泡到这里的一定是遮罩本身。
   * 但这层判断不省：键盘（Enter 激活某个按钮）与测试里的直接 `dispatchEvent`
   * 都可能让事件"看起来"来自遮罩却其实来自面板内部的某个路径。
   */
  const handleOverlayClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (!closeOnOverlayClick) return;
      if (event.target !== event.currentTarget) return;
      onClose();
    },
    [closeOnOverlayClick, onClose],
  );

  /**
   * 能力 1 + 2：焦点陷阱与 Esc
   *
   * ⚠️ **容器内一个可聚焦元素都没有**（`items.length === 0`）时的行为：
   * 两条分支都退化成"把焦点放在容器自己身上"（`panel.focus()`，
   * 容器有 `tabIndex={-1}`）。也就是说此时 Tab / Shift+Tab 都**不会**把焦点
   * 漏到背后的页面上，但也不会有什么可循环的东西 —— 这是唯一正确的语义：
   * 焦点陷阱的作用域就是"对话框这个盒子"，盒子里没东西可点时，盒子自己收着焦点。
   */
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') {
        if (!closeOnEsc) return;
        // 阻止冒泡：嵌套对话框时，Esc 只该关掉最上面那一层。
        // （React 的合成事件按组件树冒泡，内层先收到 —— `stopPropagation`
        //  在这里就是"只关一层"的实现。）
        event.stopPropagation();
        onClose();
        return;
      }

      if (event.key !== 'Tab') return;

      const panel = panelRef.current;
      if (!panel) return;

      const items = getFocusable(panel);
      const first = items[0];
      const last = items[items.length - 1];

      // 盒子里没有可聚焦元素：焦点一律收回盒子本身（见上方注释）
      if (!first || !last) {
        event.preventDefault();
        panel.focus();
        return;
      }

      const active = document.activeElement;

      // 焦点在盒子外（或还没进来）：Tab 一律拉回第一个，不往后猜
      if (!isInside(panel, active)) {
        event.preventDefault();
        first.focus();
        return;
      }

      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
        return;
      }

      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
      // 其余情况交给浏览器：中间元素的 Tab 不该被我们接管
    },
    [closeOnEsc, onClose],
  );

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      className={styles.dialogOverlay}
      onClick={handleOverlayClick}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={panelRef}
        className={styles.dialogPanel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        // 兜底落点：`-1` = 可被 `focus()` 聚焦、但不在 Tab 序列里
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        {title ? (
          <h2
            id={titleId}
            // 基础的 `.dialogTitle` 恒定在，`'danger'` 只**追加**一个改色类
            // （与 `GraphToolbar.tsx:142` 组合模块类名同一套写法）。
            // ⚠️ 不传 `titleTone` 时拼接结果必须与迁移前**逐字相同** ——
            // 那 5 套 modal 里只有 2 处该是红字标题。
            className={`${styles.dialogTitle}${
              titleTone === 'danger' ? ` ${styles.dialogTitleDanger}` : ''
            }`}
          >
            {title}
          </h2>
        ) : null}
        {children}
        {footer ? <div className={styles.dialogActions}>{footer}</div> : null}
      </div>
    </div>
  );
}

export default Dialog;
