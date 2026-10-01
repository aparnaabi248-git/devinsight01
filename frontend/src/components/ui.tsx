import type { ReactNode } from 'react';
import clsx from 'clsx';
import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react';

import { theme } from '@/lib/theme';

export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={clsx('card', className)} style={{ padding: padded ? theme.space(5) : 0 }}>
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: theme.space(4),
        marginBottom: theme.space(4),
        flexWrap: 'wrap',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <h2
          style={{
            margin: 0,
            fontSize: 15,
            fontWeight: 650,
            letterSpacing: '-0.01em',
            color: theme.color.text,
          }}
        >
          {title}
        </h2>
        {subtitle && (
          <p style={{ margin: `${theme.space(1)} 0 0`, fontSize: 12.5, color: theme.color.textMuted }}>
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div style={{ display: 'flex', gap: theme.space(2), flexShrink: 0 }}>{actions}</div>}
    </header>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = 'neutral',
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: 'neutral' | 'good' | 'warning' | 'critical' | 'primary';
  icon?: ReactNode;
}) {
  const tones: Record<string, string> = {
    neutral: theme.color.text,
    good: theme.color.success,
    warning: theme.color.warning,
    critical: theme.color.danger,
    primary: theme.color.primaryHover,
  };
  return (
    <div
      className="card"
      style={{ padding: theme.space(4), display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: theme.color.textMuted }}>
        {icon}
        <span style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>
          {label}
        </span>
      </div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 680,
          letterSpacing: '-0.02em',
          color: tones[tone],
          lineHeight: 1.15,
        }}
      >
        {value}
      </div>
      {hint && <div style={{ fontSize: 12, color: theme.color.textFaint }}>{hint}</div>}
    </div>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'good' | 'warning' | 'critical' | 'info' | 'primary';
  title?: string;
}) {
  const tones: Record<string, { fg: string; bg: string }> = {
    neutral: { fg: theme.color.textMuted, bg: 'rgba(139,151,174,0.12)' },
    good: { fg: theme.color.success, bg: theme.color.successSoft },
    warning: { fg: theme.color.warning, bg: theme.color.warningSoft },
    critical: { fg: theme.color.danger, bg: theme.color.dangerSoft },
    info: { fg: theme.color.info, bg: theme.color.infoSoft },
    primary: { fg: theme.color.primaryHover, bg: theme.color.primarySoft },
  };
  const palette = tones[tone];
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 9px',
        borderRadius: theme.radius.pill,
        background: palette.bg,
        color: palette.fg,
        fontSize: 11.5,
        fontWeight: 600,
        letterSpacing: '0.01em',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = 'primary',
  size = 'md',
  disabled,
  loading,
  type = 'button',
  title,
  full,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  disabled?: boolean;
  loading?: boolean;
  type?: 'button' | 'submit';
  title?: string;
  full?: boolean;
}) {
  const variants: Record<string, React.CSSProperties> = {
    primary: {
      background: theme.color.primary,
      color: '#fff',
      border: '1px solid transparent',
    },
    secondary: {
      background: theme.color.surface,
      color: theme.color.text,
      border: `1px solid ${theme.color.border}`,
    },
    ghost: { background: 'transparent', color: theme.color.textMuted, border: '1px solid transparent' },
    danger: { background: theme.color.danger, color: '#fff', border: '1px solid transparent' },
  };
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || loading}
      style={{
        ...variants[variant],
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 7,
        padding: size === 'sm' ? '6px 12px' : '9px 16px',
        fontSize: size === 'sm' ? 12.5 : 13.5,
        fontWeight: 600,
        borderRadius: theme.radius.md,
        cursor: disabled || loading ? 'not-allowed' : 'pointer',
        opacity: disabled || loading ? 0.6 : 1,
        transition: 'background 120ms ease, transform 80ms ease',
        width: full ? '100%' : undefined,
        fontFamily: 'inherit',
      }}
    >
      {loading && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

export function Input({
  label,
  hint,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {label && (
        <span style={{ fontSize: 12, fontWeight: 600, color: theme.color.textMuted }}>{label}</span>
      )}
      <input
        {...props}
        style={{
          background: theme.color.bgElevated,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          padding: '9px 12px',
          color: theme.color.text,
          fontSize: 13.5,
          fontFamily: 'inherit',
          width: '100%',
          ...props.style,
        }}
      />
      {hint && <span style={{ fontSize: 11.5, color: theme.color.textFaint }}>{hint}</span>}
    </label>
  );
}

export function Textarea({
  label,
  hint,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string; hint?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {label && (
        <span style={{ fontSize: 12, fontWeight: 600, color: theme.color.textMuted }}>{label}</span>
      )}
      <textarea
        {...props}
        style={{
          background: theme.color.bgElevated,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          padding: '9px 12px',
          color: theme.color.text,
          fontSize: 13.5,
          fontFamily: 'inherit',
          minHeight: 96,
          resize: 'vertical',
          width: '100%',
          ...props.style,
        }}
      />
      {hint && <span style={{ fontSize: 11.5, color: theme.color.textFaint }}>{hint}</span>}
    </label>
  );
}

export function Select({
  label,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {label && (
        <span style={{ fontSize: 12, fontWeight: 600, color: theme.color.textMuted }}>{label}</span>
      )}
      <select
        {...props}
        style={{
          background: theme.color.bgElevated,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          padding: '8px 12px',
          color: theme.color.text,
          fontSize: 13.5,
          fontFamily: 'inherit',
          cursor: 'pointer',
        }}
      >
        {children}
      </select>
    </label>
  );
}

// ------------------------------------------------------------- state displays
export function LoadingState({ label = 'Loading data…', rows = 4 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" style={{ display: 'grid', gap: theme.space(3) }}>
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 42 }} />
      ))}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message =
    (error as { detail?: string; message?: string })?.detail ??
    (error as Error)?.message ??
    'Something went wrong.';
  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: theme.space(3),
        padding: theme.space(8),
        textAlign: 'center',
        color: theme.color.textMuted,
      }}
    >
      <AlertTriangle size={28} color={theme.color.danger} aria-hidden />
      <div>
        <div style={{ color: theme.color.text, fontWeight: 600, marginBottom: 4 }}>
          Could not load this data
        </div>
        <div style={{ fontSize: 13, maxWidth: 460 }}>{message}</div>
      </div>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          <RefreshCw size={14} /> Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: theme.space(3),
        padding: theme.space(9),
        textAlign: 'center',
        color: theme.color.textMuted,
      }}
    >
      <div style={{ color: theme.color.textFaint }} aria-hidden>
        {icon ?? <Inbox size={30} />}
      </div>
      <div>
        <div style={{ color: theme.color.text, fontWeight: 620, marginBottom: 4, fontSize: 14.5 }}>
          {title}
        </div>
        {description && (
          <div style={{ fontSize: 13, maxWidth: 440, lineHeight: 1.6 }}>{description}</div>
        )}
      </div>
      {action}
    </div>
  );
}

