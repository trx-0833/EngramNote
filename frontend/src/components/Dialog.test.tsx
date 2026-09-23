/**
 * @file 对话框基座的行为测试（visual-refactor-plan 批次 D1）
 *
 * ## 这一页要守的是什么
 *
 * `Dialog.tsx` 的七项能力里，**只有两件**能被 tsc / eslint 看见（`role` 与
 * `aria-*` 属性在不在）。其余五件全是运行时行为：焦点陷阱会不会把 Tab 漏到
 * 背后的页面、Esc 该不该关、点内容会不会误关、body 滚动锁在自己被卸载时
 * 有没有解开、焦点有没有还给触发按钮。这些**没有 CSS 兜底、没有类型兜底**，
 * 只有断言能钉住 —— 而它们恰恰是"8 套各写各的 modal"里错得最多的几处
 * （现状 `focusTrap` grep 0 命中、Esc 只有 2/8 处有）。
 *
 * ## jsdom 测不到的部分（本文件刻意不假装覆盖）
 *
 * 1. **真实的 Tab 序列**。jsdom 不实现浏览器按 `tabindex` / 视觉顺序算出来的
 *    焦点顺序，所以这里**不用 `userEvent.tab()`**（它依赖大量布局 API，
 *    在这个环境里要么抛错要么给出与浏览器无关的结论）。改成派发一个真实的
 *    `keydown`（`shiftKey` 如实带上）到面板上：这正是浏览器按 Tab 时会发出的
 *    那个事件，而被测代码处理的**就是**这个事件。断言的是它搬动焦点的结果。
 * 2. **动画与窄屏观感**（遮罩淡入、面板落下、768/480 两档内边距）需要真浏览器；
 *    本文件一个字都不假装覆盖。样式侧只有"滚动锁类名加没加到 body 上"这一条
 *    是行为，其余是外观。
 *
 * ## 关于类名（css-convention.md §6）
 *
 * 需要按类名查的只有三处：遮罩（`styles.dialogOverlay`，它没有 ARIA 角色）、
 * 滚动锁（`styles.dialogScrollLock`，挂在 `body` 上）、标题危险色
 * （`styles.dialogTitleDanger` —— "红不红"是纯外观，两种语气下
 * `getByRole('heading')` 拿到的元素与可访问名**完全一样**，语义查询看不见它）。
 * 其余一律语义查询（`getByRole('dialog')` / `getByRole('button', { name })`）。
 */
import { useRef, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Dialog } from './Dialog';
// 遮罩与滚动锁的类名（哈希后只能用模块导出查）—— 见文件头
import styles from './Dialog.module.css';

/**
 * 测试用的受控外壳
 *
 * 为什么不直接渲染 `<Dialog>`：本文件要断言的多数行为（滚动锁的加/解、
 * 焦点归位、`open` 切换）都发生在**同一个组件实例的生命周期**上，
 * 用 `rerender` 改 `open` 才等价于真实用法 —— 每次都新挂一个实例会
 * 把"关闭"这一步变成"卸载"，测到的是另一条路径。
 *
 * ⚠️ 这里刻意**不**在渲染期计次（曾经有过一个 `renderCount` 探针）：
 * `react-hooks/globals` 会判它 "Cannot reassign variables declared outside of
 * the component/hook"，而那条规则是对的 —— 渲染期改模块级变量就是副作用。
 * "渲染期没有手滑调 `onClose`"改由用例里的 `expect(onClose).not.toHaveBeenCalled()`
 * 与 `toHaveBeenCalledTimes(1)` 直接钉住：前者要求 0 次、后者要求恰好 1 次，
 * 多调一次就会红。
 */
interface HarnessProps {
  open: boolean;
  onClose?: () => void;
  title?: string;
  labelledBy?: string;
  titleTone?: 'default' | 'danger';
  closeOnOverlayClick?: boolean;
  closeOnEsc?: boolean;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  children?: React.ReactNode;
}

function Harness({
  open,
  onClose = () => {},
  title,
  labelledBy,
  titleTone,
  closeOnOverlayClick,
  closeOnEsc,
  initialFocusRef,
  children,
}: HarnessProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      labelledBy={labelledBy}
      titleTone={titleTone}
      closeOnOverlayClick={closeOnOverlayClick}
      closeOnEsc={closeOnEsc}
      initialFocusRef={initialFocusRef}
    >
      {children}
    </Dialog>
  );
}

/** 打开状态的对话框，内容是两个真实按钮（焦点陷阱要有东西可循环） */
function openDialog(props: Partial<HarnessProps> = {}) {
  return render(
    <Harness open title="测试对话框" {...props}>
      <p>正文</p>
      <button type="button">第一个</button>
      <button type="button">最后一个</button>
    </Harness>,
  );
}

