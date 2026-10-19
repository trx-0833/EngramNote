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
import { useTranslation } from '../i18n';
import styles from './Auth.module.css';

export default function Register() {
  const navigate = useNavigate();
  const { register } = useAuth();
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');

    // ⚠️ 这一道是**防御性**校验 —— 通过正常交互**到不了这里**。
    //
    // 同一个 input 上还有 HTML5 的 `pattern="[a-zA-Z0-9]+"`，而两者判据
    // **完全相同**，所以浏览器会在提交前先拦下（实测真 Chromium：
    // `validity.patternMismatch=true`、点提交后页面上没有这个 `role="alert"`，
    // 浏览器弹自己的原生提示）。
    //
    // 留着它的理由：① `pattern` 哪天被去掉或改宽，这一道仍然挡得住；
    // ② 程序化提交（`form.submit()`）绕过原生校验时它是唯一的防线；
    // ③ 后端的 `pattern` 是最终判据，这里提前给出**本地化**的提示。
    //
    // 也正因如此，`auth.usernameInvalid` 那条翻译只在这条路径上出现 ——
    // `AuthPages.i18n.test.tsx` 里测它时必须**直接派发 submit**
    // 绕过原生校验（用 `click` 永远测不到）。
    if (!/^[a-zA-Z0-9]+$/.test(username)) {
      setError(t('auth.usernameInvalid'));
      return;
    }

    setLoading(true);
    try {
      await register(email, username, password);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.registerFailed'));
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
          <h1 className={styles.authTitle}>{t('auth.registerTitle')}</h1>

          <form onSubmit={handleSubmit} className={styles.authForm}>
            <div className={styles.authInputGroup}>
              <label htmlFor="email">{t('auth.email')}</label>
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
                placeholder={t('auth.emailPlaceholder')}
              />
            </div>

            <div className={styles.authInputGroup}>
              <label htmlFor="username">{t('auth.username')}</label>
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
                placeholder={t('auth.usernamePlaceholder')}
              />
            </div>

            <div className={styles.authInputGroup}>
              <label htmlFor="password">{t('auth.password')}</label>
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
                placeholder={t('auth.passwordPlaceholder')}
              />
            </div>

            {error && (
              <p role="alert" className={styles.authError}>
                {error}
              </p>
            )}

            <button type="submit" className={`btn ${styles.authSubmit}`} disabled={loading}>
              {loading ? t('auth.registerSubmitting') : t('auth.registerSubmit')}
            </button>
          </form>

          <p className={styles.authFooter}>
            {t('auth.hasAccount')}{' '}
            <a
              href="/login"
              onClick={(e) => {
                e.preventDefault();
                navigate('/login');
              }}
            >
              {t('auth.goLogin')}
            </a>
          </p>
        </main>
      </div>
    </div>
  );
}
