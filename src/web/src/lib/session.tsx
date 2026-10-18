import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { ApiError, api, errorMessage, setCsrfToken } from './api';
import type { Me } from './types';

interface Session {
  user: Me | null;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
  logout: () => Promise<void>;
}
const SessionContext = createContext<Session | null>(null);
export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setError('');
    try {
      const me = await api<Me>('/api/v1/me', {
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        return;
      }
      setUser(me);
      setCsrfToken(me.csrf_token);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setUser(null);
      setCsrfToken('');
      if (!(error instanceof ApiError && error.status === 401)) {
        setError(errorMessage(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);
  useEffect(() => {
    void reload();
    return () => pending.current?.abort();
  }, [
    reload,
  ]);
  useEffect(() => {
    const expire = () => {
      setUser(null);
      setCsrfToken('');
    };
    window.addEventListener('session-expired', expire);
    return () => window.removeEventListener('session-expired', expire);
  }, []);
  async function logout() {
    await api('/api/v1/auth/logout', {
      method: 'POST',
    });
    setUser(null);
    setCsrfToken('');
  }
  return (
    <SessionContext.Provider
      value={{
        user,
        loading,
        error,
        reload,
        logout,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}
export function useSession() {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error('Session provider missing');
  }
  return value;
}
