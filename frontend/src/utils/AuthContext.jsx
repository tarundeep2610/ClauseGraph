import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { getUser, getRole, logout } from './authService';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const u = await getUser();
    setUser(u);
    setLoading(false);
    return u;
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const role = getRole(user);
  const isAdmin = role === 'admin';

  return (
    <AuthContext.Provider value={{ user, loading, role, isAdmin, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
