import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertOctagon,
  Bug,
  GitPullRequest,
  Sparkles,
  Users,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { api } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  ProgressBar,
  StatTile,
} from '@/components/ui';
import { compactNumber, hours, number, percent, periodLabel, relativeTime } from '@/lib/format';
import { defaultRepositoryId } from '@/lib/repositories';
import { theme } from '@/lib/theme';
import type { Repository } from '@/types/api';

const chartTooltip = {
  contentStyle: {
    background: theme.color.surfaceHover,
    border: `1px solid ${theme.color.borderStrong}`,
    borderRadius: theme.radius.md,
    fontSize: 12.5,
    boxShadow: theme.shadow.pop,
  },
  labelStyle: { color: theme.color.text, fontWeight: 600 },
  itemStyle: { color: theme.color.textMuted },
  cursor: { fill: 'rgba(99,102,241,0.06)' },
};

const axisProps = {
  stroke: theme.color.textFaint,
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

export default function DashboardPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const reposQuery = useQuery({
    queryKey: ['repositories', 'dashboard'],
    queryFn: () => api.repositories({ page_size: 100, sort: 'last_synced_at' }),
  });

  const repositories = reposQuery.data?.items ?? [];
  const active = useMemo(() => {
    if (!repositories.length) return null;
    if (selectedId !== null) {
      return repositories.find((r) => r.id === selectedId) ?? repositories[0];
    }
    // Default to the repository with the most history, not the most recently synced
    // one - see defaultRepositoryId() for why, and so the three repository-scoped
    // views agree on what to show first.
    const defaultId = defaultRepositoryId(repositories);
    return repositories.find((r) => r.id === defaultId) ?? repositories[0];
  }, [repositories, selectedId]);

  const analyticsQuery = useQuery({
    queryKey: ['analytics', active?.id],
    queryFn: () => api.analytics(active!.id),
    enabled: Boolean(active?.id),
  });
  const healthQuery = useQuery({
    queryKey: ['health', active?.id],
    queryFn: () => api.health_(active!.id),
    enabled: Boolean(active?.id),
  });
  const trendsQuery = useQuery({
    queryKey: ['trends', active?.id, 'weekly'],
    queryFn: () => api.trends(active!.id, { granularity: 'weekly', limit: 60 }),
    enabled: Boolean(active?.id),
  });
  const modelsQuery = useQuery({ queryKey: ['models'], queryFn: () => api.models() });

  if (reposQuery.isLoading) {
    return (
      <>
        <PageHeader title="Dashboard" description="Loading your repositories…" />
        <div style={{ padding: theme.space(8) }}>
          <LoadingState rows={6} />
        </div>
      </>
    );
  }

  if (reposQuery.isError) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <div style={{ padding: theme.space(8) }}>
          <ErrorState error={reposQuery.error} onRetry={() => reposQuery.refetch()} />
        </div>
      </>
    );
  }

  const metrics = analyticsQuery.data;
  const health = healthQuery.data;
  const trendPoints = trendsQuery.data?.points ?? [];

  return (
    <>
      <PageHeader
        title="Engineering overview"
        description="Real metrics computed from ingested repository history, plus live predictions from the trained models."
        actions={
          repositories.length > 1 ? (
            <select
              value={active?.id ?? ''}
              onChange={(e) => setSelectedId(Number(e.target.value))}
              aria-label="Select repository"
              style={{
                background: theme.color.surface,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                padding: '8px 12px',
                color: theme.color.text,
                fontSize: 13.5,
                fontFamily: 'inherit',
                cursor: 'pointer',
              }}
            >
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          ) : undefined
        }
      />

      <div style={{ padding: theme.space(8), display: 'grid', gap: theme.space(5) }}>
        {!repositories.length && (
          <Card>
            <EmptyState
              icon={<Sparkles size={30} />}
              title="No repositories analysed yet"
              description="Enter a GitHub repository URL to ingest its real commit, issue and pull-request history. DevInsight then computes engineering metrics and runs predictions over them."
              action={
                <Link to="/analyze">
                  <Button>Analyse a repository</Button>
                </Link>
              }
            />
          </Card>
        )}

        {active && metrics && (
          <>
            <div
              style={{
                display: 'grid',
                gap: theme.space(4),
                gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
              }}
            >
              <StatTile
                label="Commits"
                value={compactNumber(metrics.total_commits)}
                hint={`${compactNumber(metrics.commits_last_30d)} in last 30 days`}
                icon={<Activity size={13} />}
                tone="primary"
              />
              <StatTile
                label="Contributors"
                value={compactNumber(metrics.total_contributors)}
                hint={`${compactNumber(metrics.active_contributors_last_90d)} active in 90 days`}
                icon={<Users size={13} />}
              />
              <StatTile
                label="Open issues"
                value={compactNumber(metrics.open_issues)}
                hint={`${percent(metrics.issue_closure_rate)} closure rate`}
                icon={<Bug size={13} />}
                tone={metrics.issue_closure_rate >= 70 ? 'good' : 'warning'}
              />
              <StatTile
                label="Pull requests"
                value={compactNumber(metrics.total_pull_requests)}
                hint={`${percent(metrics.pr_merge_rate)} merged`}
                icon={<GitPullRequest size={13} />}
              />
              <StatTile
                label="Avg resolution"
                value={hours(metrics.median_issue_resolution_hours)}
                hint={`mean ${hours(metrics.average_issue_resolution_hours)}`}
              />
              <StatTile
                label="Health score"
                value={health ? `${health.overall_score.toFixed(0)}/100` : '—'}
                hint={health ? health.status.toUpperCase() : 'computing…'}
                tone={
                  health?.status === 'good' ? 'good' : health?.status === 'warning' ? 'warning' : 'critical'
                }
              />
            </div>

            <div
              style={{
                display: 'grid',
                gap: theme.space(5),
                gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)',
              }}
            >
              <Card>
                <CardHeader
                  title="Activity trend"
                  subtitle="Weekly commits, issues and pull requests from ingested history"
                />
                {trendsQuery.isLoading ? (
                  <LoadingState rows={3} />
                ) : trendPoints.length ? (
                  <ResponsiveContainer width="100%" height={280}>
                    <AreaChart data={trendPoints} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                      <defs>
                        {['commits', 'issues_opened', 'prs_merged'].map((key, i) => (
                          <linearGradient key={key} id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={theme.color.chart[i]} stopOpacity={0.35} />
                            <stop offset="100%" stopColor={theme.color.chart[i]} stopOpacity={0} />
                          </linearGradient>
                        ))}
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        dataKey="period"
                        {...axisProps}
                        tickFormatter={(p: string) => periodLabel(p, 'weekly')}
                        minTickGap={24}
                      />
                      <YAxis {...axisProps} allowDecimals={false} />
                      <Tooltip
                        {...chartTooltip}
                        labelFormatter={(p) => periodLabel(String(p), 'weekly')}
                      />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Area
                        type="monotone"
                        dataKey="commits"
                        name="Commits"
                        stroke={theme.color.chart[0]}
                        fill="url(#grad-commits)"
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="issues_opened"
                        name="Issues opened"
                        stroke={theme.color.chart[1]}
                        fill="url(#grad-issues_opened)"
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="prs_merged"
                        name="PRs merged"
                        stroke={theme.color.chart[2]}
                        fill="url(#grad-prs_merged)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState title="No trend data yet" description="Ingest commit history to populate this chart." />
                )}
              </Card>

              <Card>
                <CardHeader title="Project health" subtitle="Composite engineering indicators" />
                {health ? (
                  <div style={{ display: 'grid', gap: theme.space(4) }}>
                    {health.indicators.map((indicator) => (
                      <div key={indicator.name} style={{ display: 'grid', gap: 6 }}>
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'baseline',
                            gap: theme.space(2),
                          }}
                        >
                          <span style={{ fontSize: 13, fontWeight: 600 }}>{indicator.name}</span>
                          <Badge
                            tone={
                              indicator.status === 'good'
                                ? 'good'
                                : indicator.status === 'warning'
                                  ? 'warning'
                                  : 'critical'
                            }
                          >
                            {indicator.score.toFixed(0)}
                          </Badge>
                        </div>
                        <ProgressBar
                          value={indicator.score}
                          tone={
                            indicator.status === 'good'
                              ? theme.color.success
                              : indicator.status === 'warning'
                                ? theme.color.warning
                                : theme.color.danger
                          }
                        />
                        <span style={{ fontSize: 11.5, color: theme.color.textFaint }}>
                          {indicator.detail}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <LoadingState rows={4} />
                )}
              </Card>
            </div>

            <div
              style={{
                display: 'grid',
                gap: theme.space(5),
                gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
              }}
            >
              <Card>
                <CardHeader
                  title="Issue categories"
                  subtitle="Model-assigned labels across the ingested backlog"
                />
                {metrics.category_distribution.length ? (
                  <ResponsiveContainer width="100%" height={230}>
                    <PieChart>
                      <Pie
                        data={metrics.category_distribution}
                        dataKey="count"
                        nameKey="category"
                        innerRadius={54}
                        outerRadius={86}
                        paddingAngle={2}
                      >
                        {metrics.category_distribution.map((_, i) => (
                          <Cell key={i} fill={theme.color.chart[i % theme.color.chart.length]} />
                        ))}
                      </Pie>
                      <Tooltip {...chartTooltip} />
                      <Legend wrapperStyle={{ fontSize: 11.5 }} />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState
                    title="No categories yet"
                    description="Run ML classification from the Predictions page to label the backlog."
                  />
                )}
              </Card>

              <Card>
                <CardHeader title="Top labels" subtitle="Most used labels on real issues" />
                {metrics.label_distribution.length ? (
                  <ResponsiveContainer width="100%" height={230}>
                    <BarChart
                      data={metrics.label_distribution.slice(0, 8)}
                      layout="vertical"
                      margin={{ top: 4, right: 16, left: 8, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" {...axisProps} allowDecimals={false} />
                      <YAxis type="category" dataKey="name" width={96} {...axisProps} />
                      <Tooltip {...chartTooltip} />
                      <Bar dataKey="count" name="Issues" fill={theme.color.chart[0]} radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState title="No labels ingested" description="Issues carry labels once synced." />
                )}
              </Card>

              <Card>
                <CardHeader title="Code churn" subtitle="Where the change concentrates" />
                <div style={{ display: 'grid', gap: theme.space(2.5) }}>
                  {metrics.most_changed_files.slice(0, 8).map((file) => (
                    <div key={file.path} style={{ display: 'grid', gap: 3 }}>
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          fontSize: 12,
                          fontFamily: theme.font.mono,
                          gap: theme.space(2),
                        }}
                      >
                        <span
                          style={{
                            color: theme.color.textMuted,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                          title={file.path}
                        >
                          {file.path}
                        </span>
                        <span style={{ color: theme.color.textFaint, flexShrink: 0 }}>
                          {number(file.changes)}
                        </span>
                      </div>
                      <ProgressBar value={file.changes} max={metrics.most_changed_files[0]?.changes || 1} />
                    </div>
                  ))}
                  {!metrics.most_changed_files.length && (
                    <EmptyState title="No file history" description="Ingest commits to see churn hotspots." />
                  )}
                </div>
              </Card>
            </div>
          </>
        )}

        <Card>
          <CardHeader
            title="Model registry"
            subtitle="Trained models serving this platform, with their measured test metrics"
            actions={
              <Link to="/models">
                <Button size="sm" variant="secondary">
                  View performance
                </Button>
              </Link>
            }
          />
          {modelsQuery.isLoading ? (
            <LoadingState rows={3} />
          ) : modelsQuery.data?.items.length ? (
            <div
              style={{
                display: 'grid',
                gap: theme.space(4),
                gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
              }}
            >
              {modelsQuery.data.items.map((model) => {
                const m = model.metrics as Record<string, number>;
                const headline =
                  m.f1_macro !== undefined
                    ? `F1-macro ${m.f1_macro.toFixed(3)}`
                    : m.mae !== undefined
                      ? `MAE ${m.mae.toFixed(1)} h`
                      : '—';
                return (
                  <div
                    key={model.tag}
                    style={{
                      border: `1px solid ${theme.color.border}`,
                      borderRadius: theme.radius.md,
                      padding: theme.space(4),
                      display: 'grid',
                      gap: 6,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong style={{ fontSize: 13 }}>{model.name}</strong>
                      <Badge tone={model.is_active ? 'good' : 'neutral'}>
                        {model.is_active ? 'active' : `v${model.version}`}
                      </Badge>
                    </div>
                    <div className="mono" style={{ fontSize: 11.5, color: theme.color.textFaint }}>
                      {model.algorithm}
                    </div>
                    <div style={{ fontSize: 12.5, color: theme.color.textMuted }}>
                      {headline}
                    </div>
                    <div style={{ fontSize: 11.5, color: theme.color.textFaint }}>
                      trained {relativeTime(model.trained_at)}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState
              icon={<AlertOctagon size={28} />}
              title="No trained models found"
              description="Run `python ml/training/run_all.py` to train the four DevInsight models."
            />
          )}
        </Card>

        {active && (
          <div style={{ fontSize: 12, color: theme.color.textFaint, textAlign: 'center' }}>
            Last synced {relativeTime(active.last_synced_at)} ·{' '}
            <a href={active.html_url} target="_blank" rel="noreferrer" style={{ color: theme.color.textMuted }}>
              {active.full_name} on GitHub ↗
            </a>
          </div>
        )}
      </div>
    </>
  );
}

export type { Repository };
