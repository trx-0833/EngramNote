import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

/**
 * ⚠️ 这段导入顺序**有语义，不要重排**（overhaul-plan 5.6）。
 *
 * Vite 按**模块图顺序**产出 CSS，而组件里的 `*.module.css` 也走同一条链。
 * 原来 `import App` 在上面、样式表在下面，于是静态引入的组件
 * （`App` → `pages/Login` → `Auth.module.css`）的模块 CSS
 * **排到了全部全局样式表之前**，产物里同权重规则的胜负因此反转：
 * `.authSubmit` 的 `padding / font-size / font-weight / transition`
 * 被全局 `.btn` 反盖（按钮肉眼可见地变小变细），而文本差集完全看不出这种损失
 * —— 两条规则都还在、值也没改，只有"谁最终生效"变了。
 * 实测（重排前）产物 `index.css` 的字节位置：
 * `Auth.module.css` = 1、`base.css` 的 `:root` = 2551、`.btn` = 6555。
 *
 * 全局样式表放在组件之前之后，产物顺序就是
 * **①令牌层 → ②全局层 → ③模块层**，与 `docs/css-convention.md` §1 的分层一致。
 *
 * 这一层顺序同时还是 `src/styles/mobile-input-font-size.test.ts` 断言的依据：
 * 它读本文件、按这里的先后把样式表拼起来，让 jsdom 算出真实级联值
 * （`markdown-extras.css` 必须在 `responsive.css` 之后，否则 `.ask-ai-input`
 * 的窄屏兜底会被压掉）。改这里的顺序 = 改那条断言的前提。
 */
import './styles/base.css';
import './styles/components.css';
import './styles/markdown.css';
import './styles/diff.css';
import './styles/cleaning.css';
import './styles/auth.css';
import './styles/layout.css';
import './styles/dashboard.css';
import './styles/learning.css';
import './styles/graph.css';
import './styles/assessment.css';
import './styles/responsive.css';
import './styles/markdown-extras.css';
import './styles/refinements.css';

// ── 应用代码放在样式表**之后**：理由见上面的注释 ──
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './components/Toast';
import { ConfirmProvider } from './components/ConfirmProvider';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* 最外层错误边界：即使路由层或布局层抛错也不会白屏 */}
    <ErrorBoundary>
      <ToastProvider>
        {/* 确认框宿主（批次 D3 后半）：给自定义 hook 里的确认提供 useConfirm()。
            必须在 App 之外 —— 用到它的 hook 分布在多个页面里 */}
        <ConfirmProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ConfirmProvider>
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
