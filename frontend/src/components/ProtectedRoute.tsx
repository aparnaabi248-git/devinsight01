import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { useAuth } from '@/context/AuthContext';

/** Blocks a route until the session is known, then redirects anonymous users to /login. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { authenticated, initialising } = useAuth();
  const location = useLocation();

  if (initialising) return <FullPageLoader label="Restoring session…" />;
  if (!authenticated) return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}

export function FullPageLoader({ label = 'Loading…' }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: 'grid',
        placeItems: 'center',
        minHeight: '60vh',
        gap: theme.space(4),
        color: theme.color.textMuted,
      }}
    >
      <div className="spinner" style={{ width: 28, height: 28, borderWidth: 3 }} />
      <span>{label}</span>
    </div>
  );
}

import { theme } from '@/lib/theme';
