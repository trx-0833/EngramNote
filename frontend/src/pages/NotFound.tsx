/**
 * 404 页（visual-refactor-plan 批次 E8）
 *
 * ## 从 `App.tsx` 搬出来
 *
 * 计划 §6 的 E8 行把「404」列成一页，路径写的是 `src/pages/NotFound.tsx` ——
 * 而代码里它一直是一个**内联在 `App.tsx` 里的局部函数**（`path="*"` 那一条路由）。
 * 按本仓库"文档与代码冲突时以代码实测为准"的规矩，这次做的是**把它变成计划里
 * 那一页**：抽成独立页面 + 自己的模块样式，行为一个字不改。
 *
 * ## 哪些东西一个字都没动（有测试/审计钉着）
 *
 * | 内容 | 谁钉着 |
 * |---|---|
 * | `<h1>` 的文本 `404` | `e2e/a11y.spec.ts` 的 `not-found` 场景（`getByRole('heading', { name: '404' })`） |
 * | 正文 `页面不存在，可能是链接已失效。` | 同上（逐字匹配） |
 * | `返回首页` 是**链接**（`<a>`）而不是按钮 | 同上（`getByRole('link', …)`）；也是 `login-api.spec.ts` 反向断言"不该出现 404"的锚点 |
 * | `href="/"` 的真实跳转（整页导航） | 迁移前就是普通 `<a>`，**没有**改成 `navigate()` —— 那是行为改动，不属于视觉批次 |
 * | `heading-order`：页面里只有这一个 `<h1>`、没有别的标题 | axe 的 `heading-order` / `page-has-heading-one` |
 *
 * ## 外观上的两处（都是"往令牌上收"，不动大小）
 *
 * 1. `font-size: 2rem` 改用 `var(--text-2xl)`（令牌值逐字等于 2rem）；
 * 2. `padding: 64px 16px` 改用 `var(--space-2xl) var(--space-md)`（上下 64 → 48px，
 *    一屏居中的空页上肉眼几乎不可分，换来的是"不留裸数字"）。
 */
import styles from './NotFound.module.css';

export default function NotFound() {
  return (
    <div className={`page-enter ${styles.notFoundPage}`}>
      <h1 className={styles.notFoundTitle}>404</h1>
      <p className={styles.notFoundMessage}>页面不存在，可能是链接已失效。</p>
      {/* 按钮造型的链接：`base.css` 给所有 <a> 加了默认下划线
          （正文链接必须能与正文区分，见 a11y-audit F-13/F-26），
          这里显式关掉 —— 它长得是按钮，不是正文里的链接。
          ⚠️ 不要把它换成 `<button onClick={navigate}>`：读屏与
          `getByRole('link', { name: '返回首页' })` 都按链接认它。 */}
      <a className={`btn btn-primary ${styles.notFoundHomeLink}`} href="/">
        返回首页
      </a>
    </div>
  );
}
