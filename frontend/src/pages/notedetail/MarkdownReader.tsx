/**
 * @file Markdown 阅读区（含 ADHD Reader 工具条）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 工具条文案、按钮类名、行级文本展示结构、正文容器的
 * `ref`/`dangerouslySetInnerHTML`/`onMouseUp` 均与拆分前一致。
 */
import type { RefObject } from 'react';

import Icon from '../../components/Icon';

interface MarkdownReaderProps {
  /** 渲染好的正文 HTML */
  htmlContent: string;
  /** 正文容器（批注包裹与选区判定都基于它，由页面持有） */
  markdownRef: RefObject<HTMLElement>;
  /** 选中文本后弹出批注浮层 */
  onMouseUp: () => void;
  /** ADHD Reader 是否开启 */
  adhdReaderEnabled: boolean;
  /** ADHD Reader 当前鼠标所在行的文本 */
  adhdCurrentLineText: string;
  /** 切换 ADHD Reader */
  onToggleAdhdReader: () => void;
}

/** Markdown 渲染区 + ADHD Reader 工具条 */
export default function MarkdownReader({
  htmlContent,
  markdownRef,
  onMouseUp,
  adhdReaderEnabled,
  adhdCurrentLineText,
  onToggleAdhdReader,
}: MarkdownReaderProps) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-sm)',
          flexWrap: 'wrap',
        }}
      >
        <button
          className={`btn ${adhdReaderEnabled ? 'btn-primary' : 'btn-secondary'}`}
          onClick={onToggleAdhdReader}
          title={
            adhdReaderEnabled
              ? '关闭 ADHD 专注阅读模式'
              : '开启 ADHD 专注阅读模式（鼠标跟随：高亮所在行、模糊其他内容）'
          }
        >
          {adhdReaderEnabled ? '关闭 ADHD Reader' : '开启 ADHD Reader'}
        </button>
        {adhdReaderEnabled && (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: '0.8rem',
              color: 'var(--color-text-secondary)',
            }}
          >
            {/* 批次 B3：`\u{1F5B1}` 🖱 换 `<Icon name="mouse" />` —— emoji 自带颜色，
                在这一行灰字提示里是唯一的彩色字形 */}
            <Icon name="mouse" size={16} />
            鼠标跟随 · 移动鼠标高亮所在行
          </span>
        )}
      </div>
      {/* 行级文本捕捉：实时展示鼠标所在的那一行文字 */}
      {adhdReaderEnabled && adhdCurrentLineText && (
        <div
          style={{
            marginBottom: 'var(--space-sm)',
            fontSize: '0.85rem',
            color: 'var(--color-text-secondary)',
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-sm)',
          }}
        >
          {/* 批次 B3：`\u{1F4D6}` 📖 换 `<Icon name="book" />`（摊开的书）——
              与 `notes`（合着的册页）刻意区分：那个的语义是"一篇笔记" */}
          <span
            style={{
              flexShrink: 0,
              fontWeight: 600,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <Icon name="book" size={16} />
            正在阅读
          </span>
          <span
            style={{
              color: 'var(--color-text)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            “{adhdCurrentLineText}”
          </span>
        </div>
      )}
      <article
        ref={markdownRef}
        className="card markdown-body"
        dangerouslySetInnerHTML={{ __html: htmlContent }}
        onMouseUp={onMouseUp}
      />
    </>
  );
}
