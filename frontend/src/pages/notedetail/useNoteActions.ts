/**
 * @file 笔记详情页的页面级动作：AI 预处理 / 删除 / 审阅 / 编辑保存
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * `startUnderstanding` 的二次确认文案、接口调用顺序、
 * toast 兜底文案、`updateNoteContent` 的目标版本（恒为 clean）均与拆分前一致。
 */
import { useState } from 'react';
import type { NavigateFunction } from 'react-router-dom';
import {
  archiveNote,
  deleteNote,
  startUnderstanding,
  updateNoteContent,
  type NoteContentTarget,
  type NoteDetail,
} from '../../api/client';
import { useToast } from '../../components/Toast';
import type { EditMode, ViewMode } from './types';

interface UseNoteActionsOptions {
  /** 当前笔记（未加载完成时为 null） */
  note: NoteDetail | null;
  /** 当前视图模式（决定是否可进入编辑） */
  viewMode: ViewMode;
  /** 编辑模式写回 */
  setEditMode: (mode: EditMode) => void;
  /** 重新拉取笔记数据 */
  fetchNote: () => void;
  /** 清洗/学习状态变化后刷新（会重置 loading 与 diff 缓存） */
  onStatusChange: () => void;
  /** 更新笔记链接后需要刷新页面数据 */
  navigate: NavigateFunction;
}

/**
 * 管理页面级动作，并持有编辑模式的本地 state（editContent / saving）。
 */
export function useNoteActions({
  note,
  viewMode,
  setEditMode,
  fetchNote,
  onStatusChange,
  navigate,
}: UseNoteActionsOptions) {
  const toast = useToast();

  /** 编辑模式相关 state（edit 为实时分屏预览：左侧编辑、右侧即时渲染） */
  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);
  /** 移入回收站确认弹窗显示状态 */
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  /** 触发理解管道（开始学习） */
  async function handleStartLearning() {
    if (!note) return;
    try {
      // archived 笔记重新理解会清空全部旧产物，先获取影响数量并二次确认，见 docs/decisions.md#F-02
      const res = await startUnderstanding(note.id, false);
      if (res.requires_confirm) {
        const impact = res.impact;
        const parts: string[] = [];
        if (impact) {
          if (impact.cards > 0) parts.push(`${impact.cards} 张知识卡片`);
          if (impact.quizzes > 0) parts.push(`${impact.quizzes} 道题目`);
          if (impact.review_logs > 0) parts.push(`${impact.review_logs} 条复习记录`);
          if (impact.relations > 0) parts.push(`${impact.relations} 条图谱关系`);
        }
        const detail = parts.length > 0 ? `\n\n将删除：${parts.join('、')}` : '';
        const ok = confirm(
          `此笔记已归档，重新学习将清空其现有学习成果。${detail}\n\n此操作不可恢复，确定继续？`,
        );
        if (!ok) return;
        await startUnderstanding(note.id, true);
        onStatusChange();
        return;
      }
      onStatusChange();
    } catch (err) {
      // 409（进行中）等状态透传提示
      toast.error(err instanceof Error ? err.message : '启动学习失败');
    }
  }

  /** 处理删除笔记（打开移入回收站确认弹窗） */
  function handleDelete() {
    if (!note) return;
    setShowDeleteDialog(true);
  }

  /** 确认移入回收站：调用软删除 API 后返回列表页 */
  async function confirmDelete() {
    if (!note) return;
    try {
      await deleteNote(note.id);
      navigate('/notes');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '移入回收站失败');
      setShowDeleteDialog(false);
    }
  }

  /** 处理归档/取消归档 */
  async function handleArchive() {
    if (!note) return;
    try {
      await archiveNote(note.id);
      await fetchNote(); // 重新获取完整数据，避免部分数据覆盖
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    }
  }

  /** 进入编辑模式：预填充内容（仅允许编辑清洗版，原始版只读） */
  function handleEnterEdit() {
    if (!note) return;
    if (viewMode === 'original') {
      toast.warning('原始版不可编辑，请切换到清洗版后编辑');
      return;
    }
    setEditContent(note.clean_md_content || '');
    setEditMode('edit');
  }

  /** 保存编辑内容 */
  async function handleSaveContent() {
    if (!note) return;
    setSaving(true);
    try {
      // 原始版只读，编辑始终写入清洗版
      const target: NoteContentTarget = 'clean';
      await updateNoteContent(note.id, editContent, target);
      await fetchNote();
      setEditMode('view');
      setEditContent('');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  /** 取消编辑（有未保存修改时确认） */
  function handleCancelEdit() {
    const original = note?.clean_md_content || '';
    if (editContent !== original && !confirm('放弃当前编辑的修改？')) return;
    setEditContent('');
    setEditMode('view');
  }

  return {
    editContent,
    setEditContent,
    saving,
    showDeleteDialog,
    setShowDeleteDialog,
    handleStartLearning,
    handleDelete,
    confirmDelete,
    handleArchive,
    handleEnterEdit,
    handleSaveContent,
    handleCancelEdit,
  };
}
