import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api, tokenStore } from '@/api/client';
import type { User } from '@/types/api';

export interface AuthState {
  user: User | null;
  initialising: boolean;
  authenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (input: {
    email: string;
    username: string;
    password: string;
    full_name?: string;
  }) => Promise<void>;
  logout: () => void;
}

/** Exported so tests can pin an identity without a real login. */
export const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [initialising, setInitialising] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const token = tokenStore.get();
    if (!token) {
      setInitialising(false);
      return;
    }
    api
      .me()
      .then((profile) => {
        if (!cancelled) setUser(profile);
      })
      .catch(() => {
        // The client already cleared an invalid token.
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setInitialising(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const response = await api.login({ username, password });
    tokenStore.set(response.access_token);
    setUser(response.user);
  }, []);

  const register = useCallback(
    async (input: { email: string; username: string; password: string; full_name?: string }) => {
      const response = await api.register(input);
      tokenStore.set(response.access_token);
      setUser(response.user);
    },
    [],
  );

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, initialising, authenticated: Boolean(user), login, register, logout }),
    [user, initialising, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
