import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Users } from 'lucide-react';

import { api } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  ProgressBar,
  StatTile,
  Table,
  Td,
  Th,
} from '@/components/ui';
import { compactNumber, number, relativeTime, shortDate } from '@/lib/format';
import { theme } from '@/lib/theme';

export default function ContributorsPage() {
  const [params, setParams] = useSearchParams();
  const reposQuery = useQuery({
    queryKey: ['repositories', 'contributors'],
    queryFn: () => api.repositories({ page_size: 100 }),
  });
  const repoId = params.get('repo') ?? String(reposQuery.data?.items[0]?.id ?? '');

  const contributorsQuery = useQuery({
    queryKey: ['contributors', repoId],
    queryFn: () => api.contributors(repoId, { page_size: 100 }),
    enabled: Boolean(repoId),
  });
  const analyticsQuery = useQuery({
    queryKey: ['analytics', repoId],
    queryFn: () => api.analytics(repoId),
    enabled: Boolean(repoId),
  });

  const rows = contributorsQuery.data?.items ?? [];
  const topChurn = rows[0]?.total_churn ?? 1;

  if (reposQuery.isError) {
    return (
      <>
        <PageHeader title="Contributors" />
        <div style={{ padding: theme.space(8) }}>
          <ErrorState error={reposQuery.error} onRetry={() => reposQuery.refetch()} />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Contributors"
        description="Per-developer engineering metrics aggregated from the real commit history: commits, code churn, pull-request throughput and issue participation."
        actions={
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
        }
      />

      <div style={{ padding: theme.space(8), display: 'grid', gap: theme.space(5) }}>
        {reposQuery.isLoading ? (
          <LoadingState rows={6} />
        ) : !reposQuery.data?.items.length ? (
          <Card>
            <EmptyState icon={<Users size={30} />} title="No repositories yet" description="Ingest a repository to see contributor analytics." />
          </Card>
        ) : (
          <>
            {analyticsQuery.data && (
              <div
                style={{
                  display: 'grid',
                  gap: theme.space(4),
                  gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
                }}
              >
                <StatTile label="Contributors" value={number(analyticsQuery.data.total_contributors)} />
                <StatTile label="Active (90d)" value={number(analyticsQuery.data.active_contributors_last_90d)} tone="primary" />
                <StatTile
                  label="Bus factor risk"
                  value={
                    analyticsQuery.data.total_contributors <= 2
                      ? 'High'
                      : analyticsQuery.data.total_contributors <= 5
                        ? 'Medium'
                        : 'Low'
                  }
                  tone={
                    analyticsQuery.data.total_contributors <= 2
                      ? 'critical'
                      : analyticsQuery.data.total_contributors <= 5
                        ? 'warning'
                        : 'good'
                  }
                  hint="based on contributor breadth"
                />
                <StatTile label="Total churn" value={compactNumber(analyticsQuery.data.total_churn)} />
              </div>
            )}

            <Card padded={false}>
              <div style={{ padding: theme.space(5) }}>
                <CardHeader
                  title="Contributor leaderboard"
                  subtitle={`${number(contributorsQuery.data?.total ?? 0)} contributors, sorted by commits`}
                />
              </div>
              {contributorsQuery.isLoading ? (
                <div style={{ padding: theme.space(5) }}>
                  <LoadingState rows={6} />
                </div>
              ) : rows.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>#</Th>
                      <Th>Contributor</Th>
                      <Th align="right">Commits</Th>
                      <Th align="right">Churn</Th>
                      <Th>Share of churn</Th>
                      <Th align="right">PRs merged</Th>
                      <Th align="right">Issues</Th>
                      <Th>Last commit</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((c, index) => (
                      <tr key={c.github_login}>
                        <Td align="right" mono>
                          {index + 1}
                        </Td>
                        <Td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                            <Avatar login={c.github_login} url={c.avatar_url} />
                            <div style={{ minWidth: 0 }}>
                              <div style={{ color: theme.color.text, fontWeight: 600 }}>
                                {c.display_name || c.github_login}
                              </div>
                              <div style={{ fontSize: 11.5, color: theme.color.textFaint }}>
                                @{c.github_login}
                              </div>
                            </div>
                          </div>
                        </Td>
                        <Td align="right">{number(c.commits_count)}</Td>
                        <Td align="right">
                          <span style={{ color: theme.color.success }}>+{compactNumber(c.additions)}</span>{' '}
                          <span style={{ color: theme.color.danger }}>−{compactNumber(c.deletions)}</span>
                        </Td>
                        <Td>
                          <div style={{ paddingRight: 8 }}>
                            <ProgressBar
                              value={c.total_churn}
                              max={topChurn}
                              tone={index < 3 ? theme.color.primary : theme.color.info}
                            />
                          </div>
                        </Td>
                        <Td align="right">{number(c.prs_merged)}</Td>
                        <Td align="right">{number(c.issues_opened)}</Td>
                        <Td>
                          <span style={{ fontSize: 12, color: theme.color.textFaint }}>
                            {relativeTime(c.last_commit_at)}
                          </span>
                          {c.first_commit_at && (
                            <div style={{ fontSize: 10.5, color: theme.color.textFaint }}>
                              since {shortDate(c.first_commit_at)}
                            </div>
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No contributor data" description="Ingest commit history to populate this table." />
              )}
            </Card>
          </>
        )}
      </div>
    </>
  );
}

export function Avatar({ login, url }: { login: string; url?: string | null }) {
  const initial = login.charAt(0).toUpperCase();
  return url ? (
    <img
      src={url}
      alt=""
      width={28}
      height={28}
      style={{ borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }}
      loading="lazy"
    />
  ) : (
    <div
      aria-hidden
      style={{
        width: 28,
        height: 28,
        borderRadius: '50%',
        background: theme.color.primarySoft,
        color: theme.color.primaryHover,
        display: 'grid',
        placeItems: 'center',
        fontSize: 12,
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      {initial}
    </div>
  );
}
