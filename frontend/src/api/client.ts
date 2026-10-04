/**
 * Typed HTTP client.
 *
 * The JWT lives in localStorage and is attached as a Bearer header. GitHub tokens are
 * never present here — they exist only in the backend process environment.
 */
import type {
  AccessGrant,
  AnalyzeResponse,
  ClassifyIssueResponse,
  Commit,
  CommitDetail,
  Contributor,
  DefectRiskResponse,
  EffortResponse,
  EvaluationReport,
  HealthResponse,
  Issue,
  Job,
  ModelComparison,
  ModelVersion,
  MyAccess,
  Page,
  PriorityResponse,
  PullRequest,
  Release,
  RepoPermission,
  Repository,
  RepositoryHealth,
  RepositoryMetrics,
  Team,
  TeamDetail,
  TeamMember,
  TeamRole,
  TokenResponse,
  TrendSeries,
  User,
} from '@/types/api';

const configuredApiUrl = (import.meta.env.VITE_API_URL as string | undefined)?.trim();
const BASE_URL = configuredApiUrl
  ? configuredApiUrl.startsWith('/') || /^https?:\/\//i.test(configuredApiUrl)
    ? configuredApiUrl.replace(/\/+$/, '')
    : `https://${configuredApiUrl.replace(/\/+$/, '')}${configuredApiUrl.includes('.') ? '' : '.onrender.com'}/api`
  : '/api';
const TOKEN_KEY = 'devinsight.token';

export class ApiError extends Error {
  readonly detail: string;
  readonly code?: string;
  readonly context?: unknown;

  constructor(status: number, message: string, code?: string, context?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = message;
    this.code = code;
    this.context = context;
  }

  status: number;
}

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

type Query = Record<string, string | number | boolean | undefined | null>;

/** Statuses a gateway returns when it cannot reach the upstream API. */
const UPSTREAM_UNREACHABLE = new Set([502, 503, 504]);

interface FieldError {
  field?: string;
  message?: string;
}

/**
 * Turn a FastAPI validation failure into something a person can act on.
 *
 * The backend already names the offending field - `{"detail": "request validation
 * failed", "context": {"errors": [{"field": "password", "message": "..."}]}}` - but the
 * generic `detail` on its own told a user nothing except that they had done something
 * wrong. Report the field and the reason instead.
 */
function validationMessage(context: unknown): string | null {
  if (typeof context !== 'object' || context === null) return null;
  const errors = (context as { errors?: unknown }).errors;
  if (!Array.isArray(errors) || errors.length === 0) return null;

  const parts = errors.slice(0, 3).map((entry) => {
    const { field, message } = (entry ?? {}) as FieldError;
    const reason = message?.trim() || 'is invalid';
    return field ? `${field}: ${reason}` : reason;
  });
  const shown = parts.join('; ');
  return errors.length > 3 ? `${shown} (+${errors.length - 3} more)` : shown;
}

function buildUrl(path: string, query?: Query): string {
  const url = `${BASE_URL}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value));
    }
  });
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  query?: Query,
): Promise<T> {
  const token = tokenStore.get();
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) headers.set('Authorization', `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { ...options, headers });
  } catch (cause) {
    // `fetch` rejects with a bare TypeError when the request never completed - the API
    // is down, or the dev proxy is not forwarding. "Failed to fetch" tells the user
    // nothing actionable, so name the likely cause instead.
    throw new ApiError(
      0,
      'Cannot reach the DevInsight API. Is the backend running on port 8000?',
      'network_error',
      { path, cause: String(cause) },
    );
  }

  // The dev proxy answers 502 when it cannot reach the backend, and nginx does the
  // same in production. Both mean exactly one thing to the user - the API is not
  // running - but they arrive as an opaque gateway error that looks like our fault.
  if (UPSTREAM_UNREACHABLE.has(response.status)) {
    throw new ApiError(
      response.status,
      'Cannot reach the DevInsight API. Is the backend running on port 8000?',
      'network_error',
      { path, status: response.status },
    );
  }

  if (response.status === 401) {
    // The message must depend on *which* request failed, not on whether a token happens
    // to be lying around. A 401 from the credentials endpoints means the password was
    // rejected - there is no session to expire, and telling someone to sign in again
    // just sends them round in circles. Anywhere else, a 401 means the token we sent is
    // no longer valid.
    const isCredentialsRequest = /^\/auth\/(login|register)/.test(path);
    if (isCredentialsRequest) {
      throw new ApiError(401, 'Incorrect username or password.', 'invalid_credentials');
    }
    tokenStore.clear();
    // Hard redirect so every route re-runs the auth guard.
    if (!window.location.pathname.startsWith('/login')) {
      window.location.assign('/login');
    }
    throw new ApiError(401, 'Your session has expired. Please sign in again.', 'invalid_token');
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const body = (payload ?? {}) as { detail?: string; code?: string; context?: unknown };
    // Prefer the per-field reasons over a generic "request validation failed".
    const fieldLevel =
      body.code === 'request_validation_error' ? validationMessage(body.context) : null;
    throw new ApiError(
      response.status,
      fieldLevel ?? body.detail ?? `Request failed with status ${response.status}`,
      body.code,
      body.context,
    );
  }
  return payload as T;
}

