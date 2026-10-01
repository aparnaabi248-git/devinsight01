import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { api } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Select,
  StatTile,
  Table,
  Td,
  Th,
} from '@/components/ui';
import { hours, number, percent, periodLabel, shortDate, truncate } from '@/lib/format';
import { theme } from '@/lib/theme';
import type { Granularity } from '@/types/api';

const SERIES = [
  { key: 'commits', label: 'Commits', color: theme.color.chart[0] },
  { key: 'issues_opened', label: 'Issues opened', color: theme.color.chart[1] },
  { key: 'issues_closed', label: 'Issues closed', color: theme.color.chart[3] },
  { key: 'prs_merged', label: 'PRs merged', color: theme.color.chart[2] },
  { key: 'bug_fixes', label: 'Bug fixes', color: theme.color.chart[4] },
  { key: 'releases', label: 'Releases', color: theme.color.chart[5] },
] as const;

const tooltipStyle = {
  contentStyle: {
    background: theme.color.surfaceHover,
    border: `1px solid ${theme.color.borderStrong}`,
    borderRadius: theme.radius.md,
    fontSize: 12.5,
    boxShadow: theme.shadow.pop,
  },
  labelStyle: { color: theme.color.text, fontWeight: 600 },
  cursor: { fill: 'rgba(99,102,241,0.06)' },
};

