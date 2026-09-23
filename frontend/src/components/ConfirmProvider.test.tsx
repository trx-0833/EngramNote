/**
 * @file 确认框 Promise 化封装的测试（visual-refactor-plan 批次 D3 后半）
 *
 * ## 这个文件最重要的是哪一条
 *
 * 「点确认拿到 `true`」那一条。`ConfirmDialog` 点确认时是**先调 `onCancel()`
 * 关框、再调 `onConfirm()`**（见它的文件头：不先关框，用户会被锁在一个已点完的
 * 框里）。如果 `ConfirmProvider` 把 Promise 的结账挂在 `onCancel` 上，
 * 点确认会先把 Promise 结成一个 `false` —— 调用方于是**永远走取消分支**，
 * 而框看起来是正常关掉的：用户点了"确认删除"，什么都没发生，也没有任何报错。
 *
 * 这是本批唯一一处会**静默吃掉用户操作**的地方，所以它有一条专门的用例，
 * 而不是只靠"取消能用"来侧面覆盖。
 *
 * ## 为什么用真实的 ConfirmDialog 而不是替身
 *
 * 这里要验的正是"两个组件拼起来之后的时序"（Provider 的 resolver 与
 * ConfirmDialog 的 `onConfirmClose`/`onConfirm` 顺序）。把 `ConfirmDialog`
 * 换成替身就等于把被测对象换掉了 —— 替身会照 Provider 期望的顺序调用，
 * 于是那条 bug 永远测不出来。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import ConfirmProvider, { useConfirm } from './ConfirmProvider';

/** 一个最小的消费方：点按钮发起确认，把结果交给外部的间谍 */
function Harness({
  onResult,
  danger = false,
}: {
  onResult: (ok: boolean) => void;
  danger?: boolean;
}) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={() => {
        void confirm({ title: '确定删除？', message: '删了就没了', danger }).then(onResult);
      }}
    >
      发起确认
    </button>
  );
}

function renderHarness(danger = false) {
  const onResult = vi.fn();
  render(
    <ConfirmProvider>
      <Harness onResult={onResult} danger={danger} />
    </ConfirmProvider>,
  );
  return onResult;
}

/** 点「发起确认」并等确认框出现 */
async function openConfirm() {
  await userEvent.click(screen.getByRole('button', { name: '发起确认' }));
  return screen.findByRole('dialog');
}

describe('ConfirmProvider / useConfirm', () => {
  it('★ 点「确认」拿到的必须是 true（不是取消）', async () => {
    const onResult = renderHarness();
    const dialog = await openConfirm();

    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
    // 反面对照：不能同时结出一个 false（结两次会让这条挂掉）
    expect(onResult).toHaveBeenCalledTimes(1);
  });

  it('点「取消」拿到 false', async () => {
    const onResult = renderHarness();
    const dialog = await openConfirm();

    await userEvent.click(within(dialog).getByRole('button', { name: '取消' }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
    expect(onResult).toHaveBeenCalledTimes(1);
  });

  it('按 Esc 拿到 false（Dialog 的关闭路径也要能结账）', async () => {
    const onResult = renderHarness();
    await openConfirm();

    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it('点遮罩拿到 false', async () => {
    const onResult = renderHarness();
    const dialog = await openConfirm();

    // 遮罩是面板的父节点；点它 = 点面板外面
    await userEvent.click(dialog.parentElement as HTMLElement);

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it('请求的参数原样落到框里：标题与说明', async () => {
    renderHarness();
    const dialog = await openConfirm();

    expect(within(dialog).getByText('确定删除？')).toBeInTheDocument();
    expect(within(dialog).getByText('删了就没了')).toBeInTheDocument();
  });

  it('danger 是真的开关：给了才红，不给就不是红的', async () => {
    // 给了 danger
    const { unmount } = render(
      <ConfirmProvider>
        <Harness onResult={vi.fn()} danger />
      </ConfirmProvider>,
    );
    let dialog = await openConfirm();
    expect(within(dialog).getByRole('button', { name: '确认' })).toHaveClass('btn-danger');
    unmount();

    // 不给 danger：同样一枚确认按钮，不该带危险色
    renderHarness(false);
    dialog = await openConfirm();
    expect(within(dialog).getByRole('button', { name: '确认' })).not.toHaveClass('btn-danger');
  });

  it('★ 同时只允许一个确认框：第二个请求到来时，前一个按取消结账（不能让它的 await 永久悬挂）', async () => {
    const first = vi.fn();
    const second = vi.fn();

    function TwoRequests() {
      const confirm = useConfirm();
      return (
        <>
          <button
            type="button"
            onClick={() => {
              void confirm({ title: '第一个' }).then(first);
            }}
          >
            第一个请求
          </button>
          <button
            type="button"
            onClick={() => {
              void confirm({ title: '第二个' }).then(second);
            }}
          >
            第二个请求
          </button>
        </>
      );
    }

    render(
      <ConfirmProvider>
        <TwoRequests />
      </ConfirmProvider>,
    );

    await userEvent.click(screen.getByRole('button', { name: '第一个请求' }));
    await screen.findByText('第一个');

    await userEvent.click(screen.getByRole('button', { name: '第二个请求' }));

    // 前一个被结掉（按取消），新的顶上来
    await waitFor(() => expect(first).toHaveBeenCalledWith(false));
    expect(await screen.findByText('第二个')).toBeInTheDocument();
    expect(second).not.toHaveBeenCalled();

    // 新的那个还能正常确认
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }));
    await waitFor(() => expect(second).toHaveBeenCalledWith(true));
  });

  it('Provider 外调用 useConfirm 会抛错，而不是悄悄返回一个永远 false 的实现', () => {
    // 静默降级的后果是"用户点了确认但什么都没发生"，且查不出原因 ——
    // 宁可在这里响亮地失败
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Harness onResult={vi.fn()} />)).toThrow(/ConfirmProvider/);
    consoleError.mockRestore();
  });
});
