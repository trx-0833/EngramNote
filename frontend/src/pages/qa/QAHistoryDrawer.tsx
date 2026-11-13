/**
 * @file 问答历史抽屉（docs/qa-history-plan.md §5.2）
 *
 * 抽出组件的理由：`QA.tsx` 已经 500+ 行，而抽屉有自己的一整套内容
 * （列表 / 空态 / 每项操作 / 清空全部 / 保留期提示）。放进页面只会让
 * "对话渲染"与"历史管理"两件事搅在一起。
 *
 * ## 三条与后端对齐的约束
 *
 * 1. **保留期提示必须在抽屉顶部**（计划 §5.4 第 2 句）：用户只有点开这个抽屉
 *    才会想到"删了是不是就没了"，提示放在这里才有用。
 * 2. **删除是逐条 + 清空全部两个动作**：前者用于"这段不想留"，后者用于
 *    "整个主题都不想留"；两者的确认文案不同，因为后者更重。
 * 3. **数字从接口/配置来**：`30 天`（软删宽限）与保留期来自后端契约说明，
 *    这里用文案占位符注入，不在组件里写死。
 *
 * ## 2026-11-13 独立审计：本文件承接 M2 / m4 / m5 / m6 四条
 *
 * | 编号 | 原来 | 现在 |
 * |---|---|---|
 * | M2 | 点「删除单条」/「清空全部」**点了就发请求**，没有二次确认 | 先 `ConfirmDialog`，确认之后才执行 |
 * | m4 | 删除失败只留一个 unhandled rejection（列表没变，用户以为点漏了） | `catch` → `toast.error`；**catch 里绝不再发一次 DELETE** |
 * | m5 | `clearQaSessions()` 返回的 `{ deleted }` 被直接丢掉 | `toast.info(qa.clearAllDone, { count: deleted })` |
 * | m6 | 只拉一页时列表看起来"就是全部" | `total > shown` 时补一句 `qa.historyTruncated` |
 *
 * ### M2：为什么两个确认框渲染在 `<Dialog>` **外面**
 *
 * `Dialog` 自带焦点陷阱、Esc、遮罩点击与 body 滚动锁（见 `Dialog.tsx` 文件头）。
 * 把 `ConfirmDialog`（它内部也是一个 `Dialog`）塞进抽屉的 `children` 里，就是
 * "陷阱套陷阱"：Tab 在内层循环、Esc 先被内层吃掉、内层关闭时外层的滚动锁
 * 对不上账。这三样都**不报错**，只表现为"偶尔 Tab 出不去"这类无主 bug。
 * 所以两个 `<ConfirmDialog>` 与抽屉的 `<Dialog>` **平级**渲染，各管各的层。
 *
 * ### m4：为什么"执行段"是独立的 async 函数，且自带 `catch`
 *
 * `ConfirmDialog` 点确认的行为是**先关框、再执行**（见它的 `handleConfirm`）。
 * 也就是说执行段拿不到任何 `await` 上下文 —— 漏一个 `catch`，失败就只剩一个
 * unhandled rejection：`finally` 里的 `busyId` 会复位、列表却没变，用户看到的
 * 是"点了没反应，再点一次还是没反应"。`runDelete` / `runClearAll` 因此各自
 * 把 `try/catch/finally` 写全。
 *
 * ⚠️ `catch` 里**只提示、不重试**。破坏性请求重发的后果是"第一次其实已经成功、
 * 第二次拿到 404"，用户会看到"对话不存在"而列表仍旧是旧的。接口层
 * （`api/qaHistory.ts` 的 `deleteQaSession`）也一并改成只读一次响应，
 * 这里保证不制造第二次调用。
 *
 * ### m5：为什么清空的返回值必须显示出来
 *
 * 清空后列表变空，"本来就空"与"清掉了 12 个"在界面上是同一个画面 ——
 * `{ deleted }` 是那次操作**唯一**的反馈来源，丢掉它用户就永远不知道
 * 刚才到底删成了没有。
 *
 * ### m6：`total` 为什么是 prop 而不是 `sessions.length`
 *
 * `sessions` 只是上层拉的**一页**（上层按审计 M1 用 `page_size=100` 请求，
 * 并带上 `surface: 'global'`）；`total` 是服务端返回的实际条数。两者相等时
 * 不必多话，`total > shown` 才说明"还有更早的对话没显示"—— 不说这句，
 * 用户会以为历史被吞了。`total` 由上层从列表响应的 `total` 字段透传，
 * 本组件不去猜，也不为此多发一次请求。
 */
