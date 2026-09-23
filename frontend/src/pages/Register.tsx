/**
 * @file 注册页面
 * @description 未认证分支的第二个入口（登录页之外唯一未认证页面）。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import styles from './Auth.module.css';

export default function Register() {
  const navigate = useNavigate();
  const { register } = useAuth();
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');

    if (!/^[a-zA-Z0-9]+$/.test(username)) {
      setError('用户名只能包含英文字母和数字');
      return;
    }

    setLoading(true);
    try {
      await register(email, username, password);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.authBg}>
      <div className={styles.authCard}>
        {/* 地标：与登录页**逐字同形**的问题与修法（F-01/F-02）。
            注册页此前整页没有 `<main>`，h1、三个 label/input 与页脚
            都不在任何 landmark 里 —— axe 报 `landmark-one-main`（1 个节点）
            + `region`（8 个节点），屏幕阅读器的"跳到主内容"在这一页不可用。
            `<main>` 放在卡片**内部**而不是替掉卡片，理由与登录页相同：
            `.authCard` 的 max-width/padding/毛玻璃/入场动画都挂在那个 div 上，
            换掉它就得把这些值复制一遍（那正是会漂移的写法）。
            ⚠️ 这一页是**未认证分支**的第二个入口，只有把它加进扫描场景
            （`register`）才会被发现 —— 上一轮修登录页时它不在任何场景里。 */}
        <main>
          <h1 className={styles.authTitle}>注册 EngramNote</h1>

          <form
            onSubmit={handleSubmit}
            style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}
          >
            <div className={styles.authInputGroup}>
              <label htmlFor="email">邮箱</label>
              <svg
                className={styles.authInputIcon}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
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
              <label htmlFor="username">用户名</label>
              <svg
                className={styles.authInputIcon}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                minLength={2}
                maxLength={50}
                pattern="[a-zA-Z0-9]+"
                placeholder="仅限英文和数字，2-50个字符"
              />
            </div>

            <div className={styles.authInputGroup}>
              <label htmlFor="password">密码</label>
              <svg
                className={styles.authInputIcon}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                autoComplete="new-password"
                placeholder="至少6位"
              />
            </div>

            {error && (
              <p
                role="alert"
                style={{ color: 'var(--color-error)', fontSize: '0.875rem', textAlign: 'center' }}
              >
                {error}
              </p>
            )}

            <button type="submit" className={`btn ${styles.authSubmit}`} disabled={loading}>
              {loading ? '注册中...' : '注册'}
            </button>
          </form>

          <p className={styles.authFooter}>
            已有账号？{' '}
            <a
              href="/login"
              onClick={(e) => {
                e.preventDefault();
                navigate('/login');
              }}
            >
              登录
            </a>
          </p>
        </main>
      </div>
    </div>
  );
}
