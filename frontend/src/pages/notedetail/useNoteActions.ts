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
  exportNote,
  startUnderstanding,
  updateNoteContent,
  type NoteContentTarget,
  type NoteDetail,
} from '../../api/client';
import { useConfirm } from '../../components/ConfirmProvider';
import { useToast } from '../../components/Toast';
import type { EditMode, ViewMode } from './types';
import { translate } from '../../i18n/translate';

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
  const confirm = useConfirm();

  /** 编辑模式相关 state（edit 为实时分屏预览：左侧编辑、右侧即时渲染） */
  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);
  /** 移入回收站确认弹窗显示状态 */
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  /**
   * 导出进行中（想法 4）
   *
   * 存在的理由不是"防重复点击"（导出是只读的，点两次只是存两份文件），
   * 而是**给用户一个反馈**：导出要先下载整篇正文再落盘，长笔记需要一点时间，
   * 期间按钮不可点 + 文案变化，用户才知道"点了、正在办"。
   */
  const [exporting, setExporting] = useState(false);

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
          if (impact.cards > 0)
            parts.push(translate('noteActions.impactCards', { count: impact.cards }));
          if (impact.quizzes > 0)
            parts.push(translate('noteActions.impactQuizzes', { count: impact.quizzes }));
          if (impact.review_logs > 0)
            parts.push(translate('noteActions.impactReviewLogs', { count: impact.review_logs }));
          if (impact.relations > 0)
            parts.push(translate('noteActions.impactRelations', { count: impact.relations }));
        }
        // 批次 D3 后半：原文案的文字一个字符都没增删，只是按 ConfirmDialog 的
        // 两段重新分了一次段 —— "不可恢复、确定继续"那句原来跟在影响清单后面，
        // 现在与第一句合成 title，清单整体挪进 message。
        const ok = await confirm({
          title: translate('noteActions.relearnConfirm'),
          message:
            parts.length > 0
              ? translate('noteActions.relearnImpact', {
                  items: parts.join(translate('noteActions.impactSeparator')),
                })
              : undefined,
          confirmText: translate('noteActions.continueAction'),
          danger: true,
        });
        if (!ok) return;
        await startUnderstanding(note.id, true);
        onStatusChange();
        return;
      }
      onStatusChange();
    } catch (err) {
      // 409（进行中）等状态透传提示
      toast.error(
        err instanceof Error ? err.message : translate('noteActions.startLearningFailed'),
      );
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
      toast.error(err instanceof Error ? err.message : translate('noteActions.moveToTrashFailed'));
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
      toast.error(err instanceof Error ? err.message : translate('noteActions.operationFailed'));
    }
  }

  /**
   * 导出笔记正文为 `.md` 文件（想法 4「设计导出功能」）
   *
   * ## 导出哪一版 = 用户当前在看哪一版
   *
   * 页头已有「原始版 / 清洗版」切换，这里跟着它走 —— 不新增任何选择 UI，
   * 也不会让用户猜"我导的到底是哪一版"。
   *
   * `diff` 视图落到 `clean`：那是"对比"不是一个版本，理论上按钮在 diff 下
   * 是禁用的（见 `NoteDetailHeader`）。这里的兜底只是为了"万一被调用到"
   * 时有个确定行为 —— 导清洗版是更合理的那个确定行为，因为清洗版是
   * 可编辑、质量更高的那一份。
   *
   * ## 为什么不能直接把 URL 交给 `<a href>`
   *
   * 端点需要 `Authorization` 头，而浏览器对 `<a href>` 的导航请求不带自定义头。
   * 所以必须先 `fetch` 成 blob（带令牌），再在内存里造一个 blob URL。
   * 这套"取回 → 造 URL → 点击 → 释放"的写法与视频加载同源
   * （`useNoteDetailData.ts:237-261`），只是那边是 `<video src>`，
   * 这里是 `<a download>`。
   *
   * ## `revokeObjectURL` 必须在 `finally` 里
   *
   * blob URL 会把整个文件内容**钉在内存里**直到被释放。不在 `finally` 释放的话，
   * 每次导出都漏一份 —— 而这类泄漏在页面上完全看不出来，只有反复导出
   * 大笔记时才会表现为内存涨。
   *
   * ## 文件名
   *
   * 优先用后端从 `Content-Disposition` 给出的名字（命名规则只有后端一份实现：
   * 标题安全化 + RFC 5987 编码）。后端没给或解析不出来时，回退到
   * `${note.title}.md` —— 那个名字可能含非法字符，但**浏览器的 `download`
   * 属性会自己做一次安全化**，比我们在这里再实现一遍规则可靠。
   */
  async function handleExport() {
    if (!note) return;
    // diff 视图下按"导清洗版"处理（按钮在 diff 下禁用，见函数头说明）
    const variant: 'clean' | 'original' = viewMode === 'original' ? 'original' : 'clean';
    setExporting(true);
    try {
      const { blob, filename } = await exportNote(note.id, variant);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename ?? `${note.title}.md`;
        document.body.appendChild(anchor);
        anchor.click();
        // 点击后立刻移除：`<a>` 只在这次点击里有用，留在 DOM 里没有任何意义
        document.body.removeChild(anchor);
      } finally {
        // 见函数头：不释放会漏一份文件内容在内存里
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      // `NOTE_EXPORT_EMPTY`（该版本没有正文）也会走到这里 ——
      // 后端的中文 detail 已经说清了原因，直接透传
      toast.error(err instanceof Error ? err.message : translate('noteActions.exportFailed'));
    } finally {
      setExporting(false);
    }
  }

  /** 进入编辑模式：预填充内容（仅允许编辑清洗版，原始版只读） */
  function handleEnterEdit() {
    if (!note) return;
    if (viewMode === 'original') {
      toast.warning(translate('noteActions.originalNotEditable'));
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
      toast.error(err instanceof Error ? err.message : translate('noteActions.saveFailed'));
    } finally {
      setSaving(false);
    }
  }

  /**
   * 取消编辑（有未保存修改时确认）
   *
   * 批次 D3 后半：`confirm` 从同步变异步，于是这个函数**变成 async**。
   * "确认之后才退出编辑"的语义与原来一致 —— 原来 `window.confirm` 同步阻塞，
   * 用户点完才往下走；现在那个"等用户点完"由 `await` 承担。
   * 调用方是按钮的 `onClick`，React 会忽略返回的 Promise。
   */
  async function handleCancelEdit() {
    const original = note?.clean_md_content || '';
    if (editContent !== original) {
      // 放弃编辑可以重来（内容还在后端），所以不给 danger
      const ok = await confirm({
        title: translate('noteActions.discardConfirm'),
        confirmText: translate('noteActions.discard'),
      });
      if (!ok) return;
    }
    setEditContent('');
    setEditMode('view');
  }

  return {
    editContent,
    setEditContent,
    saving,
    showDeleteDialog,
    setShowDeleteDialog,
    exporting,
    handleStartLearning,
    handleDelete,
    confirmDelete,
    handleArchive,
    handleExport,
    handleEnterEdit,
    handleSaveContent,
    handleCancelEdit,
  };
}
