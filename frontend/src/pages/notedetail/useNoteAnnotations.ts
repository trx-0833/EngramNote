/**
 * @file 笔记批注（高亮/下划线）+ 选区浮层 的页面级 hook
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 两个 effect 的依赖数组、`.catch` 吞错、`confirm()` 文案、
 * 延迟 100ms 应用 DOM 批注等细节均与拆分前逐字一致。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import {
  createAnnotation,
  deleteAnnotation,
  getAnnotations,
  type Annotation,
  type NoteDetail,
} from '../../api/client';
import { useToast } from '../../components/Toast';
import { computeAnnotationContext, computeSelectionContext } from './selection';
import {
  applyAnnotationsToDom,
  createAnnotationWrapper,
  unwrapAnnotationFromDom,
} from './annotations';
import { getAnnotationViewMode } from './viewMode';
import type { AskAIState, EditMode, SelectionContext, ViewMode } from './types';

/** 选中文本的最大可批注长度（超过则提示并放弃） */
const MAX_ANNOTATION_TEXT_LENGTH = 5000;

/** AI 提问参考的上下文窗口（前后各取多少字符） */
const ASK_CONTEXT_WINDOW = 1500;

interface UseNoteAnnotationsOptions {
  /** 当前笔记（未加载完成时为 null） */
  note: NoteDetail | null;
  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 当前编辑模式（编辑态不弹批注浮层） */
  editMode: EditMode;
  /** Markdown 正文容器（批注包裹与选区判定都基于它） */
  markdownRef: RefObject<HTMLElement>;
}

/**
 * 管理批注数据、正文 DOM 包裹、选中浮层与 AI 提问浮层的开关。
 */
export function useNoteAnnotations({
  note,
  viewMode,
  editMode,
  markdownRef,
}: UseNoteAnnotationsOptions) {
  const toast = useToast();

  /** 批注相关 state */
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [showAnnotationMenu, setShowAnnotationMenu] = useState(false);
  const [annotationMenuPos, setAnnotationMenuPos] = useState({ x: 0, y: 0 });

  /** AI 提问浮层 state：选中文本 + 选区上下文 + 浮层位置（null 表示关闭） */
  const [askAIState, setAskAIState] = useState<AskAIState | null>(null);
  /** mouseup 时暂存的选区信息（点击菜单按钮后 live selection 会被清空，需依赖此 ref） */
  const selectionRef = useRef<SelectionContext>({
    text: '',
    contextBefore: '',
    contextAfter: '',
  });

  // 加载批注：当 note 加载完成且 viewMode 确定后加载批注
  useEffect(() => {
    if (!note?.id) return;
    const loadAnnotations = async () => {
      try {
        const data = await getAnnotations(note.id, viewMode);
        setAnnotations(data.annotations || []);
      } catch (err) {
        console.error('加载批注失败:', err);
      }
    };
    loadAnnotations();
  }, [note?.id, viewMode]);

  /** 删除批注 */
  const handleDeleteAnnotation = useCallback(
    async (annotationId: string) => {
      if (!note) return;
      if (!confirm('确定删除此批注？')) return;

      try {
        await deleteAnnotation(note.id, annotationId);
        setAnnotations((prev) => prev.filter((a) => a.id !== annotationId));

        // 从 DOM 移除样式
        unwrapAnnotationFromDom(markdownRef.current, annotationId);
      } catch {
        toast.error('删除批注失败');
      }
    },
    // toast 有意不入依赖：与拆分前一致，删除失败提示用当时闭包里的 toast 即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [note, markdownRef],
  );

  // 批注恢复：DOM 渲染后应用批注到对应文本节点
  // handleDeleteAnnotation 每次渲染重建,加入依赖会让 effect 频繁重跑,
  // 事件监听器捕获的是 effect 创建时的闭包,删除操作仍可用,故豁免 exhaustive-deps
  useEffect(() => {
    if (editMode !== 'view') return;
    if (!markdownRef.current || annotations.length === 0) return;

    // 延迟执行，确保 DOM 已渲染
    setTimeout(
      () =>
        applyAnnotationsToDom(markdownRef.current, annotations, viewMode, handleDeleteAnnotation),
      100,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 见上方注释,handleDeleteAnnotation 不入门
  }, [
    note?.id,
    note?.original_md_content,
    note?.clean_md_content,
    viewMode,
    annotations,
    editMode,
  ]);

  /** 处理鼠标抬起：选中文本时弹出批注操作浮层 */
  function handleMouseUp() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.toString().trim().length === 0) {
      setShowAnnotationMenu(false);
      return;
    }

    // 确保选区在 markdown 内容区域内
    const range = selection.getRangeAt(0);
    if (!markdownRef.current?.contains(range.commonAncestorContainer)) {
      setShowAnnotationMenu(false);
      return;
    }

    // 暂存选区信息（供「AI 提问」使用；点击菜单按钮后 live selection 会被清空）
    selectionRef.current = computeSelectionContext(markdownRef.current, range, ASK_CONTEXT_WINDOW);

    // 计算浮层位置
    const rect = range.getBoundingClientRect();
    setAnnotationMenuPos({
      x: rect.left + rect.width / 2,
      y: rect.top - 10,
    });
    setShowAnnotationMenu(true);
  }

  /** 打开 AI 提问浮层：基于 mouseup 时暂存的选区信息 */
  function handleOpenAskAI() {
    const sel = selectionRef.current;
    if (!sel.text.trim()) return;
    setAskAIState({
      text: sel.text,
      contextBefore: sel.contextBefore,
      contextAfter: sel.contextAfter,
      pos: annotationMenuPos,
    });
    setShowAnnotationMenu(false);
    window.getSelection()?.removeAllRanges();
  }

  /** 应用批注：高亮或下划线 */
  async function handleApplyAnnotation(type: 'highlight' | 'underline') {
    if (!note) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return;

    const text = selection.toString().trim();
    if (!text || text.length > MAX_ANNOTATION_TEXT_LENGTH) {
      toast.warning('选中文本过长或为空');
      return;
    }

    const range = selection.getRangeAt(0);

    // 获取上下文
    const context = computeAnnotationContext(markdownRef.current, range);
    if (!context) return;

    try {
      const newAnn = await createAnnotation(note.id, {
        view_mode: getAnnotationViewMode(viewMode),
        type,
        text_content: text,
        context_before: context.contextBefore,
        context_after: context.contextAfter,
      });

      setAnnotations((prev) => [...prev, newAnn]);

      // 立即应用到 DOM
      const wrapper = createAnnotationWrapper(newAnn.id, type, handleDeleteAnnotation);

      try {
        range.surroundContents(wrapper);
      } catch (err) {
        console.warn('应用批注失败:', err);
      }
    } catch {
      toast.error('保存批注失败');
    }

    setShowAnnotationMenu(false);
    selection.removeAllRanges();
  }

  return {
    annotations,
    showAnnotationMenu,
    annotationMenuPos,
    askAIState,
    setAskAIState,
    handleMouseUp,
    handleOpenAskAI,
    handleApplyAnnotation,
    handleDeleteAnnotation,
  };
}
