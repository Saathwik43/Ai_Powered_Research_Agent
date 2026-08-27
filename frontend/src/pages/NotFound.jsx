import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Home } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import './NotFound.css';

const NotFound = () => {
  const { user } = useAuth();

  return (
    <div className="not-found-page">
      <header className="not-found-nav">
        <Link to={user ? '/dashboard' : '/'} className="not-found-brand" aria-label="Research Agent home">
          <img src="/9672704.webp" alt="" width={32} height={32} />
          <span>
            <strong>Research Agent</strong>
            <small>Publishing workspace</small>
          </span>
        </Link>
      </header>

      <main className="not-found-main">
        <p className="not-found-code" aria-hidden="true">404</p>
        <h1>This page is not in Research Agent</h1>
        <p className="not-found-lede">
          The address does not match a workspace or account page. Check the URL,
          or continue from a page that exists.
        </p>
        <div className="not-found-actions">
          {user ? (
            <>
              <Link to="/dashboard" className="btn btn-primary">
                Open dashboard <ArrowRight size={16} />
              </Link>
              <Link to="/literature-survey" className="btn btn-secondary">
                Literature survey
              </Link>
            </>
          ) : (
            <>
              <Link to="/" className="btn btn-primary">
                <Home size={16} /> Back home
              </Link>
              <Link to="/login" className="btn btn-secondary">
                Sign in
              </Link>
            </>
          )}
        </div>
      </main>
    </div>
  );
};

export default NotFound;
