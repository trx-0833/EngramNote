/**
 * @file 批注操作浮层（高亮 / 下划线 / AI 提问）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 定位方式（fixed + translate(-50%, -100%)）、按钮文案与 title 均与拆分前一致。
 * 显示条件（`showAnnotationMenu && editMode === 'view'`）由页面侧判断。
 */
// 浮层样式（overhaul-plan 5.6）：原 src/styles/markdown-extras.css 的
// `.selection-menu` 3 条搬到这里
import styles from './SelectionMenu.module.css';
import { useTranslation } from '../../i18n';

interface SelectionMenuProps {
  /** 浮层锚点（选区中心的视口坐标） */
  pos: { x: number; y: number };
  /** 应用高亮批注 */
  onApplyAnnotation: (type: 'highlight' | 'underline') => void;
  /** 打开 AI 提问浮层 */
  onOpenAskAI: () => void;
}

/** 选中文本后显示的批注操作浮层 */
export default function SelectionMenu({ pos, onApplyAnnotation, onOpenAskAI }: SelectionMenuProps) {
  const { t } = useTranslation();
  return (
    <div
      className={styles.selectionMenu}
      style={{
        position: 'fixed',
        left: pos.x,
        top: pos.y,
        transform: 'translate(-50%, -100%)',
        zIndex: 1000,
      }}
    >
      <button onClick={() => onApplyAnnotation('highlight')} title={t('noteAnnotations.highlight')}>
        {t('noteAnnotations.highlight')}
      </button>
      <button onClick={() => onApplyAnnotation('underline')} title={t('noteAnnotations.underline')}>
        {t('noteAnnotations.underline')}
      </button>
      <button onClick={onOpenAskAI} title={t('noteAnnotations.askAiTitle')}>
        {t('noteAsk.title')}
      </button>
    </div>
  );
}
