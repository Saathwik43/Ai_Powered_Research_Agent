import React, { createContext, useCallback, useContext, useMemo, useState, useEffect } from 'react';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const storedToken = sessionStorage.getItem('ra_token');
    const storedUser = sessionStorage.getItem('ra_user');
    if (storedToken && storedUser) {
      setToken(storedToken);
      setUser(JSON.parse(storedUser));
    }
    setLoading(false);
  }, []);

  const login = useCallback((tokenVal, userVal) => {
    sessionStorage.setItem('ra_token', tokenVal);
    sessionStorage.setItem('ra_user', JSON.stringify(userVal));
    setToken(tokenVal);
    setUser(userVal);
  }, []);

  const logout = useCallback(() => {
    sessionStorage.removeItem('ra_token');
    sessionStorage.removeItem('ra_user');
    setToken(null);
    setUser(null);
  }, []);

  const authFetch = useCallback(async (url, options = {}) => {
    const isFormData = options.body instanceof FormData;
    const headers = {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    };

    if (!isFormData && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    return fetch(url, { ...options, headers });
  }, [token]);

  const value = useMemo(() => ({ user, token, login, logout, authFetch, loading }), [user, token, login, logout, authFetch, loading]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
