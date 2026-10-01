import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { CheckCircle2, FolderGit2, Search, XCircle } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import { useAuth } from '@/context/AuthContext';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Input,
  StatTile,
  Table,
  Td,
  Th,
} from '@/components/ui';
import { number, relativeTime, truncate } from '@/lib/format';
import { theme } from '@/lib/theme';
import type { Job, SyncStatus } from '@/types/api';

const STATUS_TONE: Record<SyncStatus, 'good' | 'warning' | 'neutral' | 'critical'> = {
  completed: 'good',
  running: 'warning',
  pending: 'neutral',
  failed: 'critical',
};

export default function AnalyzePage() {
  const [url, setUrl] = useState('https://github.com/pallets/click');
  const [maxCommits, setMaxCommits] = useState(1500);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const analyze = useMutation({
    mutationFn: () =>
      api.analyze({ url: url.trim(), max_commits: maxCommits, max_issues: 600 }),
    onSuccess: (data) => {
      toast.success(`${data.full_name}: ${data.message}`);
      queryClient.invalidateQueries({ queryKey: ['repositories'] });
      if (data.job_id) queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (error) => {
      const message =
        error instanceof ApiError ? error.detail : 'Could not start the analysis.';
      toast.error(message);
    },
  });

  const reposQuery = useQuery({
    queryKey: ['repositories', 'analyze'],
    queryFn: () => api.repositories({ page_size: 50, sort: 'created_at' }),
    // Poll while any repository is still ingesting.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => r.sync_status === 'running' ||
        r.sync_status === 'pending')
        ? 4000
        : false,
  });

  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.jobs({ page_size: 10 }),
    refetchInterval: 5000,
  });

  return (
    <>
      <PageHeader
        title="Analyse a repository"
        description="Paste a GitHub repository URL. DevInsight ingests real commits, pull requests, issues, contributors and releases through the GitHub API, then computes engineering metrics and runs the trained models over the result."
      />

      <div style={{ padding: theme.space(8), display: 'grid', gap: theme.space(5), maxWidth: 1120 }}>
        <Card>
          <CardHeader
            title="New analysis"
            subtitle="Only github.com URLs are accepted. Analysis runs in the background and is idempotent."
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              analyze.mutate();
            }}
            style={{ display: 'grid', gap: theme.space(4) }}
          >
            <Input
              label="Repository URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/owner/name"
              required
              inputMode="url"
              hint="Accepts a full URL, a clone URL, or simply owner/name"
            />
            <div style={{ display: 'grid', gap: theme.space(4), gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
              <Input
                label="Max commits to ingest"
                type="number"
                min={50}
                max={20000}
                value={maxCommits}
                onChange={(e) => setMaxCommits(Number(e.target.value))}
                hint="Higher values need more GitHub API quota"
              />
            </div>
            <div style={{ display: 'flex', gap: theme.space(3), alignItems: 'center' }}>
              <Button type="submit" loading={analyze.isPending}>
                <Search size={15} /> Start analysis
              </Button>
              <span style={{ fontSize: 12, color: theme.color.textFaint }}>
                Requires a token with a rate limit below 5,000 requests/hour for large repos.
              </span>
            </div>
          </form>
        </Card>

        <Card padded={false}>
          <div style={{ padding: theme.space(5) }}>
            <CardHeader
              title="Tracked repositories"
              subtitle="Everything ingested so far"
              actions={
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => reposQuery.refetch()}
                  title="Refresh"
                >
                  Refresh
                </Button>
              }
            />
          </div>
          <Table>
            <thead>
              <tr>
                <Th>Repository</Th>
                <Th>Status</Th>
                <Th align="right">Commits</Th>
                <Th align="right">Issues</Th>
                <Th align="right">Stars</Th>
                <Th>Last synced</Th>
                <Th align="right">Open</Th>
              </tr>
            </thead>
            <tbody>
              {reposQuery.isLoading && (
                <tr>
                  <Td colSpan={7}>Loading repositories…</Td>
                </tr>
              )}
              {reposQuery.data?.items.map((repo) => (
                <tr key={repo.id}>
                  <Td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <FolderGit2 size={14} color={theme.color.textFaint} aria-hidden />
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: theme.color.text, fontWeight: 600 }}>
                          {repo.full_name}
                        </div>
                        <div style={{ fontSize: 11.5, color: theme.color.textFaint }}>
                          {repo.language ?? 'unknown'} · {truncate(repo.description, 60)}
                        </div>
                      </div>
                    </div>
                  </Td>
                  <Td>
                    <Badge tone={STATUS_TONE[repo.sync_status]}>{repo.sync_status}</Badge>
                  </Td>
                  <Td align="right">{number(repo.ingested_commits)}</Td>
                  <Td align="right">{number(repo.ingested_issues)}</Td>
                  <Td align="right">{number(repo.stars)}</Td>
                  <Td>{relativeTime(repo.last_synced_at)}</Td>
                  <Td align="right">
                    <Button size="sm" variant="secondary" onClick={() => navigate(`/analytics?repo=${repo.id}`)}>
                      View
                    </Button>
                  </Td>
                </tr>
              ))}
              {!reposQuery.isLoading && !reposQuery.data?.items.length && (
                <tr>
                  <Td colSpan={7}>
                    {isAdmin
                      ? 'No repositories analysed yet.'
                      : 'No repositories are shared with you yet. Enter a link above to add your own project — you will own it and be able to see it — or ask a project manager to grant you access to an existing one.'}
                  </Td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card>

        {jobsQuery.data?.items.length ? (
          <Card padded={false}>
            <div style={{ padding: theme.space(5) }}>
              <CardHeader title="Ingestion jobs" subtitle="Every pipeline run is recorded for auditability" />
            </div>
            <Table>
              <thead>
                <tr>
                  <Th>Target</Th>
                  <Th>Status</Th>
                  <Th align="right">Rows</Th>
                  <Th align="right">API calls</Th>
                  <Th align="right">Duration</Th>
                  <Th>Finished</Th>
                </tr>
              </thead>
              <tbody>
                {jobsQuery.data.items.map((job) => (
                  <JobRow key={job.id} job={job} />
                ))}
              </tbody>
            </Table>
          </Card>
        ) : null}

        <div
          style={{
            display: 'grid',
            gap: theme.space(4),
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          }}
        >
          <StatTile label="Quota used" value="Capped" hint="ETag cache prevents repeat calls" />
          <StatTile label="Write mode" value="Idempotent" hint="Unique keys, no duplicate rows" />
          <StatTile label="Token exposure" value="None" hint="Server-side only, never in the browser" />
        </div>
      </div>
    </>
  );
}

function JobRow({ job }: { job: Job }) {
  const tone =
    job.status === 'completed' ? 'good' : job.status === 'failed' ? 'critical' : 'warning';
  return (
    <tr>
      <Td>
        <span className="mono">{job.target}</span>
      </Td>
      <Td>
        <Badge tone={tone}>
          {job.status === 'completed' ? (
            <CheckCircle2 size={11} />
          ) : job.status === 'failed' ? (
            <XCircle size={11} />
          ) : null}
          {job.status}
        </Badge>
        {job.error && (
          <div style={{ fontSize: 11, color: theme.color.danger, marginTop: 4, maxWidth: 320 }}>
            {job.error}
          </div>
        )}
      </Td>
      <Td align="right">{number(job.rows_ingested)}</Td>
      <Td align="right">{number(job.api_calls_made)}</Td>
      <Td align="right">
        {job.duration_seconds ? `${job.duration_seconds.toFixed(1)}s` : '—'}
      </Td>
      <Td>{relativeTime(job.finished_at ?? job.created_at)}</Td>
    </tr>
  );
}
