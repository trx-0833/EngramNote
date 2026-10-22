/**
 * @file 回收站页面
 * @description 展示已移入回收站的笔记列表，支持：
 * 1. 查看每项笔记的附属统计（卡片/题目/批注/版本/双向链接数）
 * 2. 恢复笔记（原子包整体还原，同名冲突自动改名并提示）
 * 3. 彻底删除单条笔记（PurgeNoteDialog，悬挂引用策略 + 可选提升核心卡片）
 * 4. 清空回收站（物理删除全部，二次确认）
 *
 * 批次 E8（visual-refactor-plan §6）的两处：
 *   ① **表格化**：一条笔记 = 一行（真 `<table>` + `<th scope="col">`），
 *      原来是一张张 `.card`，横向没法比"哪条删得最久"；
 *   ② **描边危险按钮**：入口（「清空回收站」/「彻底删除」）改描边，
 *      实底红只留给确认框里那一下（§C2 第 14 行）。
 * 承载结构与样式见 `Trash.module.css`。
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getTrashedNotes,
  restoreNote,
  purgeNote,
  purgeAllTrash,
  type TrashNoteItem,
} from '../api/client';
import { PurgeNoteDialog } from '../components/DeleteNoteDialog';
// 对话框基座（visual-refactor-plan 批次 D2）：清空确认弹窗的遮罩 / 面板改由它渲染
import Dialog from '../components/Dialog';
import Icon from '../components/Icon';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import { useLabels } from '../utils/labels';
import { formatDateTime } from '../utils/datetime';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 副标题 + 清空按钮）整块交给组件 —— 副标题也一并进去，
// 这样按钮仍然是对着"标题 + 副标题"整块垂直居中（与迁移前逐像素相同）
import PageHeader from '../components/PageHeader';
import { useToast } from '../components/Toast';
// 回收站私有样式（visual-refactor-plan 批次 E8）：表格化的承载结构
// —— 类名进模块后会被哈希，留在全局样式表里的选择器再也选不中它（css-convention.md §3 雷区 2）
import styles from './Trash.module.css';
import { useTranslation } from '../i18n';

/**
 * 回收站查询的 key（迁移 5.2 / C1）
 *
 * 导出成常量而不是在几处写字面量：写操作后要失效的就是它，
 * 字面量写两遍迟早会不一致（而那种不一致**不会报错**，
 * 只会让"删完列表不刷新"这种问题偶尔出现）。
 */
export const TRASH_QUERY_KEY = ['trash'] as const;

