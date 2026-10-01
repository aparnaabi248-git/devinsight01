/**
 * DevInsight design tokens.
 * A single dark developer-tool theme: near-black surfaces, one indigo accent, semantic
 * colours reserved for state so they always mean the same thing across the app.
 */
export const theme = {
  color: {
    bg: '#080b12',
    bgElevated: '#0e131d',
    surface: '#131926',
    surfaceHover: '#1a2131',
    border: '#222a3a',
    borderStrong: '#2e3950',

    text: '#e6ebf5',
    textMuted: '#8b97ae',
    textFaint: '#5d6880',

    primary: '#6366f1',
    primaryHover: '#818cf8',
    primarySoft: 'rgba(99, 102, 241, 0.14)',

    success: '#22c55e',
    successSoft: 'rgba(34, 197, 94, 0.14)',
    warning: '#f59e0b',
    warningSoft: 'rgba(245, 158, 11, 0.14)',
    danger: '#ef4444',
    dangerSoft: 'rgba(239, 68, 68, 0.14)',
    info: '#38bdf8',
    infoSoft: 'rgba(56, 189, 248, 0.14)',

    chart: ['#6366f1', '#22c55e', '#f59e0b', '#38bdf8', '#ec4899', '#a855f7', '#14b8a6'],
  },
  radius: { sm: '6px', md: '10px', lg: '14px', pill: '999px' },
  space: (n: number) => `${n * 4}px`,
  shadow: {
    card: '0 1px 2px rgba(0,0,0,0.4), 0 8px 24px rgba(0,0,0,0.28)',
    pop: '0 12px 40px rgba(0,0,0,0.55)',
  },
  font: {
    mono: "'JetBrains Mono', 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace",
    sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  },
  layout: { sidebar: '248px', header: '60px', maxWidth: '1560px' },
} as const;

export type RiskTone = 'low' | 'medium' | 'high';
export type StatusTone = 'good' | 'warning' | 'critical' | 'neutral';

export const riskTone = (level: string): RiskTone => {
  const v = level.toUpperCase();
  if (v === 'HIGH' || v === 'CRITICAL') return 'high';
  if (v === 'MEDIUM') return 'medium';
  return 'low';
};

export const toneColors: Record<StatusTone, { fg: string; bg: string; border: string }> = {
  good: { fg: theme.color.success, bg: theme.color.successSoft, border: 'transparent' },
  warning: { fg: theme.color.warning, bg: theme.color.warningSoft, border: 'transparent' },
  critical: { fg: theme.color.danger, bg: theme.color.dangerSoft, border: 'transparent' },
  neutral: { fg: theme.color.textMuted, bg: 'rgba(139,151,174,0.12)', border: 'transparent' },
};
