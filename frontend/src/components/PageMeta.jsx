import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { NOT_FOUND_META, PAGE_META, SIGNUP_IN_META } from '../constants/pageMeta';

function resolveMeta(pathname, { user, locationState }) {
  if (pathname === '/signup/complete') {
    const signedIn = Boolean(user) || locationState?.mode === 'in';
    if (signedIn) return SIGNUP_IN_META;
    return PAGE_META['/signup/complete'];
  }
  return PAGE_META[pathname] || NOT_FOUND_META;
}

/** Sets a unique document title and meta description for the current route. */
export default function PageMeta() {
  const { pathname, state } = useLocation();
  const { user, loading } = useAuth();
  const { title, description } = resolveMeta(pathname, { user, locationState: state });

  useEffect(() => {
    if (loading && pathname === '/signup/complete') return;
    document.title = title;
    let el = document.querySelector('meta[name="description"]');
    if (!el) {
      el = document.createElement('meta');
      el.setAttribute('name', 'description');
      document.head.appendChild(el);
    }
    el.setAttribute('content', description);
  }, [title, description, loading, pathname]);

  return null;
}
