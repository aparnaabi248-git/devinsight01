import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { Badge, Button, EmptyState, ErrorState, ProgressBar, StatTile } from '@/components/ui';
import { compactNumber, hours, periodLabel, percent, shortSha, truncate } from '@/lib/format';
import { riskTone } from '@/lib/theme';
import { parseChanges } from '@/lib/parseChanges';
import { defaultRepositoryId } from '@/lib/repositories';
import type { Repository } from '@/types/api';

describe('StatTile', () => {
  it('renders a label, value and hint', () => {
    render(<StatTile label="Commits" value="1,234" hint="last 30 days" />);
    expect(screen.getByText('Commits')).toBeInTheDocument();
    expect(screen.getByText('1,234')).toBeInTheDocument();
    expect(screen.getByText('last 30 days')).toBeInTheDocument();
  });

  it('applies a semantic tone to the value', () => {
    const { rerender } = render(<StatTile label="Score" value="42" tone="critical" />);
    expect(screen.getByText('42').getAttribute('style')).toContain('239');
    rerender(<StatTile label="Score" value="42" tone="good" />);
    expect(screen.getByText('42').getAttribute('style')).toContain('34');
  });
});

describe('Button', () => {
  it('calls onClick when enabled', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Predict</Button>);
    fireEvent.click(screen.getByText('Predict'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not call onClick when disabled', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick} disabled>Predict</Button>);
    fireEvent.click(screen.getByText('Predict'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('shows a spinner and blocks clicks while loading', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick} loading>Predict</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('ProgressBar', () => {
  it('exposes an accessible progressbar role and value', () => {
    render(<ProgressBar value={42} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });

  it('clamps out-of-range values', () => {
    render(<ProgressBar value={500} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });
});

describe('Badge', () => {
  it('renders its children', () => {
    render(<Badge tone="critical">HIGH</Badge>);
    expect(screen.getByText('HIGH')).toBeInTheDocument();
  });
});

describe('ErrorState', () => {
  it('surfaces the API detail message', () => {
    render(<ErrorState error={{ detail: 'Repository not found' }} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Repository not found')).toBeInTheDocument();
  });

  it('offers a retry action when one is provided', () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new Error('boom')} onRetry={onRetry} />);
    fireEvent.click(screen.getByText(/retry/i));
    expect(onRetry).toHaveBeenCalled();
  });
});

describe('EmptyState', () => {
  it('renders a title and description', () => {
    render(<EmptyState title="No repositories" description="Analyse one to begin." />);
    expect(screen.getByText('No repositories')).toBeInTheDocument();
    expect(screen.getByText('Analyse one to begin.')).toBeInTheDocument();
  });
});

describe('formatters', () => {
  it('compacts large numbers', () => {
    expect(compactNumber(999)).toBe('999');
    expect(compactNumber(1500)).toBe('1.5k');
    expect(compactNumber(2_400_000)).toBe('2.4M');
    expect(compactNumber(null)).toBe('—');
  });

  it('formats hours in human units', () => {
    expect(hours(0.5)).toBe('30 min');
    expect(hours(6.5)).toBe('6.5 h');
    expect(hours(72)).toBe('3.0 d');
    expect(hours(null)).toBe('—');
  });

  it('formats percentages and tolerates null', () => {
    expect(percent(82.456)).toBe('82.5%');
    expect(percent(null)).toBe('—');
  });

  it('truncates long text and shortens shas', () => {
    expect(truncate('abcdefghij', 5)).toBe('abcd…');
    expect(truncate('short')).toBe('short');
    expect(shortSha('06b2a678741131fd577ce170e23e5ca0aeba0309')).toBe('06b2a67');
  });

  it('labels trend periods per granularity', () => {
    expect(periodLabel('2024-03-05', 'daily')).toMatch(/05/);
    expect(periodLabel('2024-03-05', 'monthly')).toMatch(/Mar/);
  });
});

describe('riskTone', () => {
  it('maps risk levels to semantic tones', () => {
    expect(riskTone('HIGH')).toBe('high');
    expect(riskTone('CRITICAL')).toBe('high');
    expect(riskTone('MEDIUM')).toBe('medium');
    expect(riskTone('LOW')).toBe('low');
  });
});

describe('parseChanges', () => {
  it('parses the file/diff textarea format', () => {
    const parsed = parseChanges('src/a.py, +10, -3\ntests/b.py, +5, -0');
    expect(parsed).toEqual([
      { path: 'src/a.py', additions: 10, deletions: 3 },
      { path: 'tests/b.py', additions: 5, deletions: 0 },
    ]);
  });

  it('skips blank lines and defaults missing counts to zero', () => {
    const parsed = parseChanges('\n  \nfoo.py, , ');
    expect(parsed).toEqual([{ path: 'foo.py', additions: 0, deletions: 0 }]);
  });

  it('handles negative-prefixed deletions', () => {
    expect(parseChanges('a.py, +1, -7')).toEqual([
      { path: 'a.py', additions: 1, deletions: 7 },
    ]);
  });
});

describe('API client', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('sends a bearer token when one is stored', async () => {
    localStorage.setItem('devinsight.token', 'test-token');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'healthy' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const { api } = await import('@/api/client');
    const result = await api.health();

    expect(result.status).toBe('healthy');
    const [, options] = fetchMock.mock.calls[0];
    expect((options.headers as Headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('sends no Authorization header when anonymous', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'healthy' }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const { api } = await import('@/api/client');
    await api.health();

    const [, options] = fetchMock.mock.calls[0];
    expect((options.headers as Headers).get('Authorization')).toBeNull();
  });

  it('throws an ApiError carrying the backend detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: 'Repository 5 does not exist',
            code: 'repository_not_found',
          }),
          { status: 404, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );

    const { api, ApiError } = await import('@/api/client');
    // The fetch mock returns a single Response, so each call needs a fresh one.
    const { fetch: fetchMock } = await import('vitest').then(() => ({
      fetch: globalThis.fetch as ReturnType<typeof vi.fn>,
    }));
    fetchMock.mockImplementation(
      () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: 'Repository 5 does not exist',
              code: 'repository_not_found',
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          ),
        ),
    );

    try {
      await api.repository(5);
      throw new Error('expected the request to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as InstanceType<typeof ApiError>).code).toBe('repository_not_found');
      expect((error as InstanceType<typeof ApiError>).detail).toBe(
        'Repository 5 does not exist',
      );
      expect((error as InstanceType<typeof ApiError>).status).toBe(404);
    }
  });

  it('reports a rejected login as bad credentials, not an expired session', async () => {
    // Regression: every 401 used to be rewritten as "Your session has expired", which
    // is actively misleading on the login form - there is no session to expire, and the
    // backend had already said exactly what was wrong.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: 'incorrect username or password',
              code: 'invalid_credentials',
            }),
            { status: 401, headers: { 'Content-Type': 'application/json' } },
          ),
        ),
      ),
    );

    const { api, ApiError } = await import('@/api/client');
    try {
      await api.login({ username: 'appu28', password: 'wrong' });
      throw new Error('expected the request to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as InstanceType<typeof ApiError>).code).toBe('invalid_credentials');
      expect((error as InstanceType<typeof ApiError>).detail).toBe(
        'Incorrect username or password.',
      );
    }
  });

  it('does not clear a token or redirect when a login is rejected', async () => {
    localStorage.setItem('devinsight.token', 'still-a-real-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(new Response('{}', { status: 401 })),
      ),
    );

    const { api, ApiError } = await import('@/api/client');
    const failure = (await api
      .login({ username: 'a', password: 'b' })
      .catch((error: unknown) => error)) as InstanceType<typeof ApiError>;

    // A stale token plus a mistyped password is still a credentials problem. Wiping a
    // valid token here would sign the user out of every other tab for no reason.
    expect(failure.code).toBe('invalid_credentials');
    expect(localStorage.getItem('devinsight.token')).toBe('still-a-real-token');
  });

  it('still treats a 401 on a data route as an expired session', async () => {
    localStorage.setItem('devinsight.token', 'a-stale-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(new Response('{}', { status: 401 })),
      ),
    );

    const { api, ApiError } = await import('@/api/client');
    const failure = (await api
      .repository(5)
      .catch((error: unknown) => error)) as InstanceType<typeof ApiError>;

    expect(failure.code).toBe('invalid_token');
    expect(failure.detail).toMatch(/session has expired/);
    // The stale token is dropped so the next request is made anonymously.
    expect(localStorage.getItem('devinsight.token')).toBeNull();
  });

  it('names the likely cause when the API cannot be reached', async () => {
    // Regression: `fetch` rejects with a bare TypeError, and the user saw only
    // "Failed to fetch" with no indication that the backend was the problem.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => Promise.reject(new TypeError('Failed to fetch'))),
    );

    const { api, ApiError } = await import('@/api/client');
    try {
      await api.health();
      throw new Error('expected the request to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const failure = error as InstanceType<typeof ApiError>;
      expect(failure.code).toBe('network_error');
      expect(failure.detail).toMatch(/backend running on port 8000/);
    }
  });

  it('explains a 502 from the dev proxy as "the API is not running"', async () => {
    // Regression: the Vite proxy returns 502 when the backend is down, which used to
    // surface as a bare gateway error and read like an application fault.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(new Response('Bad Gateway', { status: 502 })),
      ),
    );

    const { api, ApiError } = await import('@/api/client');
    try {
      await api.teams();
      throw new Error('expected the request to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const failure = error as InstanceType<typeof ApiError>;
      expect(failure.code).toBe('network_error');
      expect(failure.detail).toMatch(/backend running on port 8000/);
    }
  });

  it('omits empty query parameters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"items":[]}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const { api } = await import('@/api/client');
    await api.repositories({ page: 1, search: '', language: undefined });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('page=1');
    expect(url).not.toContain('search=');
    expect(url).not.toContain('language=');
  });
});