const get = <T>(path: string, query?: Query) => request<T>(path, { method: 'GET' }, query);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });
const patch = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined });
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

export const api = {
  // ------------------------------------------------------------------ auth
  register: (body: { email: string; username: string; password: string; full_name?: string }) =>
    post<TokenResponse>('/auth/register', body),
  login: (body: { username: string; password: string }) => post<TokenResponse>('/auth/login', body),
  me: () => get<User>('/auth/me'),

  // -------------------------------------------------------------- system
  health: () => get<HealthResponse>('/health'),

  // --------------------------------------------------------- repositories
  analyze: (body: {
    url: string;
    ingest_history?: boolean;
    max_commits?: number;
    max_issues?: number;
    background?: boolean;
  }) => post<AnalyzeResponse>('/repositories/analyze', body),
  repositories: (query?: Query) => get<Page<Repository>>('/repositories', query),
  repository: (id: number | string) => get<Repository>(`/repositories/${id}`),
  analytics: (id: number | string) => get<RepositoryMetrics>(`/repositories/${id}/analytics`),
  health_: (id: number | string) => get<RepositoryHealth>(`/repositories/${id}/health`),
  trends: (id: number | string, query?: Query) =>
    get<TrendSeries>(`/repositories/${id}/trends`, query),
  contributors: (id: number | string, query?: Query) =>
    get<Page<Contributor>>(`/repositories/${id}/contributors`, query),
  commits: (id: number | string, query?: Query) =>
    get<Page<Commit>>(`/repositories/${id}/commits`, query),
  commit: (id: number | string, sha: string) =>
    get<CommitDetail>(`/repositories/${id}/commits/${sha}`),
  issues: (id: number | string, query?: Query) =>
    get<Page<Issue>>(`/repositories/${id}/issues`, query),
  pullRequests: (id: number | string, query?: Query) =>
    get<Page<PullRequest>>(`/repositories/${id}/pull-requests`, query),
  releases: (id: number | string) => get<Release[]>(`/repositories/${id}/releases`),
  jobs: (query?: Query) => get<Page<Job>>('/jobs', query),
  job: (id: number | string) => get<Job>(`/jobs/${id}`),

  // --------------------------------------------------- teams and code access
  teams: (query?: Query) => get<Page<Team>>('/teams', query),
  team: (id: number | string) => get<TeamDetail>(`/teams/${id}`),
  createTeam: (body: { name: string; description?: string; manager_username?: string }) =>
    post<Team>('/teams', body),
  updateTeam: (
    id: number | string,
    body: { name?: string; description?: string; is_active?: boolean },
  ) => patch<Team>(`/teams/${id}`, body),

  addTeamMember: (
    id: number | string,
    body: { username: string; role?: TeamRole; scoped_repository_id?: number | null },
  ) => post<TeamMember>(`/teams/${id}/members`, body),
  updateTeamMember: (
    id: number | string,
    userId: number | string,
    body: { role?: TeamRole; scoped_repository_id?: number | null },
  ) => patch<TeamMember>(`/teams/${id}/members/${userId}`, body),
  removeTeamMember: (id: number | string, userId: number | string) =>
    del<void>(`/teams/${id}/members/${userId}`),

  grantTeamRepository: (
    id: number | string,
    body: {
      repository_id: number;
      permission: RepoPermission;
      expires_at?: string | null;
      note?: string | null;
    },
  ) => post<AccessGrant>(`/teams/${id}/access`, body),
  revokeTeamRepository: (id: number | string, grantId: number | string) =>
    del<void>(`/teams/${id}/access/${grantId}`),

  /** Administrator-only: grant a repository to one user without going via a team. */
  grantUserRepository: (body: {
    repository_id: number;
    user_id: number;
    permission: RepoPermission;
    note?: string | null;
  }) => post<AccessGrant>('/teams/access', body),

  myAccess: () => get<MyAccess>('/teams/access/me'),

  // -------------------------------------------------------------------- ML
  defectRisk: (body: {
    repository: string;
    author?: string;
    commit_message?: string;
    changes: { path: string; change_type?: string; additions: number; deletions: number }[];
  }) => post<DefectRiskResponse>('/ml/defect-risk', body),
  classifyIssue: (body: { title: string; body?: string; labels?: string[] }) =>
    post<ClassifyIssueResponse>('/ml/classify-issue', body),
  predictPriority: (body: {
    title: string;
    body?: string;
    labels?: string[];
    comments_count?: number;
    author_association?: string;
  }) => post<PriorityResponse>('/ml/predict-priority', body),
  estimateEffort: (body: {
    title: string;
    body?: string;
    labels?: string[];
    comments_count?: number;
    author_association?: string;
  }) => post<EffortResponse>('/ml/estimate-effort', body),
  models: () => get<{ items: ModelVersion[]; loaded: string[]; total: number }>('/ml/models'),
  modelComparison: () => get<ModelComparison[]>('/ml/models/comparison'),
  evaluation: (name: string) => get<EvaluationReport>(`/ml/evaluation/${name}`),
};