import { useMemo, useState } from 'react';

import { clearQaSessions, deleteQaSession, type QASessionSummary } from '../../api/qaHistory';
import ConfirmDialog from '../../components/ConfirmDialog';
import Dialog from '../../components/Dialog';
import Icon from '../../components/Icon';
import { useToast } from '../../components/Toast';
import { useTranslation } from '../../i18n';
import { formatDateTime } from '../../utils/datetime';
import styles from './QAHistoryDrawer.module.css';

export interface QAHistoryDrawerProps {
  open: boolean;
  onClose: () => void;
  /** 当前页的会话（上层按 `page_size=100` 拉取，见文件头 m6 段） */
  sessions: QASessionSummary[];
  /**
   * 服务端总数（列表响应的 `total`），**不是** `sessions.length`
   *
   * 两个数字不等 → 抽屉要说明"只显示了最近的 N 个"（审计 m6）；
   * 清空确认框里的 `{count}` 也用这个数字，因为被删掉的是全部而不只是这一页。
   */
  total: number;
  loading: boolean;
  error?: string;
  /** 当前正在看的会话（高亮） */
  activeSessionId?: string;
  /** 点一条：载入该会话并接着追问 */
  onOpenSession: (sessionId: string) => void;
  /** 删除/清空之后让上层刷新列表 */
  onChanged: () => void;
  /** 保留期（天）与备份周数：由调用方从后端配置读，写死会与配置漂移 */
  retentionDays: number;
  backupWeeks: number;
}