export default function Trash() {
  const { t } = useTranslation();
  const { sourceTypeLabels } = useLabels();
  const toast = useToast();
  const queryClient = useQueryClient();
  /** 操作进行中的笔记 ID（恢复/彻底删除），用于按钮禁用 */
  const [operatingId, setOperatingId] = useState<string | null>(null);
  /** 待彻底删除的笔记（打开 PurgeNoteDialog） */
  const [noteToPurge, setNoteToPurge] = useState<TrashNoteItem | null>(null);
  /** 清空回收站确认弹窗 */
  const [showPurgeAll, setShowPurgeAll] = useState(false);

  /**
   * 回收站列表（迁移 5.2 / C1，2026-10-22）
   *
   * ## 三个本地状态去哪了
   *
   * 改动前这里是 `items` / `loading` / `error` 三个 `useState` 加一个
   * `fetchTrash()` 与 `useEffect`。现在它们分别由
   * `data` / `isLoading` / `error` 承担 —— **本地那三个状态整个删掉**，
   * 不留两套（计划 §4 的风险栏点了这条："页面里既留旧 state 又用 query"）。
   *
   * ## 三种界面与文案**逐字不变**（B1 的验收判据）
   *
   * - 加载中：`isLoading`（首次加载，无缓存）→ 仍是 `<LoadingSpinner />`；
   * - 失败：`error` → 仍是 `<ErrorDisplay message={...} onRetry={refetch} />`，
   *   文案仍是 `err.message` 或 `t('shared.loadFailed')`；
   * - 空态：`items.length === 0` → 仍是 `<EmptyState>`。
   *
   * ⚠️ 用 `isLoading`（= `isPending && isFetching`）而不是 `isPending`：
   * 后者在"有缓存但正在后台重取"时也是 true，那会让页面在已有数据时
   * 闪一下 spinner —— 那是对现状的**行为改动**，B1 不接受。
   */
  const {
    data: trashData,
    isLoading: loading,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: TRASH_QUERY_KEY,
    queryFn: ({ signal }) => getTrashedNotes({ signal }),
  });

  /**
   * ⚠️ `data` 是**整个响应**（`{ items: [...] }`），不是数组本身。
   *
   * 第一版写成 `data: items = []`，`tsc` 立刻在 `.length` / `.map` 上报错 4 处 ——
   * 改动前那句是 `setItems(res.items)`，`items` 是**取出来之后**的名字。
   * 这类错在 JS 里会一路跑到运行时才炸，这次靠类型层当场拦住。
   */
  const items = trashData?.items ?? [];

  const error = queryError
    ? queryError instanceof Error
      ? queryError.message
      : t('shared.loadFailed')
    : '';

  /**
   * 从缓存里移除一条（恢复/彻底删除之后）
   *
   * ## 为什么用 `setQueryData` 而不是 `invalidateQueries`
   *
   * 这两个动作的结果是**确定的**（那条笔记确实不在回收站了），
   * 本地移除与服务器的答案一致，所以不需要再打一次请求去问。
   * 现状也是本地 `setItems(filter)` —— 这样请求次数与现状**逐次相同**，
   * 不会引入 B1 不该有的行为变化。
   *
   * 同时失效 `['notes']`：恢复出去的笔记要出现在笔记列表里。
   * 不失效的话，`staleTime: 30_000` 会让用户"恢复完切到笔记列表却看不到它"
   * （计划 §4 风险栏第 2 条："缓存掩盖了刷新"）。
   */
  function removeFromCache(noteId: string) {
    queryClient.setQueryData<TrashNoteItem[]>(TRASH_QUERY_KEY, (prev) =>
      (prev ?? []).filter((it) => it.note.id !== noteId),
    );
  }

  /**
   * 恢复笔记
   * 原子包整体还原；若原位置已有同名新文件，后端自动加序号后缀，
   * 此处以 alert 提示 renamed_to。
   */
  async function handleRestore(item: TrashNoteItem) {
    setOperatingId(item.note.id);
    try {
      const res = await restoreNote(item.note.id);
      if (res.renamed_to) {
        toast.info(t('trash.restoredRenamed', { name: res.renamed_to }));
      }
      removeFromCache(item.note.id);
      // 恢复出去的笔记要出现在笔记列表里（见 removeFromCache 的说明）
      await queryClient.invalidateQueries({ queryKey: ['notes'] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('trash.restoreFailed'));
    } finally {
      setOperatingId(null);
    }
  }

  /** 确认彻底删除单条笔记 */
  async function confirmPurge(promoteKeyCards: boolean) {
    if (!noteToPurge) return;
    const targetId = noteToPurge.note.id;
    setOperatingId(targetId);
    try {
      await purgeNote(targetId, promoteKeyCards);
      removeFromCache(targetId);
      setNoteToPurge(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('trash.purgeFailed'));
    } finally {
      setOperatingId(null);
    }
  }

  /** 确认清空回收站 */
  async function confirmPurgeAll() {
    try {
      const res = await purgeAllTrash();
      setShowPurgeAll(false);
      // 清空是"整份列表归零"，直接写缓存（与逐条删除同一口径：结果确定，不必再问）
      queryClient.setQueryData<TrashNoteItem[]>(TRASH_QUERY_KEY, []);
      if (res.failed > 0) {
        toast.success(t('trash.purgeResult', { purged: res.purged, failed: res.failed }));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('trash.emptyAllFailed'));
    }
  }

  return (
    <div className="page-enter">
      {/* 头部：标题 + 副标题 + 清空按钮（批次 C1：统一进 <PageHeader>；
          窄屏换行原由全局 `.page-header-row` 负责，现随组件进模块） */}
      <PageHeader
        title={t('trash.title')}
        subtitle={t('trash.subtitle')}
        actions={
          // `? :` 而不是 `&&`：回收站为空时动作区**必须真的不存在**。
          // 传 `false` 会渲染出一个 0 宽的空容器，那在窄屏换行后会白占一行
          // （`row-gap` 照算），而那正是"回收站本来就没有清空按钮"的场景。
          items.length > 0 ? (
            /* 批次 E8：原来是 `.btn` + 内联 `background: var(--color-error); color: '#fff'`
               —— 实底红 + 一个硬编码的 `#fff`。按 §C2 第 14 行改成**描边危险按钮**：
               它打开的只是一个确认框，真正的不可恢复发生在框里那一下
               （对话框里的确认按钮仍是实底 `.btn-danger`）。 */
            <button className="btn btn-danger-outline" onClick={() => setShowPurgeAll(true)}>
              {/* 批次 B3：「删除」类按钮此前全是纯文字 —— 补 `delete` 图标 */}
              <Icon name="delete" size={16} />
              {t('trash.emptyAll')}
            </button>
          ) : undefined
        }
      />

      {error && <ErrorDisplay message={error} onRetry={() => refetch()} />}

      {loading ? (
        <LoadingSpinner />
      ) : items.length === 0 ? (
        <EmptyState message={t('trash.empty')} description={t('trash.emptyHint')} />
      ) : (
        /* 表格化（批次 E8）：一条已删除的笔记 = 一行。
           ⚠️ 真 `<table>`：`<th scope="col">` 是读屏用户唯一能拿到的列语义，
           不要为了窄屏把它换成 div + grid（那会把 table/row/cell 三个角色
           从无障碍树里删掉，而且 axe 报不出来）。
           窄屏由 `.trashTableScroll` 横向滚动兜住，见 Trash.module.css。 */
        <div className={styles.trashTableScroll}>
          <table className={styles.trashTable}>
            <thead>
              <tr>
                <th scope="col">{t('trash.colNote')}</th>
                <th scope="col">{t('trash.colSourceAndDeletedAt')}</th>
                <th scope="col">{t('trash.colContains')}</th>
                <th scope="col">{t('trash.colActions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.note.id}>
                  <td className={styles.trashTitleCell}>
                    {/* `h2` 而不是 `h3`：这一页的大纲是 h1「回收站」→ 每行笔记，
                        中间不存在任何区块级标题 —— 写成 h3 就是 h1 → h3 跳级
                        （axe 的 heading-order）。表格单元格里放标题是合法的
                        （`<td>` 的内容模型是 flow content），而 `<th>` **不允许**
                        有标题后代，所以标题留在 `<td>` 里、列语义由 thead 的 `<th>` 承担。 */}
                    <h2 className={styles.trashTitle}>{item.note.title}</h2>
                  </td>
                  <td className={styles.trashMeta}>
                    {sourceTypeLabels[item.note.source_type] || item.note.source_type}
                    {' · '}
                    {t('trash.deletedAt', {
                      time: item.note.trashed_at ? formatDateTime(item.note.trashed_at) : '—',
                    })}
                  </td>
                  {/* 附属统计：恢复时可还原的内容 */}
                  <td>
                    <div className={styles.trashChips}>
                      {[
                        t('trash.countCards', { count: item.card_count }),
                        t('trash.countQuizzes', { count: item.quiz_count }),
                        t('trash.countAnnotations', { count: item.annotation_count }),
                        t('trash.countVersions', { count: item.version_count }),
                        t('trash.countLinks', { count: item.link_count }),
                      ].map((text) => (
                        <span key={text} className={styles.trashChip}>
                          {text}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <div className={styles.trashActions}>
                      <button
                        className="btn btn-primary"
                        disabled={operatingId === item.note.id}
                        onClick={() => handleRestore(item)}
                      >
                        {t('trash.restore')}
                      </button>
                      {/* 描边危险（批次 E8）：它打开的是确认框，不是最终删除 */}
                      <button
                        className="btn btn-danger-outline"
                        disabled={operatingId === item.note.id}
                        onClick={() => setNoteToPurge(item)}
                      >
                        <Icon name="delete" size={16} />
                        {t('trash.purge')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 彻底删除单条确认弹窗 */}
      {noteToPurge && (
        <PurgeNoteDialog
          note={noteToPurge.note}
          onClose={() => setNoteToPurge(null)}
          onConfirm={confirmPurge}
        />
      )}

      {/* 清空回收站确认弹窗（批次 D2：遮罩 / 面板 / 层级 / 圆角 / 阴影 / 内边距
          全部交给 `<Dialog>` 基座；原来这里是手写内联遮罩 + `className="card"` 面板。
          标题原来是红字 `<h3>` —— D2 迁移时被基座的统一标题色抹掉，本批由
          `titleTone="danger"` 补回，与正文的「不可恢复」、红底确认按钮一起承载危险语义）。
          ⚠️ 不再需要 `showPurgeAll &&` 包一层：`open={false}` 时基座连遮罩都不渲染。 */}
      <Dialog
        open={showPurgeAll}
        onClose={() => setShowPurgeAll(false)}
        title={t('trash.emptyAll')}
        titleTone="danger"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setShowPurgeAll(false)}>
              {t('trash.cancel')}
            </button>
            {/* 最终确认（批次 E8）：这里是**真的**不可恢复那一下，
                所以用实底 `.btn-danger` —— 描边只留给"打开确认框"的入口，
                内联的 `background: var(--color-error); color: '#fff'` 随之去掉
                （既与 `.btn-danger` 逐字同值，又少两个硬编码色值）。 */}
            <button className="btn btn-danger" onClick={confirmPurgeAll}>
              {t('trash.emptyAction')}
            </button>
          </>
        }
      >
        <p style={{ marginBottom: 'var(--space-md)' }}>
          {t('trash.emptyAllConfirm', { count: items.length })}
          <strong>{t('shared.irreversible')}</strong>。
        </p>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          {t('trash.emptyAllImpact')}
        </p>
      </Dialog>
    </div>
  );
}
