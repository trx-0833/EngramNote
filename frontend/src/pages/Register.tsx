/**
 * @file 注册页面
 * @description 未认证分支的第二个入口（登录页之外唯一未认证页面）。
 *
 * ⚠️ 与登录页共用 `Auth.module.css`，包括那一处**刻意保留的渐变**
 * （批次 A3 的边界／E8 行「登录注册（渐变只留这里）」）—— 删它属于产品级外观决策，
 * 见该文件 `.authTitle` 上方的注释。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../components/Icon';
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

          <form onSubmit={handleSubmit} className={styles.authForm}>
            <div className={styles.authInputGroup}>
              <label htmlFor="email">邮箱</label>
              {/* 批次 B3：内联 `<svg>` → `<Icon name="mail" />`（图形逐点未改，
                  线宽由内联的 2 收到全站统一的 1.5，尺寸落回 20 这一档） */}
              <Icon name="mail" size={20} className={styles.authInputIcon} />
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
              {/* 批次 B3：内联 `<svg>` → `<Icon name="user" />`（图形未改） */}
              <Icon name="user" size={20} className={styles.authInputIcon} />
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
              {/* 批次 B3：内联 `<svg>` → `<Icon name="lock" />`（图形未改） */}
              <Icon name="lock" size={20} className={styles.authInputIcon} />
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
              <p role="alert" className={styles.authError}>
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
