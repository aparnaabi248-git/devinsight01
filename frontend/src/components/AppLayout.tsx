import type { ReactNode } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  Cpu,
  FolderGit2,
  LayoutDashboard,
  LogOut,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react';

import { useAuth } from '@/context/AuthContext';
import { theme } from '@/lib/theme';
import { Badge } from '@/components/ui';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/analyze', label: 'Analyze Repository', icon: FolderGit2, end: false },
  { to: '/analytics', label: 'Analytics', icon: BarChart3, end: false },
  { to: '/contributors', label: 'Contributors', icon: Users, end: false },
  { to: '/predictions', label: 'ML Predictions', icon: Cpu, end: false },
  { to: '/models', label: 'Model Performance', icon: Activity, end: false },
  { to: '/teams', label: 'Teams & Access', icon: ShieldCheck, end: false },
];

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <a href="#main" className="skip-link">
        Skip to main content
      </a>

      {/* ---------------------------------------------------------- sidebar */}
      <aside
        style={{
          width: theme.layout.sidebar,
          flexShrink: 0,
          background: theme.color.bgElevated,
          borderRight: `1px solid ${theme.color.border}`,
          display: 'flex',
          flexDirection: 'column',
          position: 'sticky',
          top: 0,
          height: '100vh',
        }}
      >
        <div
          style={{
            padding: theme.space(5),
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            borderBottom: `1px solid ${theme.color.border}`,
          }}
        >
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 8,
              background: `linear-gradient(135deg, ${theme.color.primary}, #a855f7)`,
              display: 'grid',
              placeItems: 'center',
              flexShrink: 0,
            }}
            aria-hidden
          >
            <Sparkles size={16} color="#fff" />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-0.01em' }}>
              DevInsight
            </div>
            <div style={{ fontSize: 10.5, color: theme.color.textFaint, letterSpacing: '0.04em' }}>
              ENGINEERING INTELLIGENCE
            </div>
          </div>
        </div>

        <nav style={{ padding: theme.space(3), display: 'grid', gap: 2, flex: 1 }} aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              style={({ isActive }) => ({
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: `${theme.space(2.5)} ${theme.space(3)}`,
                borderRadius: theme.radius.md,
                fontSize: 13.5,
                fontWeight: isActive ? 620 : 500,
                color: isActive ? theme.color.text : theme.color.textMuted,
                background: isActive ? theme.color.primarySoft : 'transparent',
                textDecoration: 'none',
                transition: 'background 120ms ease, color 120ms ease',
              })}
            >
              <Icon size={16} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>

        <div style={{ padding: theme.space(3), borderTop: `1px solid ${theme.color.border}` }}>
          {user && (
            <div style={{ marginBottom: theme.space(3), minWidth: 0 }}>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {user.full_name || user.username}
              </div>
              <div style={{ marginTop: 2 }}>
                <Badge tone={user.role === 'admin' ? 'primary' : 'neutral'}>{user.role}</Badge>
              </div>
            </div>
          )}
          <button
            onClick={() => {
              logout();
              navigate('/login');
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              padding: `${theme.space(2)} ${theme.space(3)}`,
              background: 'transparent',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.md,
              color: theme.color.textMuted,
              fontSize: 13,
              fontFamily: 'inherit',
              cursor: 'pointer',
            }}
          >
            <LogOut size={14} aria-hidden /> Sign out
          </button>
        </div>
      </aside>

      {/* ------------------------------------------------------------- main */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header
      style={{
        padding: `${theme.space(6)} ${theme.space(8)} ${theme.space(4)}`,
        borderBottom: `1px solid ${theme.color.border}`,
        display: 'flex',
        alignItems: 'flex-end',
        justifyContent: 'space-between',
        gap: theme.space(4),
        flexWrap: 'wrap',
        background: theme.color.bgElevated,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <h1 style={{ margin: 0, fontSize: 21, fontWeight: 700, letterSpacing: '-0.02em' }}>
          {title}
        </h1>
        {description && (
          <p style={{ margin: `${theme.space(1.5)} 0 0`, color: theme.color.textMuted, fontSize: 13.5, maxWidth: 760 }}>
            {description}
          </p>
        )}
      </div>
      {actions && <div style={{ display: 'flex', gap: theme.space(2), flexShrink: 0 }}>{actions}</div>}
    </header>
  );
}