/** 面板内的两个按钮（语义查询，不碰哈希类名） */
function dialogButtons(): [HTMLElement, HTMLElement] {
  return [
    screen.getByRole('button', { name: '第一个' }),
    screen.getByRole('button', { name: '最后一个' }),
  ];
}

function overlay(): HTMLElement {
  const el = document.querySelector<HTMLElement>(`.${styles.dialogOverlay}`);
  if (!el) throw new Error('页面上没有遮罩 —— styles.dialogOverlay 没对上');
  return el;
}

/**
 * 按一次 Tab（或 Shift+Tab）
 *
 * 派发到面板上：浏览器里按 Tab 时 keydown 就是从"当前有焦点的那个元素"发出、
 * 冒泡到面板的，这里等价，且不依赖 jsdom 的布局能力（见文件头）。
 */
function pressTab(shiftKey = false) {
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab', shiftKey });
}

function pressEscape() {
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
}

afterEach(() => {
  // 滚动锁是挂在 body 上的全局状态：任何一个用例把它留下都会污染后面的文件
  document.body.classList.remove(styles.dialogScrollLock);
  document.body.removeAttribute('style');
});

describe('Dialog：结构与可访问性三件套', () => {
  it('role="dialog" + aria-modal="true" + aria-labelledby 指到标题上', () => {
    openDialog();

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('测试对话框');

    // aria-labelledby 是"指到标题元素上"，不是另写一份 aria-label
    const labelledBy = dialog.getAttribute('aria-labelledby');
    expect(labelledBy).toBeTruthy();
    expect(document.getElementById(labelledBy as string)).toHaveTextContent('测试对话框');
  });

  it('标题与子树真的渲染出来了，且没有任何"渲染期手滑调 onClose"', () => {
    const onClose = vi.fn();
    openDialog({ onClose });

    expect(screen.getByRole('heading', { name: '测试对话框' })).toBeVisible();
    expect(screen.getByText('正文')).toBeVisible();
    expect(onClose).not.toHaveBeenCalled(); // ← 渲染不该触发关闭
  });

  it('open=false 时什么都不渲染（连遮罩都不留）', () => {
    render(<Harness open={false} title="测试对话框" />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector(`.${styles.dialogOverlay}`)).toBeNull();
  });

  it('labelledBy 给了就用它，覆盖自动生成的 id', () => {
    render(
      <Harness open title="被覆盖的标题" labelledBy="external-title">
        <h2 id="external-title">外部标题</h2>
      </Harness>,
    );

    expect(screen.getByRole('dialog')).toHaveAttribute('aria-labelledby', 'external-title');
  });
});

describe('Dialog：标题语气（titleTone，批次 D2 收尾）', () => {
  it('★ 不传 titleTone 时标题不带危险色类；titleTone="danger" 时才带上', () => {
    // 同一个用例里 render 两次之前必须卸掉前一个：两个对话框同时在文档里，
    // 下面所有 getByRole 都会撞上 "found multiple elements"
    const plainRender = openDialog();

    const plainTitle = screen.getByRole('heading', { name: '测试对话框' });
    expect(plainTitle).toHaveClass(styles.dialogTitle);
    expect(plainTitle).not.toHaveClass(styles.dialogTitleDanger);
    plainRender.unmount();

    openDialog({ titleTone: 'danger' });

    const dangerTitle = screen.getByRole('heading', { name: '测试对话框' });
    // danger 只**追加**一个改色类，基础类不能被替换掉（否则字重/间距全丢）
    expect(dangerTitle).toHaveClass(styles.dialogTitle);
    expect(dangerTitle).toHaveClass(styles.dialogTitleDanger);
  });
});

describe('Dialog：焦点陷阱', () => {
  it('★ Tab 在最后一个元素上按 → 焦点回到第一个', async () => {
    openDialog();
    const [first, last] = dialogButtons();

    last.focus();
    expect(document.activeElement).toBe(last);

    pressTab();
    expect(document.activeElement).toBe(first); // 循环回第一个，而不是漏到页面外
  });

  it('★ Shift+Tab 在第一个元素上按 → 焦点回到最后一个', async () => {
    openDialog();
    const [first, last] = dialogButtons();

    first.focus();
    pressTab(true);
    expect(document.activeElement).toBe(last);
  });

  it('两个元素之间按 Tab 不接管（交给浏览器走正常顺序）', async () => {
    openDialog();
    const [first] = dialogButtons();

    first.focus();
    pressTab(); // 没有 preventDefault 的迹象：焦点仍是自己，浏览器会往后走
    expect(document.activeElement).toBe(first);
  });

  it('容器里一个可聚焦元素都没有时，Tab 停在容器本身（`tabIndex={-1}`）', () => {
    render(
      <Harness open title="空的对话框">
        <p>只有文字，没有任何可聚焦元素</p>
      </Harness>,
    );

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('tabindex', '-1');

    pressTab(); // 派发到容器上（它此时就是焦点落点）
    expect(document.activeElement).toBe(dialog);
    pressTab(true);
    expect(document.activeElement).toBe(dialog);
  });

  it('焦点从页面外进来时（容器外按 Tab）被拉回第一个可聚焦元素', async () => {
    openDialog();
    const [first] = dialogButtons();

    // 模拟"焦点已经在背后的页面上"：容器外的一个按钮
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);

    pressTab();
    expect(document.activeElement).toBe(first);
    outside.remove();
  });
});

