import React, { createContext, useCallback, useContext, useMemo, useRef, useState, useEffect } from 'react';
import { API_BASE, createApi } from '../lib/api';
import { clearCache } from '../lib/queryCache';

const AuthContext = createContext(null);

const TOKEN_KEY = 'ra_token';
const REFRESH_KEY = 'ra_refresh';
const USER_KEY = 'ra_user';

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  // The live token, readable synchronously. `authFetch` closes over the state
  // value, which is a render behind after a refresh — a request issued in the
  // same tick as a rotation would otherwise still carry the retired token.
  const tokenRef = useRef(null);
  const refreshRef = useRef(null);
  // One in-flight refresh per tab. Several requests failing 401 at once must
  // rotate once: the second rotation would present a token the first already
  // spent, which the server reads as a leak and kills the whole session (1.14).
  const refreshInFlight = useRef(null);

  useEffect(() => {
    const storedToken = sessionStorage.getItem(TOKEN_KEY);
    const storedUser = sessionStorage.getItem(USER_KEY);
    if (storedToken && storedUser) {
      tokenRef.current = storedToken;
      refreshRef.current = sessionStorage.getItem(REFRESH_KEY);
      setToken(storedToken);
      setUser(JSON.parse(storedUser));
    }
    setLoading(false);
  }, []);

  const login = useCallback((tokenVal, userVal, refreshVal) => {
    sessionStorage.setItem(TOKEN_KEY, tokenVal);
    sessionStorage.setItem(USER_KEY, JSON.stringify(userVal));
    if (refreshVal) sessionStorage.setItem(REFRESH_KEY, refreshVal);
    tokenRef.current = tokenVal;
    refreshRef.current = refreshVal || null;
    setToken(tokenVal);
    setUser(userVal);
  }, []);

  const logout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_KEY);
    sessionStorage.removeItem(USER_KEY);
    // The query cache holds this user's drafts, surveys and chats. Signing out
    // without dropping it would hand them to whoever signs in next in this tab.
    clearCache();
    tokenRef.current = null;
    refreshRef.current = null;
    refreshInFlight.current = null;
    setToken(null);
    setUser(null);
  }, []);

  /** Rotate the session. Resolves to the new access token, or null if the
   *  refresh token is gone or rejected — in which case the session is over. */
  const refreshSession = useCallback(async () => {
    if (!refreshRef.current) return null;
    if (refreshInFlight.current) return refreshInFlight.current;

    refreshInFlight.current = (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshRef.current }),
        });
        if (!res.ok) {
          logout();
          return null;
        }
        const body = await res.json();
        tokenRef.current = body.token;
        refreshRef.current = body.refresh_token;
        sessionStorage.setItem(TOKEN_KEY, body.token);
        sessionStorage.setItem(REFRESH_KEY, body.refresh_token);
        if (body.user) {
          sessionStorage.setItem(USER_KEY, JSON.stringify(body.user));
          setUser(body.user);
        }
        setToken(body.token);
        return body.token;
      } catch {
        // A network failure is not proof the session ended — keep the tokens
        // and let the caller surface the original error.
        return null;
      } finally {
        refreshInFlight.current = null;
      }
    })();

    return refreshInFlight.current;
  }, [logout]);

  const authFetch = useCallback(async (url, options = {}) => {
    const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url.startsWith('/') ? '' : '/'}${url}`;
    const isFormData = options.body instanceof FormData;

    const send = (bearer) => {
      const headers = {
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        ...(options.headers || {}),
      };
      if (!isFormData && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
      }
      return fetch(fullUrl, { ...options, headers });
    };

    const response = await send(tokenRef.current);
    // Access tokens are now short-lived, so a 401 is usually "expired", not
    // "signed out". Rotate once and replay; a second 401 is the real answer.
    if (response.status !== 401 || !refreshRef.current) return response;
    // A streamed or FormData body cannot be replayed — it has already been
    // consumed by the first attempt.
    if (isFormData || options.body instanceof ReadableStream) return response;

    const fresh = await refreshSession();
    if (!fresh) return response;
    return send(fresh);
  }, [refreshSession]);

  // One client per token, so pages get parsed JSON and a typed ApiError instead
  // of each rebuilding the same URL and the same res.ok check.
  const api = useMemo(() => createApi(authFetch), [authFetch]);

  const value = useMemo(
    () => ({ user, token, login, logout, authFetch, api, refreshSession, loading }),
    [user, token, login, logout, authFetch, api, refreshSession, loading],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
