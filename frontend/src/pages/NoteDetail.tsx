/**
 * @file 笔记详情页面（编排层）
 * @description 展示单条笔记的完整内容，包括：
 * 1. 笔记元信息（标题、来源类型、状态、页数、大小、创建时间）
 * 2. Markdown 内容渲染（支持代码高亮）
 * 3. 原始版/清洗版/对比视图三种模式切换
 * 4. 清洗操作面板（触发清洗、恢复/删除重复块）
 * 5. Diff 对比视图
 * 6. 删除笔记功能
 * 7. 处理中状态的等待提示
 *
 * overhaul-plan 5.5 把本文件从 ~1180 行拆到 300 行以下：
 * - 视图层拆到 `pages/notedetail/` 下的子组件
 * - 批注/选区、链接关系、数据加载与轮询、页面级动作拆成同目录的 hook
 * - 纯判定拆到 `viewMode.ts`，DOM helper 拆到 `selection.ts` / `annotations.ts`
 *
 * 本文件只保留：路由参数、ADHD Reader 接线、状态变化后的整体刷新、
 * 各子模块之间的组装。拆分只做搬运，未改变任何行为。
 */
import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import 'highlight.js/styles/github-dark.css';
import 'katex/dist/katex.min.css';
import { renderMarkdown } from '../utils/markdown';
import CleaningPanel from '../components/CleaningPanel';
import { DeleteNoteDialog } from '../components/DeleteNoteDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import VersionHistory from '../components/VersionHistory';
import { useAdhdReader } from '../hooks/useAdhdReader';
import NoteDetailHeader from './notedetail/NoteDetailHeader';
import NotePropertyRail from './notedetail/NotePropertyRail';
import RelatedLinksSection, { shouldShowRelatedLinks } from './notedetail/RelatedLinksSection';
import CitingNotesSection, { shouldShowCitingNotes } from './notedetail/CitingNotesSection';
import LinkManagerModal from './notedetail/LinkManagerModal';
import VideoPlayer from './notedetail/VideoPlayer';
import ContentArea from './notedetail/ContentArea';
import SelectionMenu from './notedetail/SelectionMenu';
import AnnotationAskPanel from './notedetail/AnnotationAskPanel';
import RelatedCardsSection from './notedetail/RelatedCardsSection';
import LoadErrorView from './notedetail/LoadErrorView';
import { useNoteAnnotations } from './notedetail/useNoteAnnotations';
import { useNoteLinks } from './notedetail/useNoteLinks';
import { useNoteActions } from './notedetail/useNoteActions';
import { useNoteDetailData } from './notedetail/useNoteDetailData';
import {
  canShowClean,
  canShowDiff,
  computeMdContent,
  parseCitationJump,
} from './notedetail/viewMode';
import type { EditMode } from './notedetail/types';
// 批次 E2 新增：元信息竖轨 + 正文的两栏骨架（页头骨架仍留在 notedetail/ 里）
import styles from './NoteDetail.module.css';

/**
 * 笔记详情页面组件
 */
