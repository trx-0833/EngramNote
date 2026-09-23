/**
 * @file 回收站相关弹窗组件
 * @description 提供两个弹窗：
 * 1. DeleteNoteDialog —— 移入回收站确认弹窗：展示笔记关联统计
 *    （卡片数 / 核心卡片数 / 双向链接数），说明"关联暂不可见但可随时恢复"。
 * 2. PurgeNoteDialog —— 彻底删除确认弹窗：警示不可恢复，说明悬挂引用
 *    策略，高级选项支持"将核心卡片提升为独立节点"。
 */
import { useEffect, useState } from 'react';
import { getNoteTrashInfo, type Note, type TrashInfoResponse } from '../api/client';
import Dialog from './Dialog';
import Icon from './Icon';

/**
 * 批次 D2：这里原来有一对 `overlayStyle` / `cardStyle` 内联常量 ——
 * 全屏遮罩 + 居中面板，写着 `zIndex: 1000`、`background: 'rgba(0,0,0,0.5)'`、
 * `maxWidth: 520`、`maxHeight: '80vh'`，注释还自称"与 NoteDetail 关联资料弹窗
 * 保持一致"（也就是靠人工复制常量维持一致）。本文件的两个弹窗改用 `<Dialog>`
 * 基座后它们**已整体删除**：遮罩色 / 层级 / 圆角 / 阴影 / 内边距现在只有
 * `Dialog.module.css` 一个来源。
 *
 * 下面留下的是**面板内部内容**的排版常量（统计块），与弹窗外形无关，逐字保留。
 */
const statRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 'var(--space-md)',
  marginBottom: 'var(--space-md)',
};

const statItemStyle: React.CSSProperties = {
  flex: 1,
  textAlign: 'center' as const,
  padding: 'var(--space-sm) var(--space-xs)',
  background: 'var(--color-primary-light)',
  borderRadius: 'var(--radius-md)',
};

interface DeleteNoteDialogProps {
  /** 要移入回收站的笔记 */
  note: Note;
  /** 关闭弹窗（不执行任何操作） */
  onClose: () => void;
  /** 确认移入回收站 */
  onConfirm: () => void;
}

/**
 * 移入回收站确认弹窗
 *
 * 打开时异步加载笔记的关联统计（trash-info），展示：
 * - 卡片数 / 核心卡片数 / 双向链接数
 * - "关联暂不可见，但可在回收站中整体恢复"的说明
 */
export function DeleteNoteDialog({ note, onClose, onConfirm }: DeleteNoteDialogProps) {
  const [info, setInfo] = useState<TrashInfoResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getNoteTrashInfo(note.id)
      .then((res) => {
        if (!cancelled) setInfo(res);
      })
      .catch(() => {
        // 统计加载失败不阻塞删除流程，仅展示基础提示
        if (!cancelled) setInfo(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [note.id]);

  return (
    <Dialog
      open
      onClose={onClose}
      title="移入回收站"
      // 操作区走基座的 `footer`（右对齐 + 上边距由 `.dialogActions` 统一给）；
      // 两枚按钮本身、文案、onClick 逐字保留。
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" onClick={onConfirm}>
            {/* 批次 B3：两枚确认按钮各补图标，正好演示 `trash` 与 `delete` 的分工 ——
                "移入回收站"是**位置**（`trash`，与侧边栏那一行同一个语义），
                "彻底删除"是**动作**（`delete`，见 icons/delete.tsx 文件头）。
                两个图标都是装饰性的（aria-hidden），按钮的可访问名仍是可见文字。 */}
            <Icon name="trash" size={16} />
            确认移入
          </button>
        </>
      }
    >
      <p style={{ marginBottom: 'var(--space-md)' }}>确定将「{note.title}」移入回收站吗？</p>

      {loading && <p style={{ color: 'var(--color-text-secondary)' }}>正在统计关联内容…</p>}

      {!loading && info && (
        <>
          <div style={statRowStyle}>
            <div style={statItemStyle}>
              <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.card_count}</div>
              <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>
                知识卡片
              </div>
            </div>
            <div style={statItemStyle}>
              <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.key_card_count}</div>
              <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>
                核心卡片
              </div>
            </div>
            <div style={statItemStyle}>
              <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.link_count}</div>
              <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>
                双向链接
              </div>
            </div>
          </div>
          <p
            style={{
              fontSize: '0.875rem',
              color: 'var(--color-text-secondary)',
              background: 'var(--color-accent-light)',
              padding: 'var(--space-sm) var(--space-md)',
              borderRadius: 'var(--radius-md)',
            }}
          >
            该笔记与 {info.card_count} 张知识卡片、{info.link_count} 个原始资料存在双向链接。
            移入回收站后，这些关联将暂不可见，但不影响其他笔记的引用。
            笔记及其全部内容（卡片、题目、复习记录）将作为整体保存，可随时在回收站中恢复。
          </p>
        </>
      )}

      {!loading && !info && (
        <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          笔记及其全部内容（卡片、题目、复习记录）将作为整体移入回收站，可随时恢复。
        </p>
      )}
    </Dialog>
  );
}

