/**
 * @file 认证上下文
 * @description 全局认证状态管理，替代 App.tsx 中的 useState。
 * 提供 isAuthenticated 状态和 login/logout/register 方法，
 * 监听 Token 过期事件自动登出。
 */
import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import { getToken, setToken, removeToken, TOKEN_EXPIRED_EVENT, login as apiLogin, register as apiRegister } from '../api/client'

interface AuthContextType {
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!getToken())

  useEffect(() => {
    setIsAuthenticated(!!getToken())
  }, [])

  useEffect(() => {
    const handleTokenExpired = () => setIsAuthenticated(false)
    window.addEventListener(TOKEN_EXPIRED_EVENT, handleTokenExpired)
    return () => window.removeEventListener(TOKEN_EXPIRED_EVENT, handleTokenExpired)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password)
    setToken(res.access_token)
    setIsAuthenticated(true)
  }, [])

  const register = useCallback(async (email: string, username: string, password: string) => {
    const res = await apiRegister(email, username, password)
    setToken(res.access_token)
    setIsAuthenticated(true)
  }, [])

  const logout = useCallback(() => {
    removeToken()
    setIsAuthenticated(false)
  }, [])

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
