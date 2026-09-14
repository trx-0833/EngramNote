/**
 * @file 登录页面
 * @description 品牌化登录页面，渐变背景 + 毛玻璃卡片
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
// 认证页样式（overhaul-plan 5.6）：与 Register 共用一份，见 Auth.module.css 文件头
import styles from './Auth.module.css'

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(email, password)
      // 登录成功后必须显式跳到 `/`。
      //
      // 为什么不能"靠路由表自己切过去"：`/login` 这条路由只注册在 App.tsx 的
      // **未登录**分支里，切成已登录分支后 pathname 仍停在 `/login`，
      // 而已登录分支的路由表没有它 —— 于是落到 `path="*"` 的 404 页。
      // 从 `/` 进入的用户看不出问题（pathname 本来就是 `/`），只有从
      // `/login`（注册页页脚点过来，或直接输地址）登录的人会撞上。
      //
      // 放在 `await` 之后的成功路径上：`login` 抛错（401 等）时**不会**执行，
      // 失败仍然留在登录页显示错误（见上面的 catch）。
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.authBg}>
      <div className={styles.authCard}>
        {/* 地标：登录页此前**整页**没有 <main>，h1、两个 label/input 与页脚
            都不在任何 landmark 里 —— axe 报 `landmark-one-main` + `region`
            共 7 个节点（F-01/F-02），屏幕阅读器的"跳到主内容"在这一页不可用。
            把 `<main>` 放在卡片**内部**而不是替掉卡片：`.authCard` 的
            max-width/padding/毛玻璃/入场动画都挂在那个 div 上，换掉它就得把
            这些值复制一遍（那正是会漂移的写法）；这一层被包住的元素
            正好就是这一页的全部内容，所以地标边界等于页面边界。 */}
        <main>
          <h1 className={styles.authTitle}>登录 EngramNote</h1>

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
            <div className={styles.authInputGroup}>
              <label htmlFor="email">邮箱</label>
              <svg className={styles.authInputIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="4" width="20" height="16" rx="2" />
                <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
              </svg>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder="your@email.com"
              />
            </div>

            <div className={styles.authInputGroup}>
              <label htmlFor="password">密码</label>
              <svg className={styles.authInputIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                placeholder="至少6位"
              />
            </div>

            {error && (
              <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem', textAlign: 'center' }}>
                {error}
              </p>
            )}

            <button type="submit" className={`btn ${styles.authSubmit}`} disabled={loading}>
              {loading ? '登录中...' : '登录'}
            </button>
          </form>

          <p className={styles.authFooter}>
            没有账号？{' '}
            <a href="/register" onClick={(e) => { e.preventDefault(); navigate('/register') }}>
              注册
            </a>
          </p>
        </main>
      </div>
    </div>
  )
}
