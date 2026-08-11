import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

const STORAGE_KEY = 'ra_theme';
const PUBLIC_PATHS = new Set(['/', '/login', '/signup', '/forgot-password', '/reset-password']);

function readStoredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    /* ignore quota / private mode */
  }
  return 'light';
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme;
}

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const { pathname } = useLocation();
  const [theme, setThemeState] = useState(readStoredTheme);
  const isPublic = PUBLIC_PATHS.has(pathname);
  const resolvedTheme = isPublic ? 'light' : theme;

  useEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setTheme = useCallback((next) => {
    const value = next === 'dark' ? 'dark' : 'light';
    setThemeState(value);
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      /* ignore */
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  }, [theme, setTheme]);

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme, toggleTheme, isPublic }),
    [theme, resolvedTheme, setTheme, toggleTheme, isPublic]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
