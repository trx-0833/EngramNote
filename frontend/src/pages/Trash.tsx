/**
 * @file 回收站页面
 * @description 展示已移入回收站的笔记列表，支持：
 * 1. 查看每项笔记的附属统计（卡片/题目/批注/版本/双向链接数）
 * 2. 恢复笔记（原子包整体还原，同名冲突自动改名并提示）
 * 3. 彻底删除单条笔记（PurgeNoteDialog，悬挂引用策略 + 可选提升核心卡片）
 * 4. 清空回收站（物理删除全部，二次确认）
 */
import { useEffect, useState } from 'react';
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
import { sourceTypeLabels } from '../utils/labels';
import { formatDateTime } from '../utils/datetime';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 副标题 + 清空按钮）整块交给组件 —— 副标题也一并进去，
// 这样按钮仍然是对着"标题 + 副标题"整块垂直居中（与迁移前逐像素相同）
import PageHeader from '../components/PageHeader';
import { useToast } from '../components/Toast';

export default function Trash() {
  const toast = useToast();
  /** 回收站列表 */
  const [items, setItems] = useState<TrashNoteItem[]>([]);
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true);
  /** 错误信息 */
  const [error, setError] = useState('');
  /** 操作进行中的笔记 ID（恢复/彻底删除），用于按钮禁用 */
  const [operatingId, setOperatingId] = useState<string | null>(null);
  /** 待彻底删除的笔记（打开 PurgeNoteDialog） */
  const [noteToPurge, setNoteToPurge] = useState<TrashNoteItem | null>(null);
  /** 清空回收站确认弹窗 */
  const [showPurgeAll, setShowPurgeAll] = useState(false);

  /** 加载回收站列表 */
  async function fetchTrash() {
    setLoading(true);
    setError('');
    try {
      const res = await getTrashedNotes();
      setItems(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchTrash();
  }, []);

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
        toast.info(`原位置已存在同名文件，恢复后已自动重命名为「${res.renamed_to}」`);
      }
      setItems((prev) => prev.filter((it) => it.note.id !== item.note.id));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '恢复失败');
    } finally {
      setOperatingId(null);
    }
  }

  /** 确认彻底删除单条笔记 */
  async function confirmPurge(promoteKeyCards: boolean) {
    if (!noteToPurge) return;
    setOperatingId(noteToPurge.note.id);
    try {
      await purgeNote(noteToPurge.note.id, promoteKeyCards);
      setItems((prev) => prev.filter((it) => it.note.id !== noteToPurge.note.id));
      setNoteToPurge(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '彻底删除失败');
    } finally {
      setOperatingId(null);
    }
  }

  /** 确认清空回收站 */
  async function confirmPurgeAll() {
    try {
      const res = await purgeAllTrash();
      setShowPurgeAll(false);
      setItems([]);
      if (res.failed > 0) {
        toast.success(`已彻底删除 ${res.purged} 条，${res.failed} 条删除失败，请重试`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '清空回收站失败');
    }
  }

  return (
    <div className="page-enter">
      {/* 头部：标题 + 副标题 + 清空按钮（批次 C1：统一进 <PageHeader>；
          窄屏换行原由全局 `.page-header-row` 负责，现随组件进模块） */}
      <PageHeader
        title="回收站"
        subtitle="笔记及其全部内容（卡片、题目、复习记录、关系）作为整体保存，可随时整体恢复"
        actions={
          // `? :` 而不是 `&&`：回收站为空时动作区**必须真的不存在**。
          // 传 `false` 会渲染出一个 0 宽的空容器，那在窄屏换行后会白占一行
          // （`row-gap` 照算），而那正是"回收站本来就没有清空按钮"的场景。
          items.length > 0 ? (
            <button
              className="btn"
              style={{ background: 'var(--color-error)', color: '#fff' }}
              onClick={() => setShowPurgeAll(true)}
            >
              {/* 批次 B3：「删除」类按钮此前全是纯文字 —— 补 `delete` 图标 */}
              <Icon name="delete" size={16} />
              清空回收站
            </button>
          ) : undefined
        }
      />

      {error && <ErrorDisplay message={error} onRetry={fetchTrash} />}

      {loading ? (
        <LoadingSpinner />
      ) : items.length === 0 ? (
        <EmptyState message="回收站是空的" description="被删除的笔记会在这里保留，随时可以恢复" />
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {items.map((item) => (
            <article key={item.note.id} className="card" style={{ padding: 'var(--space-md)' }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  gap: 'var(--space-md)',
                }}
              >
                {/* 左侧：标题 + 元信息 + 附属统计 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  {/* `h2` 而不是 `h3`：这一页的大纲是 h1「回收站」→ 每张卡片，
                      中间不存在任何区块级标题 —— 写成 h3 就是 h1 → h3 跳级
                      （axe 的 heading-order）。字号与字重都显式钉着
                      （`fontSize: '1rem'` / `fontWeight: 600`），所以改级别
                      **一个像素都没动**，做法与已修的 F-18（笔记列表卡片
                      h3→h2）逐字相同。 */}
                  <h2
                    style={{
                      fontSize: '1rem',
                      fontWeight: 600,
                      marginBottom: 'var(--space-xs)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {item.note.title}
                  </h2>
                  <div
                    style={{
                      fontSize: '0.8125rem',
                      color: 'var(--color-text-secondary)',
                      marginBottom: 'var(--space-sm)',
                    }}
                  >
                    {sourceTypeLabels[item.note.source_type] || item.note.source_type}
                    {' · '}
                    删除于 {item.note.trashed_at ? formatDateTime(item.note.trashed_at) : '—'}
                  </div>
                  {/* 附属统计：恢复时可还原的内容 */}
                  <div
                    style={{
                      display: 'flex',
                      gap: 'var(--space-sm)',
                      flexWrap: 'wrap',
                      fontSize: '0.75rem',
                    }}
                  >
                    {[
                      `${item.card_count} 张卡片`,
                      `${item.quiz_count} 道题目`,
                      `${item.annotation_count} 条批注`,
                      `${item.version_count} 个版本`,
                      `${item.link_count} 个双向链接`,
                    ].map((text) => (
                      <span
                        key={text}
                        style={{
                          padding: '2px 8px',
                          background: 'var(--color-primary-light)',
                          borderRadius: 'var(--radius-sm)',
                          color: 'var(--color-text-secondary)',
                        }}
                      >
                        {text}
                      </span>
                    ))}
                  </div>
                </div>
                {/* 右侧：操作按钮 */}
                <div style={{ display: 'flex', gap: 'var(--space-sm)', flexShrink: 0 }}>
                  <button
                    className="btn btn-primary"
                    disabled={operatingId === item.note.id}
                    onClick={() => handleRestore(item)}
                  >
                    恢复
                  </button>
                  <button
                    className="btn btn-danger"
                    disabled={operatingId === item.note.id}
                    onClick={() => setNoteToPurge(item)}
                  >
                    <Icon name="delete" size={16} />
                    彻底删除
                  </button>
                </div>
              </div>
            </article>
          ))}
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
          全部交给 `<Dialog>` 基座；原来这里是手写内联遮罩 + `className="card"` 面板，
          标题还是红字 `<h3>` —— 基座的 `.dialogTitle` 统一标题色，危险语义由正文的
          「不可恢复」与红底确认按钮承担）。
          ⚠️ 不再需要 `showPurgeAll &&` 包一层：`open={false}` 时基座连遮罩都不渲染。 */}
      <Dialog
        open={showPurgeAll}
        onClose={() => setShowPurgeAll(false)}
        title="清空回收站"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setShowPurgeAll(false)}>
              取消
            </button>
            <button
              className="btn"
              style={{ background: 'var(--color-error)', color: '#fff' }}
              onClick={confirmPurgeAll}
            >
              清空
            </button>
          </>
        }
      >
        <p style={{ marginBottom: 'var(--space-md)' }}>
          确定彻底删除回收站中的全部 {items.length} 条笔记吗？此操作<strong>不可恢复</strong>。
        </p>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          其他笔记对这些笔记的引用将以「[已删除的笔记]」占位符保留，不会影响其他笔记的内容。
        </p>
      </Dialog>
    </div>
  );
}