export default function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const [granularity, setGranularity] = useState<Granularity>(
    (params.get('granularity') as Granularity) ?? 'weekly',
  );

  const reposQuery = useQuery({
    queryKey: ['repositories', 'analytics'],
    queryFn: () => api.repositories({ page_size: 100 }),
  });
  const repoId = params.get('repo') ?? String(reposQuery.data?.items[0]?.id ?? '');

  const analyticsQuery = useQuery({
    queryKey: ['analytics', repoId],
    queryFn: () => api.analytics(repoId),
    enabled: Boolean(repoId),
  });
  const trendsQuery = useQuery({
    queryKey: ['trends', repoId, granularity],
    queryFn: () => api.trends(repoId, { granularity, limit: 200 }),
    enabled: Boolean(repoId),
  });
  const commitsQuery = useQuery({
    queryKey: ['commits', repoId],
    queryFn: () => api.commits(repoId, { page_size: 12 }),
    enabled: Boolean(repoId),
  });
  const prsQuery = useQuery({
    queryKey: ['prs', repoId],
    queryFn: () => api.pullRequests(repoId, { page_size: 12 }),
    enabled: Boolean(repoId),
  });
  const releasesQuery = useQuery({
    queryKey: ['releases', repoId],
    queryFn: () => api.releases(repoId),
    enabled: Boolean(repoId),
  });

  const repo = useMemo(
    () => reposQuery.data?.items.find((r) => String(r.id) === repoId),
    [reposQuery.data, repoId],
  );

  const setGranularityValue = (value: Granularity) => {
    setGranularity(value);
    const next = new URLSearchParams(params);
    next.set('granularity', value);
    setParams(next, { replace: true });
  };

  if (reposQuery.isLoading) {
    return (
      <>
        <PageHeader title="Analytics" />
        <div style={{ padding: theme.space(8) }}>
          <LoadingState rows={6} />
        </div>
      </>
    );
  }

  if (reposQuery.isError) {
    return (
      <>
        <PageHeader title="Analytics" />
        <div style={{ padding: theme.space(8) }}>
          <ErrorState error={reposQuery.error} onRetry={() => reposQuery.refetch()} />
        </div>
      </>
    );
  }

  const m = analyticsQuery.data;

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Trends computed in PostgreSQL from the ingested fact tables — commits, issues, pull requests, releases and contributor activity."
        actions={
          <>
            <select
              value={repoId}
              onChange={(e) => {
                const next = new URLSearchParams(params);
                next.set('repo', e.target.value);
                setParams(next, { replace: true });
              }}
              aria-label="Repository"
              style={{
                background: theme.color.surface,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                padding: '8px 12px',
                color: theme.color.text,
                fontSize: 13.5,
                fontFamily: 'inherit',
                cursor: 'pointer',
                maxWidth: 260,
              }}
            >
              {reposQuery.data?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                </option>
              ))}
            </select>
            <div style={{ width: 150 }}>
              <Select
                value={granularity}
                onChange={(e) => setGranularityValue(e.target.value as Granularity)}
                aria-label="Granularity"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </Select>
            </div>
          </>
        }
      />

      <div style={{ padding: theme.space(8), display: 'grid', gap: theme.space(5) }}>
        {!reposQuery.data?.items.length && (
          <Card>
            <EmptyState title="Nothing to analyse" description="Ingest a repository first." />
          </Card>
        )}

        {m && repo && (
          <>
            <div
              style={{
                display: 'grid',
                gap: theme.space(4),
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              }}
            >
              <StatTile label="Commits" value={number(m.total_commits)} tone="primary" />
              <StatTile label="Churn" value={number(m.total_churn)} hint="lines added + deleted" />
              <StatTile label="Commits / week" value={m.commit_frequency_per_week.toFixed(1)} />
              <StatTile
                label="Release cadence"
                value={`${m.release_frequency_per_month.toFixed(1)}/mo`}
              />
              <StatTile
                label="Issue closure"
                value={percent(m.issue_closure_rate)}
                tone={m.issue_closure_rate >= 70 ? 'good' : 'warning'}
              />
              <StatTile label="PR merge rate" value={percent(m.pr_merge_rate)} />
              <StatTile label="Avg PR size" value={number(m.average_pr_size)} hint={`${m.average_pr_size_files.toFixed(1)} files`} />
              <StatTile
                label="Bug-fix ratio"
                value={percent(m.bugfix_ratio)}
                tone={m.bugfix_ratio < 15 ? 'good' : 'warning'}
                hint={`${number(m.bugfix_commits)} fix commits`}
              />
            </div>

            <Card>
              <CardHeader
                title="Activity over time"
                subtitle={`${granularity} buckets from ${repo.full_name} history`}
              />
              {trendsQuery.isLoading ? (
                <LoadingState rows={4} />
              ) : trendsQuery.data?.points.length ? (
                <ResponsiveContainer width="100%" height={340}>
                  <LineChart data={trendsQuery.data.points} margin={{ top: 6, right: 12, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis
                      dataKey="period"
                      stroke={theme.color.textFaint}
                      fontSize={11}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(p: string) => periodLabel(p, granularity)}
                      minTickGap={28}
                    />
                    <YAxis stroke={theme.color.textFaint} fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip {...tooltipStyle} labelFormatter={(p) => periodLabel(String(p), granularity)} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    {SERIES.map((s) => (
                      <Line
                        key={s.key}
                        type="monotone"
                        dataKey={s.key}
                        name={s.label}
                        stroke={s.color}
                        strokeWidth={2}
                        dot={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <EmptyState title="No trend data" description="Ingest history to populate trends." />
              )}
            </Card>

            <div
              style={{
                display: 'grid',
                gap: theme.space(5),
                gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))',
              }}
            >
              <Card>
                <CardHeader title="Velocity" subtitle="Cadence and throughput" />
                <Table>
                  <tbody>
                    <MetricRow label="Commits per month" value={m.commit_frequency_per_month.toFixed(1)} />
                    <MetricRow label="Active contributors (90d)" value={number(m.active_contributors_last_90d)} />
                    <MetricRow label="Days since last commit" value={m.days_since_last_commit ?? '—'} />
                    <MetricRow label="Avg merge duration" value={hours(m.average_merge_duration_hours)} />
                    <MetricRow label="Median issue resolution" value={hours(m.median_issue_resolution_hours)} />
                    <MetricRow label="Open / closed issues" value={`${number(m.open_issues)} / ${number(m.closed_issues)}`} />
                    <MetricRow label="Open / merged PRs" value={`${number(m.open_pull_requests)} / ${number(m.merged_pull_requests)}`} />
                  </tbody>
                </Table>
              </Card>

              <Card>
                <CardHeader title="Releases" subtitle="From real tags and GitHub releases" />
                <div style={{ display: 'grid', gap: theme.space(2), maxHeight: 320, overflowY: 'auto' }}>
                  {releasesQuery.data?.length ? (
                    releasesQuery.data.slice(0, 40).map((release) => (
                      <div
                        key={release.tag_name}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          gap: theme.space(3),
                          padding: '7px 0',
                          borderBottom: `1px solid ${theme.color.border}`,
                        }}
                      >
                        <span className="mono" style={{ fontSize: 12.5, color: theme.color.text }}>
                          {release.tag_name}
                        </span>
                        <span style={{ fontSize: 11.5, color: theme.color.textFaint, flexShrink: 0 }}>
                          {shortDate(release.published_at)}
                        </span>
                      </div>
                    ))
                  ) : (
                    <EmptyState title="No releases ingested" />
                  )}
                </div>
              </Card>
            </div>

            <div
              style={{
                display: 'grid',
                gap: theme.space(5),
                gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
              }}
            >
              <Card padded={false}>
                <div style={{ padding: theme.space(5) }}>
                  <CardHeader title="Recent commits" subtitle="Latest ingested changes" />
                </div>
                <Table>
                  <tbody>
                    {commitsQuery.data?.items.map((commit) => (
                      <tr key={commit.sha}>
                        <Td mono>{commit.sha.slice(0, 7)}</Td>
                        <Td>{truncate(commit.message, 62)}</Td>
                        <Td align="right">
                          <span style={{ color: theme.color.success }}>+{commit.additions}</span>{' '}
                          <span style={{ color: theme.color.danger }}>−{commit.deletions}</span>
                        </Td>
                        <Td>
                          {commit.is_bugfix ? <Badge tone="warning">fix</Badge> : null}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>

              <Card padded={false}>
                <div style={{ padding: theme.space(5) }}>
                  <CardHeader title="Recent pull requests" subtitle="Size and merge outcome" />
                </div>
                <Table>
                  <thead>
                    <tr>
                      <Th>#</Th>
                      <Th>Title</Th>
                      <Th align="right">Size</Th>
                      <Th>State</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {prsQuery.data?.items.map((pr) => (
                      <tr key={pr.number}>
                        <Td mono>{pr.number}</Td>
                        <Td>{truncate(pr.title, 52)}</Td>
                        <Td align="right">{number(pr.size)}</Td>
                        <Td>
                          <Badge tone={pr.merged ? 'good' : pr.state === 'open' ? 'warning' : 'neutral'}>
                            {pr.state}
                          </Badge>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function MetricRow({ label, value }: { label: string; value: string | number }) {
  return (
    <tr>
      <td style={{ padding: '8px 0', fontSize: 13, color: theme.color.textMuted, borderBottom: `1px solid ${theme.color.border}` }}>
        {label}
      </td>
      <td
        style={{
          padding: '8px 0',
          textAlign: 'right',
          fontSize: 13.5,
          fontWeight: 620,
          color: theme.color.text,
          borderBottom: `1px solid ${theme.color.border}`,
        }}
      >
        {value}
      </td>
    </tr>
  );
}