export default function NoteDetail() {
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();

  /** 引用回跳参数（阶段 2.7）：`?view=clean&cs=<char_start>&ce=<char_end>` */
  const [searchParams, setSearchParams] = useSearchParams();
  const jump = parseCitationJump(searchParams);

  const markdownRef = useRef<HTMLElement>(null);

  /** 笔记数据：加载、清洗/学习轮询、引用回跳、视频 blob */
  const {
    note,
    setNote,
    loading,
    setLoading,
    error,
    viewMode,
    setViewMode,
    diffData,
    setDiffData,
    diffLoading,
    relatedCards,
    hasQuizItems,
    videoUrl,
    mutatingRef,
    fetchNote,
  } = useNoteDetailData({ noteId, markdownRef, jump, searchParams, setSearchParams });

  /** 编辑模式相关 state（edit 为实时分屏预览：左侧编辑、右侧即时渲染） */
  const [editMode, setEditMode] = useState<EditMode>('view');
  /** 版本历史面板显示状态 */
  const [showVersionHistory, setShowVersionHistory] = useState(false);

  /** ADHD Reader 专注阅读模式（鼠标遮罩/显示文本） */
  const {
    enabled: adhdReaderEnabled,
    currentLineText: adhdCurrentLineText,
    toggle: toggleAdhdReader,
    disable: disableAdhdReader,
  } = useAdhdReader(markdownRef);

  /** 清洗/学习状态变化后刷新笔记数据 */
  function handleStatusChange() {
    setLoading(true);
    setDiffData(null); // 清除 diff 缓存
    fetchNote();
  }

  /** 链接关系（关联资料 / 被引用） */
  const {
    noteLinks,
    showLinkManager,
    setShowLinkManager,
    linkMaterialIds,
    setLinkMaterialIds,
    availableMaterials,
    handleManageLinks,
    handleSaveLinks,
    handleCleanDanglingLinks,
  } = useNoteLinks({ noteId, noteRole: note?.note_role });

  /** 批注 + 选区浮层（高亮/下划线、AI 提问） */
  const {
    showAnnotationMenu,
    annotationMenuPos,
    askAIState,
    setAskAIState,
    handleMouseUp,
    handleOpenAskAI,
    handleApplyAnnotation,
  } = useNoteAnnotations({ note, viewMode, editMode, markdownRef });

  /** 页面级动作：AI 预处理 / 删除 / 审阅 / 编辑保存 */
  const {
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
  } = useNoteActions({
    note,
    viewMode,
    setEditMode,
    fetchNote,
    onStatusChange: handleStatusChange,
    navigate,
  });

  // 离开纯阅读视图（编辑/对比）时自动关闭 ADHD Reader
  useEffect(() => {
    if ((editMode !== 'view' || viewMode === 'diff') && adhdReaderEnabled) {
      disableAdhdReader();
    }
  }, [editMode, viewMode, adhdReaderEnabled, disableAdhdReader]);

  // 加载中状态
  if (loading) {
    return <LoadingSpinner />;
  }

  // 错误或笔记不存在状态
  if (error || !note) {
    return <LoadErrorView error={error} onBack={() => navigate('/notes')} />;
  }

  /** 选择要显示的 Markdown 内容 */
  const mdContent = computeMdContent(note, viewMode);

  // 将 Markdown 文本解析为 HTML
  const htmlContent = renderMarkdown(mdContent);

  // 编辑预览的 HTML
  const editPreviewHtml = renderMarkdown(editContent);

  // 是否可以显示清洗版 / diff（cleaned/archived/learning_failed 状态都可以查看）
  const showClean = canShowClean(note);
  const showDiff = canShowDiff(note);

  return (
    <div className="page-enter">
      {/* 头部信息区域。
          批次 E2：`onRoleUpdated` 不再传给它 —— 「笔记角色」下拉随元信息
          整块搬进了下面那条竖轨，回调改由 `NotePropertyRail` 接
          （数据流方向与合并逻辑一字未改）。 */}
      <NoteDetailHeader
        note={note}
        noteId={noteId}
        viewMode={viewMode}
        editMode={editMode}
        hasQuizItems={hasQuizItems}
        canShowClean={showClean}
        canShowDiff={showDiff}
        onViewModeChange={setViewMode}
        onEnterEdit={handleEnterEdit}
        onStartLearning={handleStartLearning}
        onArchive={handleArchive}
        onDelete={handleDelete}
        onOpenVersionHistory={() => setShowVersionHistory(true)}
        onManageLinks={handleManageLinks}
        onRetryConverted={(result) =>
          setNote((prev) =>
            prev ? { ...prev, status: result.status, error_message: result.error_message } : prev,
          )
        }
        navigate={navigate}
      />

      {/* 批次 E2：两条栏 —— 左侧「元信息竖轨」，右侧是纯净的主内容区。
          ⚠️ 这一层只加包装：里面每一个子块（清洗面板 / 关联资料 / 被引用 /
          视频 / 正文）连同它们的 props、显示条件、顺序都留在原位。 */}
      <div className={styles.detailBody}>
        <NotePropertyRail
          note={note}
          onRoleUpdated={(noteRole) =>
            setNote((prev) => (prev ? { ...prev, note_role: noteRole } : prev))
          }
        />

        <div className={styles.detailMain}>
          {/* 清洗操作面板（converted/cleaning/cleaning_failed/cleaned 状态时显示） */}
          {(note.status === 'converted' ||
            note.status === 'cleaning' ||
            note.status === 'cleaning_failed' ||
            note.status === 'cleaned') && (
            <CleaningPanel
              note={note}
              onStatusChange={handleStatusChange}
              onMutatingChange={(mutating) => {
                mutatingRef.current = mutating;
              }}
            />
          )}

          {/* 关联的学习资料列表（字段缺失时整块不显示，而不是整页崩掉） */}
          {shouldShowRelatedLinks(noteLinks) && (
            <RelatedLinksSection
              noteLinks={noteLinks}
              onCleanDanglingLinks={handleCleanDanglingLinks}
            />
          )}

          {/* 被引用笔记列表 */}
          {shouldShowCitingNotes(noteLinks) && <CitingNotesSection noteLinks={noteLinks} />}

          {/* 视频播放器（仅视频类型笔记显示） */}
          {note.source_type === 'video' && videoUrl && <VideoPlayer videoUrl={videoUrl} />}

          {/* 内容区域 */}
          <ContentArea
            noteId={noteId}
            status={note.status}
            viewMode={viewMode}
            editMode={editMode}
            mdContent={mdContent}
            htmlContent={htmlContent}
            editPreviewHtml={editPreviewHtml}
            diffData={diffData}
            diffLoading={diffLoading}
            markdownRef={markdownRef}
            onMouseUp={handleMouseUp}
            onRefresh={fetchNote}
            adhdReaderEnabled={adhdReaderEnabled}
            adhdCurrentLineText={adhdCurrentLineText}
            onToggleAdhdReader={toggleAdhdReader}
            editContent={editContent}
            onEditContentChange={setEditContent}
            saving={saving}
            onSave={handleSaveContent}
            onCancelEdit={handleCancelEdit}
          />
        </div>
      </div>

      {/* 批注操作浮层：选中文本后显示高亮/下划线/ AI 提问按钮 */}
      {showAnnotationMenu && editMode === 'view' && (
        <SelectionMenu
          pos={annotationMenuPos}
          onApplyAnnotation={handleApplyAnnotation}
          onOpenAskAI={handleOpenAskAI}
        />
      )}

      {/* AI 提问浮层：基于当前笔记选区上下文流式提问 */}
      {askAIState && editMode === 'view' && (
        <AnnotationAskPanel
          noteId={note.id}
          noteTitle={note.title}
          askAIState={askAIState}
          viewMode={viewMode}
          markdown={mdContent}
          onClose={() => setAskAIState(null)}
        />
      )}

      {/* 链接管理弹窗：选择关联的学习资料 */}
      {showLinkManager && (
        <LinkManagerModal
          availableMaterials={availableMaterials}
          linkMaterialIds={linkMaterialIds}
          onLinkMaterialIdsChange={setLinkMaterialIds}
          onClose={() => setShowLinkManager(false)}
          onSave={handleSaveLinks}
        />
      )}

      {/* 版本历史面板：模态浮层形式 */}
      {showVersionHistory && (
        <VersionHistory
          noteId={note.id}
          onClose={() => setShowVersionHistory(false)}
          onRestored={fetchNote}
        />
      )}

      {/* 移入回收站确认弹窗 */}
      {showDeleteDialog && (
        <DeleteNoteDialog
          note={note}
          onClose={() => setShowDeleteDialog(false)}
          onConfirm={confirmDelete}
        />
      )}

      {/* 关联知识卡片区域 */}
      {noteId && relatedCards.length > 0 && (
        <RelatedCardsSection relatedCards={relatedCards} noteId={noteId} navigate={navigate} />
      )}
    </div>
  );
}