export default function QAHistoryDrawer({
  open,
  onClose,
  sessions,
  total,
  loading,
  error,
  activeSessionId,
  onOpenSession,
  onChanged,
  retentionDays,
  backupWeeks,
}: QAHistoryDrawerProps) {
  const { t } = useTranslation();
  // 审计 m4/m5：两条失败路径（删除、清空）与一次成功反馈（清掉几个）都要
  // 说出来。此前"失败让上层接管"的做法在抽屉里等于什么都不显示。
  const toast = useToast();

  /** 正在删的那一条（禁用它的按钮，防连点） */
  const [busyId, setBusyId] = useState<string | null>(null);
  /**
   * 审计 M2：待确认的那一条。`null` = 没有待确认的删除
   *
   * 点删除按钮**只改这个 state**，请求由确认框的 `onConfirm` 触发 ——
   * 这就是 M2 的全部改动（"先确认再执行"），不是把 `window.confirm` 换个皮。
   */
  const [pendingDelete, setPendingDelete] = useState<QASessionSummary | null>(null);
  const [clearing, setClearing] = useState(false);
  /** 审计 M2：`true` 时清空确认框打开 */
  const [confirmClearAll, setConfirmClearAll] = useState(false);

  /** 当前页实际渲染了几条（`qa.historyTruncated` 里的 {shown}，见审计 m6） */
  const shown = sessions.length;

  /**
   * 真正执行单条删除（**只**由确认框的「删除」触发）
   *
   * `onConfirm` 不是 `await` 上下文（`ConfirmDialog` 先关框再执行），
   * 所以异常必须在这里自己接住 —— 否则就是审计 m4 的 unhandled rejection。
   */
  async function runDelete(session: QASessionSummary) {
    setBusyId(session.id);
    try {
      await deleteQaSession(session.id);
      toast.info(t('qa.deleted'));
      onChanged();
    } catch (err) {
      // m4：提示失败，**不重试**。重发的危害见文件头。
      toast.error(err instanceof Error ? err.message : t('shared.deleteFailed'));
    } finally {
      // 无论成败都要复位：失败时按钮卡在禁用态，用户会以为整个抽屉坏了。
      setBusyId(null);
      setPendingDelete(null);
    }
  }

  /** 真正执行清空（**只**由确认框的「清空全部」触发） */
  async function runClearAll() {
    setClearing(true);
    try {
      const { deleted } = await clearQaSessions();
      // m5：这个数字是清空动作唯一的反馈，不能丢。
      toast.info(t('qa.clearAllDone', { count: deleted }));
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('shared.deleteFailed'));
    } finally {
      setClearing(false);
      setConfirmClearAll(false);
    }
  }

  const body = useMemo(() => {
    if (loading) return <p className={styles.hint}>{t('shared.loading')}</p>;
    if (error) return <p className={styles.error}>{error}</p>;
    if (shown === 0) return <p className={styles.hint}>{t('qa.historyEmpty')}</p>;
    return (
      <ul className={styles.list}>
        {sessions.map((session) => {
          const isActive = session.id === activeSessionId;
          return (
            <li key={session.id} className={styles.item}>
              <button
                type="button"
                className={`${styles.itemMain} ${isActive ? styles.itemActive : ''}`}
                onClick={() => onOpenSession(session.id)}
                aria-current={isActive ? 'true' : undefined}
              >
                <span className={styles.itemTitle}>{session.title || t('qa.historyUntitled')}</span>
                <span className={styles.itemMeta}>
                  {formatDateTime(session.updated_at)} · {session.turn_count}
                </span>
              </button>
              <button
                type="button"
                className={styles.itemDelete}
                // 审计 M2：这里**只**挂起待确认对象，请求在 `runDelete` 里发
                onClick={() => setPendingDelete(session)}
                disabled={busyId === session.id}
                aria-label={t('qa.deleteSession')}
                title={t('qa.deleteSession')}
              >
                <Icon name="delete" size={16} />
              </button>
            </li>
          );
        })}
      </ul>
    );
    // `setPendingDelete`（`useState` 的 setter）与 `onOpenSession` 是每次渲染
    // 新建/稳定但行为不随渲染变化的闭包；上面列出的才是真正影响渲染结果的值。
    // 把 callback 放进依赖只会让 memo 每次都失效，所以这里显式豁免。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, error, shown, sessions, activeSessionId, busyId, t]);

  return (
    <>
      <Dialog open={open} onClose={onClose} title={t('qa.historyTitle')}>
        {/* 保留期提示（计划 §5.4 第 2 句）：数字来自配置，不写死。
            审计改造后仍在抽屉**顶部** —— 这句是"删了会怎样"的唯一解释。 */}
        <p className={styles.note}>
          {t('qa.retentionHint', { days: retentionDays, weeks: backupWeeks })}
        </p>

        {body}

        {/* 审计 m6：只拉一页时把"还有更早的"说清楚，否则用户以为历史被吞了 */}
        {total > shown && (
          <p className={styles.note}>{t('qa.historyTruncated', { total, shown })}</p>
        )}

        {total > 0 && (
          <div className={styles.footer}>
            <button
              type="button"
              className="btn btn-danger-outline"
              // 审计 M2：同上，只打开确认框
              onClick={() => setConfirmClearAll(true)}
              disabled={clearing}
            >
              {t('qa.clearAll')}
            </button>
          </div>
        )}
      </Dialog>

      {/*
        审计 M2：两个确认框与抽屉平级（**不**嵌在 `<Dialog>` 里），理由见文件头。
        确认框点「确认」会先关自己（走 `onCancel`）再跑 `onConfirm`，
        所以执行段必须像上面那样自带 catch。
      */}
      <ConfirmDialog
        open={pendingDelete !== null}
        title={t('qa.deleteConfirm')}
        // 标题缺失的会话也要能确认：显示「未命名对话」，与列表行一致
        message={pendingDelete ? pendingDelete.title || t('qa.historyUntitled') : undefined}
        confirmText={t('qa.deleteSession')}
        danger
        onConfirm={() => {
          if (pendingDelete) void runDelete(pendingDelete);
        }}
        onCancel={() => setPendingDelete(null)}
      />

      <ConfirmDialog
        open={confirmClearAll}
        // {count} 用服务端总数：被移出历史的是全部，不只是当前这一页
        title={t('qa.clearAllConfirm', { count: total })}
        confirmText={t('qa.clearAll')}
        danger
        onConfirm={() => {
          void runClearAll();
        }}
        onCancel={() => setConfirmClearAll(false)}
      />
    </>
  );
}