describe('defaultRepositoryId', () => {
  const repo = (
    id: number,
    ingested_commits: number,
    latest_commit_at: string | null = null,
  ) => ({ id, ingested_commits, latest_commit_at }) as Repository;

  it('picks the repository with the most ingested history', () => {
    // Regression: Analytics and Contributors took `items[0]`, which is alphabetical -
    // so they opened on a freshly added 2-commit repo while the Dashboard showed
    // psf/requests. Contributors rendered "1 contributor, High bus factor risk".
    const items = [repo(8, 2, '2026-04-30'), repo(5, 900, '2026-09-28'), repo(1, 640)];
    expect(defaultRepositoryId(items)).toBe(5);
  });

  it('handles an empty or missing list', () => {
    expect(defaultRepositoryId([])).toBeUndefined();
    expect(defaultRepositoryId(undefined)).toBeUndefined();
  });

  it('does not mutate the input order', () => {
    const items = [repo(8, 2), repo(5, 900)];
    defaultRepositoryId(items);
    expect(items.map((r) => r.id)).toEqual([8, 5]);
  });

  it('breaks a history tie on the most recently active repository', () => {
    // seed_local.py caps ingestion at 900 commits per repository, so the five OSS
    // repositories tie exactly and health_score ties too. Ranking on commits alone sent
    // every page to encode/httpx, whose history ends six months before today, so the
    // dashboard showed "0 commits in the last 30 days" and "0 active in 90 days".
    const tied = [
      repo(1, 900, '2026-03-29'), // encode/httpx  - stale
      repo(2, 900, '2026-09-29'), // expressjs     - most recent
      repo(3, 900, '2026-09-23'), // pallets/click
      repo(4, 900, '2026-09-08'), // pallets/flask
    ];
    expect(defaultRepositoryId(tied)).toBe(2);
    // Same answer no matter how the API sorted the list.
    expect(defaultRepositoryId([...tied].reverse())).toBe(2);
  });

  it('ignores recency when it would outweigh history', () => {
    // A three-commit repository from last week must not outrank a 900-commit one.
    const items = [repo(6, 3, '2026-10-01'), repo(5, 900, '2026-09-28')];
    expect(defaultRepositoryId(items)).toBe(5);
  });

  it('treats a missing timestamp as oldest rather than newest', () => {
    const items = [repo(4, 900, null), repo(2, 900, '2026-09-29')];
    expect(defaultRepositoryId(items)).toBe(2);
  });

  it('breaks a total tie on the lowest id', () => {
    const items = [repo(8, 0), repo(5, 0)];
    expect(defaultRepositoryId(items)).toBe(5);
  });
});
