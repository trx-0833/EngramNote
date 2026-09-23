/**
 * @file 批注的 DOM 读写 helper（高亮/下划线包裹与还原）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 查找/包裹/还原的顺序、`surroundContents` 的 try-catch 吞错、
 * 点击包裹元素即删除的行为均与拆分前一致。
 */
import type { Annotation } from '../../api/client';

/** 批注类型 → 包裹标签（高亮用 mark，下划线用 u） */
function wrapperTagFor(type: Annotation['type']): 'mark' | 'u' {
  return type === 'highlight' ? 'mark' : 'u';
}

/** 批注类型 → CSS 类名 */
function wrapperClassFor(type: Annotation['type']): string {
  return type === 'highlight' ? 'annotation-mark' : 'annotation-underline';
}

/**
 * 创建包裹元素：打上 data-annotation-id 与类名，点击即请求删除该批注
 *
 * 点击回调**每次创建时捕获**传入的 `onDelete`（拆分前亦如此，
 * 事件监听器捕获的是 effect 创建时的闭包）。
 */
export function createAnnotationWrapper(
  id: string,
  type: Annotation['type'],
  onDelete: (annotationId: string) => void,
): HTMLElement {
  const wrapper = document.createElement(wrapperTagFor(type));
  wrapper.className = wrapperClassFor(type);
  wrapper.dataset.annotationId = id;
  wrapper.addEventListener('click', (e) => {
    e.stopPropagation();
    onDelete(id);
  });
  return wrapper;
}

/**
 * 把一条批注应用到正文 DOM（本模块内部使用）。
 *
 * - 已应用过（存在同名 data-annotation-id 节点）则跳过，避免重复包裹
 * - 只包裹**第一个**匹配且上下文校验通过的文本节点
 * - `surroundContents` 跨节点失败时静默跳过（与拆分前一致）
 *
 * @returns 是否成功包裹
 */
function applyAnnotationToDom(
  container: HTMLElement | null,
  ann: Annotation,
  onDelete: (annotationId: string) => void,
): boolean {
  if (!container) return false;

  // 跳过已应用的批注，避免重复包裹 DOM
  if (container.querySelector(`[data-annotation-id="${ann.id}"]`)) return false;

  // 在 DOM 中查找匹配的文本
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);

  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    const text = node.textContent || '';
    const idx = text.indexOf(ann.text_content);
    if (idx >= 0) {
      // 验证上下文（可选，简单验证）
      const before = text.substring(Math.max(0, idx - 50), idx);
      const after = text.substring(
        idx + ann.text_content.length,
        idx + ann.text_content.length + 50,
      );
      if (ann.context_before && !before.endsWith(ann.context_before)) continue;
      if (ann.context_after && !after.startsWith(ann.context_after)) continue;

      // 创建包裹元素
      const range = document.createRange();
      range.setStart(node, idx);
      range.setEnd(node, idx + ann.text_content.length);

      const wrapper = createAnnotationWrapper(ann.id, ann.type, onDelete);

      try {
        range.surroundContents(wrapper);
      } catch {
        // surroundContents 可能跨节点失败，跳过
      }
      return true;
    }
  }
  return false;
}

/**
 * 应用当前视图下的全部批注（DOM 渲染完成后调用一次）
 *
 * `viewMode` 只处理与之匹配的批注：切视图会重建 DOM，
 * 把另一份副本的批注套到当前文本上会指向错误的段落。
 */
export function applyAnnotationsToDom(
  container: HTMLElement | null,
  annotations: Annotation[],
  viewMode: string,
  onDelete: (annotationId: string) => void,
): void {
  if (!container) return;
  annotations.forEach((ann) => {
    if (ann.view_mode !== viewMode) return;
    applyAnnotationToDom(container, ann, onDelete);
  });
}

/**
 * 从 DOM 中移除批注样式：把包裹元素的内容提回父节点后删掉包裹元素，
 * 并 `normalize()` 合并相邻文本节点（否则连续的文本会被切成多个节点，
 * 影响下一次按文本查找批注）。
 */
export function unwrapAnnotationFromDom(container: HTMLElement | null, annotationId: string): void {
  const elem = container?.querySelector(`[data-annotation-id="${annotationId}"]`);
  if (!elem) return;
  const parent = elem.parentNode;
  while (elem.firstChild) {
    parent?.insertBefore(elem.firstChild, elem);
  }
  parent?.removeChild(elem);
  parent?.normalize(); // 合并相邻文本节点
}