export function ProgressBar({ value, max = 100, tone }: { value: number; max?: number; tone?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      style={{
        height: 6,
        background: theme.color.bgElevated,
        borderRadius: theme.radius.pill,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          width: `${pct}%`,
          height: '100%',
          background: tone ?? theme.color.primary,
          transition: 'width 400ms ease',
        }}
      />
    </div>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>{children}</table>
    </div>
  );
}

export function Th({ children, align = 'left' }: { children: ReactNode; align?: 'left' | 'right' | 'center' }) {
  return (
    <th
      scope="col"
      style={{
        textAlign: align,
        padding: `${theme.space(2)} ${theme.space(3)}`,
        fontSize: 11,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: theme.color.textFaint,
        fontWeight: 650,
        borderBottom: `1px solid ${theme.color.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = 'left',
  mono,
  colSpan,
  style,
}: {
  children: ReactNode;
  align?: 'left' | 'right' | 'center';
  mono?: boolean;
  colSpan?: number;
  style?: React.CSSProperties;
}) {
  return (
    <td
      colSpan={colSpan}
      style={{
        textAlign: align,
        padding: `${theme.space(2.5)} ${theme.space(3)}`,
        borderBottom: `1px solid ${theme.color.border}`,
        color: theme.color.textMuted,
        fontFamily: mono ? theme.font.mono : undefined,
        fontSize: mono ? 12 : 13,
        verticalAlign: 'middle',
        ...style,
      }}
    >
      {children}
    </td>
  );
}
