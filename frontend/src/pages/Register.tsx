/**
 * @file 注册页面
 * @description 用户注册表单页面，注册成功后通过 AuthContext 自动登录。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function Register() {
  const navigate = useNavigate()
  const { register } = useAuth()
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')

    if (!/^[a-zA-Z0-9]+$/.test(username)) {
      setError('用户名只能包含英文字母和数字')
      return
    }

    setLoading(true)

    try {
      await register(email, username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--space-md)',
      }}
    >
      <div className="card" style={{ width: '100%', maxWidth: '400px' }}>
        <h1 style={{ fontSize: '1.5rem', marginBottom: 'var(--space-lg)', textAlign: 'center' }}>
          注册 EngramNote
        </h1>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
          <div>
            <label htmlFor="email" style={{ display: 'block', marginBottom: 'var(--space-xs)', fontWeight: 500 }}>
              邮箱
            </label>
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

          <div>
            <label htmlFor="username" style={{ display: 'block', marginBottom: 'var(--space-xs)', fontWeight: 500 }}>
              用户名
            </label>
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

          <div>
            <label htmlFor="password" style={{ display: 'block', marginBottom: 'var(--space-xs)', fontWeight: 500 }}>
              密码
            </label>
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
            <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem' }}>
              {error}
            </p>
          )}

          <button type="submit" className="btn btn-primary" disabled={loading} style={{ width: '100%' }}>
            {loading ? '注册中...' : '注册'}
          </button>
        </form>

        <p style={{ marginTop: 'var(--space-md)', textAlign: 'center', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          已有账号？{' '}
          <a href="/login" onClick={(e) => { e.preventDefault(); navigate('/login') }}>
            登录
          </a>
        </p>
      </div>
    </div>
  )
}