interface PurgeNoteDialogProps {
  /** 要彻底删除的笔记 */
  note: Note;
  /** 关闭弹窗（不执行任何操作） */
  onClose: () => void;
  /**
   * 确认彻底删除
   *
   * @param promoteKeyCards - 是否将核心卡片提升为独立节点（图谱中保留）
   */
  onConfirm: (promoteKeyCards: boolean) => void;
}

/**
 * 彻底删除确认弹窗（物理删除，悬挂引用策略）
 *
 * 高级选项："将本笔记中的核心卡片自动提升为独立节点"——
 * 笔记删除后，勾选的核心卡片在知识图谱中依然存活，不会成为信息孤岛。
 */
export function PurgeNoteDialog({ note, onClose, onConfirm }: PurgeNoteDialogProps) {
  const [promote, setPromote] = useState(false);

  return (
    <Dialog
      open
      onClose={onClose}
      title="彻底删除"
      // ⚠️ 原来这一处标题是 `<h3 style={{ color: 'var(--color-error)' }}>` 的红字，
      // 迁到基座后标题由 `Dialog.module.css` 的 `.dialogTitle` 统一给色 ——
      // 危险语义改由下面红底的说明段与红底确认按钮承担（见批次 D2 报告）。
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>
            取消
          </button>
          <button
            className="btn"
            style={{ background: 'var(--color-error)', color: '#fff' }}
            onClick={() => onConfirm(promote)}
          >
            <Icon name="delete" size={16} />
            彻底删除
          </button>
        </>
      }
    >
      <p style={{ marginBottom: 'var(--space-md)' }}>
        确定彻底删除「{note.title}」吗？此操作<strong>不可恢复</strong>。
      </p>
      <p
        style={{
          fontSize: '0.875rem',
          color: 'var(--color-text-secondary)',
          background: 'var(--color-error-light)',
          padding: 'var(--space-sm) var(--space-md)',
          borderRadius: 'var(--radius-md)',
          marginBottom: 'var(--space-md)',
        }}
      >
        笔记及其卡片、题目、复习记录将被永久删除。其他笔记对该笔记的引用将以
        「[已删除的笔记]」占位符保留，不会破坏其他笔记的内容结构。
      </p>

      <label
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 'var(--space-sm)',
          padding: 'var(--space-sm) var(--space-md)',
          background: 'var(--color-accent-light)',
          borderRadius: 'var(--radius-md)',
          cursor: 'pointer',
          fontSize: '0.875rem',
        }}
      >
        <input
          type="checkbox"
          checked={promote}
          onChange={(e) => setPromote(e.target.checked)}
          style={{ marginTop: 2 }}
        />
        <span>
          <strong>高级选项：将本笔记中的核心卡片自动提升为独立节点</strong>
          <br />
          <span style={{ color: 'var(--color-text-secondary)' }}>
            勾选后，标记为核心（is_key_point）的卡片在知识图谱中依然存活，
            不会因父级笔记删除而变成信息孤岛。
          </span>
        </span>
      </label>
    </Dialog>
  );
}
