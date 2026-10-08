import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { clearPersistedFilters } from "./persist";
import { api, getToken, setToken, type User } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (...roles: string[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await api<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    clearPersistedFilters();
    setToken(access_token);
    setUser(await api<User>("/auth/me"));
  }, []);

  const logout = useCallback(() => {
    clearPersistedFilters();
    setToken(null);
    setUser(null);
  }, []);

  const can = useCallback(
    (...roles: string[]) => Boolean(user && (user.roles.includes("ADMIN") || roles.some((r) => user.roles.includes(r)))),
    [user],
  );

  return <AuthContext.Provider value={{ user, loading, login, logout, can }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fora do AuthProvider");
  return ctx;
}
