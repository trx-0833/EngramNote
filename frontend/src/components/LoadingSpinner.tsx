/**
 * @file 统一加载中组件
 * @description 优雅的渐变脉冲圆环动画
 *
 * i18n 阶段 1：`text` 的默认值从硬编码中文改成 `t('common.loading')`。
 *
 * ⚠️ 仍然保留 `text` prop —— 有些调用方要传更具体的文案
 * （"正在生成题目…" 这类）。那种由**调用方**自己 `t()` 后传进来，
 * 本组件不猜它的语义、也不去建一张"加载中"的子表。
 *
 * 注意默认值从 `'加载中...'` 改成了 `'加载中…'`（三点 → 省略号）：
 * 项目其余地方用的是 `…`（U+2026），这里原来是不一致的那一处。
 * 单字符宽度的差别在界面上看不出来，但它让"同一个词两种写法"消失了。
 */
import { useTranslation } from '../i18n';

export default function LoadingSpinner({ text }: { text?: string }) {
  const { t } = useTranslation();
  return (
    <div className="state-container">
      <div className="spinner" />
      <p className="state-message" style={{ marginTop: 'var(--space-md)' }}>
        {text ?? t('common.loading')}
      </p>
    </div>
  );
}