describe('Dialog：Esc 关闭', () => {
  it('★ Esc 触发 onClose', () => {
    const onClose = vi.fn();
    openDialog({ onClose });

    pressEscape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('★ closeOnEsc={false} 时 Esc 不触发 onClose', () => {
    const onClose = vi.fn();
    openDialog({ onClose, closeOnEsc: false });

    pressEscape();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('Dialog：点遮罩关闭', () => {
  it('★ 点遮罩触发 onClose', async () => {
    const onClose = vi.fn();
    openDialog({ onClose });

    await userEvent.click(overlay());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('★ 点内容区**不**触发 onClose', async () => {
    const onClose = vi.fn();
    openDialog({ onClose });

    await userEvent.click(screen.getByText('正文'));
    await userEvent.click(screen.getByRole('button', { name: '第一个' }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closeOnOverlayClick={false} 时点遮罩也不关', async () => {
    const onClose = vi.fn();
    openDialog({ onClose, closeOnOverlayClick: false });

    await userEvent.click(overlay());
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('Dialog：初始焦点', () => {
  it('给了 initialFocusRef 就聚焦它（而不是第一个可聚焦元素）', () => {
    function WithInput() {
      const inputRef = useRef<HTMLInputElement>(null);
      return (
        <Harness open title="带输入框" initialFocusRef={inputRef}>
          <button type="button">第一个</button>
          <input ref={inputRef} placeholder="名称" />
        </Harness>
      );
    }
    render(<WithInput />);

    expect(document.activeElement).toBe(screen.getByPlaceholderText('名称'));
  });

  it('没给 initialFocusRef 就聚焦容器内第一个可聚焦元素', () => {
    openDialog();
    const [first] = dialogButtons();
    expect(document.activeElement).toBe(first);
  });

  it('容器里没有可聚焦元素时聚焦容器本身', () => {
    render(
      <Harness open title="空的对话框">
        <p>只有文字</p>
      </Harness>,
    );
    expect(document.activeElement).toBe(screen.getByRole('dialog'));
  });
});

describe('Dialog：焦点归位', () => {
  /**
   * 批次 D3 后半修的那条回退：**触发元素还在 DOM 里，但关闭时已经 disabled**。
   *
   * 真实路径是"点删除 → 确认 → 那一帧里关框与按钮进 loading 是同一批 state 更新"，
   * 等归位的清理函数跑到时按钮已经不可聚焦了。而 `focus()` 对 disabled 元素是
   * **静默 no-op** —— 焦点会掉到 `body` 上，用户接着按 Tab 就从页首重新开始。
   */
  it('★ 关闭时触发按钮已 disabled：焦点交给还能收焦点的祖先，而不是掉到 body', async () => {
    function Page() {
      const [open, setOpen] = useState(false);
      const [busy, setBusy] = useState(false);
      /** 关框与禁用按钮**同一批更新** —— 这正是真实调用点的形状 */
      const closeAndBusy = () => {
        setOpen(false);
        setBusy(true);
      };
      return (
        <>
          {/* tabIndex=-1：这条工具栏就是"还能收焦点的那一层" */}
          <div data-testid="toolbar" tabIndex={-1}>
            <button type="button" disabled={busy} onClick={() => setOpen(true)}>
              删除
            </button>
          </div>
          <Harness open={open} onClose={closeAndBusy} title="确定删除？">
            <button type="button" onClick={closeAndBusy}>
              确认
            </button>
          </Harness>
        </>
      );
    }
    render(<Page />);

    const toolbar = screen.getByTestId('toolbar');
    const trigger = screen.getByRole('button', { name: '删除' });

    trigger.focus(); // 打开前拿着焦点的就是它
    await userEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '确认' }));

    await userEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toBeDisabled(); // 前提成立：触发元素此时确实不可聚焦
    expect(document.activeElement).toBe(toolbar); // 焦点落在能收它的那一层
  });

  it('触发元素及其祖先都不可聚焦时不抛错，焦点也不留在已关闭的框里', async () => {
    function Page() {
      const [open, setOpen] = useState(false);
      const [busy, setBusy] = useState(false);
      const closeAndBusy = () => {
        setOpen(false);
        setBusy(true);
      };
      return (
        <>
          {/* 这一层刻意**不带** tabIndex：整条祖先链都收不了焦点 */}
          <div>
            <button type="button" disabled={busy} onClick={() => setOpen(true)}>
              删除
            </button>
          </div>
          <Harness open={open} onClose={closeAndBusy} title="确定删除？">
            <button type="button" onClick={closeAndBusy}>
              确认
            </button>
          </Harness>
        </>
      );
    }
    render(<Page />);

    const trigger = screen.getByRole('button', { name: '删除' });
    trigger.focus();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('button', { name: '确认' }));

    // 没有落点就不强行 focus —— 焦点由浏览器放在 body 上。这是基座层的边界：
    // 要精确到"被删掉的下一行"得由调用方给锚点，基座不该猜。
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect([document.body, document.documentElement]).toContain(document.activeElement);
  });

  it('★ 关闭后焦点还给打开前的那个元素（用两个真实按钮做触发/归还）', async () => {
    function Page() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            打开对话框
          </button>
          <button type="button">别动我</button>
          <Harness open={open} onClose={() => setOpen(false)} title="测试对话框">
            <button type="button" onClick={() => setOpen(false)}>
              关闭
            </button>
          </Harness>
        </>
      );
    }
    render(<Page />);

    const trigger = screen.getByRole('button', { name: '打开对话框' });
    const bystander = screen.getByRole('button', { name: '别动我' });

    trigger.focus(); // 显式聚焦 = 打开前 document.activeElement 就是它
    await userEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeVisible();
    // 打开后焦点在对话框里（不这样断言的话，"归位"可能落在"从来没离开过"上）
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭' }));

    await userEvent.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
    expect(document.activeElement).not.toBe(bystander); // 归还给**触发**的那一个
  });

  it('关闭后再打开，焦点归位与初始焦点都各自成立（不是一次性副作用）', async () => {
    const { rerender } = render(
      <>
        <button type="button">触发器</button>
        <Harness open={false} title="测试对话框">
          <button type="button">第一个</button>
        </Harness>
      </>,
    );

    const trigger = screen.getByRole('button', { name: '触发器' });
    trigger.focus();

    rerender(
      <>
        <button type="button">触发器</button>
        <Harness open title="测试对话框">
          <button type="button">第一个</button>
        </Harness>
      </>,
    );
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '第一个' }));

    rerender(
      <>
        <button type="button">触发器</button>
        <Harness open={false} title="测试对话框">
          <button type="button">第一个</button>
        </Harness>
      </>,
    );
    expect(document.activeElement).toBe(trigger);
  });

  it('触发元素已经不在 DOM 里时归位不抛错（删除后整行消失是常见形态）', () => {
    const { rerender } = render(
      <>
        <button type="button">触发器</button>
        <Harness open={false} title="测试对话框">
          <button type="button">第一个</button>
        </Harness>
      </>,
    );

    screen.getByRole('button', { name: '触发器' }).focus();

    // 打开对话框，同时把触发元素从 DOM 里拿掉（`onClose` 那条路径上它已 `isConnected === false`）
    rerender(
      <Harness open title="测试对话框">
        <button type="button">第一个</button>
      </Harness>,
    );

    // 归位发生在 effect 清理函数里：对游离节点调 focus() 什么都不做，不该抛错
    expect(() =>
      rerender(
        <Harness open={false} title="测试对话框">
          <button type="button">第一个</button>
        </Harness>,
      ),
    ).not.toThrow();
    // 触发元素已经不在文档里 —— 归位目标落空时焦点留在 body 上，而不是乱跳到别的控件
    expect(screen.queryByRole('button', { name: '触发器' })).toBeNull();
    expect(document.activeElement).toBe(document.body);
  });
});

describe('Dialog：body 滚动锁', () => {
  it('★ 打开时 body 上有滚动锁类，关闭后没有', () => {
    const { rerender } = render(<Harness open title="测试对话框" />);
    expect(document.body.classList.contains(styles.dialogScrollLock)).toBe(true);

    rerender(<Harness open={false} title="测试对话框" />);
    expect(document.body.classList.contains(styles.dialogScrollLock)).toBe(false);
  });

  it('★ 对话框还开着就被直接卸载时也必须解锁（锁留在 body 上整页会再也滚不动）', () => {
    const { unmount } = render(<Harness open title="测试对话框" />);
    expect(document.body.classList.contains(styles.dialogScrollLock)).toBe(true);

    unmount();
    expect(document.body.classList.contains(styles.dialogScrollLock)).toBe(false);
  });

  it('没打开时不加锁（桌面端不该受影响）', () => {
    render(<Harness open={false} title="测试对话框" />);
    expect(document.body.classList.contains(styles.dialogScrollLock)).toBe(false);
  });
});
