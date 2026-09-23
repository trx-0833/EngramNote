/**
 * @file 认证上下文
 * @description 全局认证状态管理，替代 App.tsx 中的 useState。
 * 提供 isAuthenticated 状态和 login/logout/register 方法，
 * 监听 Token 过期事件自动登出。
 *
 * 阶段 6.3 起令牌是**一对**（访问 + 刷新）：刷新令牌交给服务端撤销，
 * 因此 `logout` 变成异步（先尽力撤销服务端会话，再清本地状态）。
 */
import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import {
  getToken,
  setTokens,
  clearTokens,
  getRefreshToken,
  TOKEN_EXPIRED_EVENT,
  login as apiLogin,
  register as apiRegister,
  logout as apiLogout,
} from '../api/client';

interface AuthContextType {
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, username: string, password: string) => Promise<void>;
  /** 登出：尽力撤销服务端刷新令牌，然后必定清除本地令牌 */
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!getToken());

  useEffect(() => {
    const handleTokenExpired = () => setIsAuthenticated(false);
    window.addEventListener(TOKEN_EXPIRED_EVENT, handleTokenExpired);
    return () => window.removeEventListener(TOKEN_EXPIRED_EVENT, handleTokenExpired);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    setTokens(res.access_token, res.refresh_token);
    setIsAuthenticated(true);
  }, []);

  const register = useCallback(async (email: string, username: string, password: string) => {
    const res = await apiRegister(email, username, password);
    setTokens(res.access_token, res.refresh_token);
    setIsAuthenticated(true);
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = getRefreshToken();
    try {
      // 先撤销服务端状态（否则那枚刷新令牌还能被用来续期，登出等于只清了个 UI）
      await apiLogout(refreshToken);
    } catch {
      // ⚠️ 刻意吞掉异常：网络不通/服务端 5xx 时本地**仍必须**登出。
      // 把"撤销失败"变成"登不出去"会让用户在一个自己已经不想用的会话里被卡住，
      // 而他能做的补救（清本地令牌）恰好被这段异常挡掉了。
      // 代价是服务端可能残留一枚未撤销的刷新令牌 —— 它会在 30 天后自然过期，
      // 且用户下次登录可以显式选择"退出所有设备"。
    } finally {
      clearTokens();
      setIsAuthenticated(false);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
