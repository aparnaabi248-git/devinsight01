/**
 * Typed API contracts mirroring backend/app/schemas. Keeping these in one place means
 * a backend schema change surfaces as a TypeScript error rather than a runtime surprise.
 */

export type Granularity = 'daily' | 'weekly' | 'monthly';

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface User {
  id: number;
  email: string;
  username: string;
  full_name: string | null;
  role: 'admin' | 'analyst' | 'viewer';
  is_active: boolean;
  github_login: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export type SyncStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface Repository {
  id: number;
  full_name: string;
  owner_name: string;
  name: string;
  description: string | null;
  html_url: string;
  default_branch: string;
  language: string | null;
  stars: number;
  forks: number;
  watchers: number;
  open_issues_count: number;
  license_spdx: string | null;
  is_archived: boolean;
  sync_status: SyncStatus;
  last_synced_at: string | null;
  ingested_commits: number;
  ingested_issues: number;
  health_score: number;
  /** Newest commit actually ingested; null before anything has been ingested. */
  latest_commit_at: string | null;
  created_at: string;
}

export interface Contributor {
  github_login: string;
  display_name: string | null;
  avatar_url: string | null;
  commits_count: number;
  additions: number;
  deletions: number;
  total_churn: number;
  files_touched: number;
  issues_opened: number;
  prs_opened: number;
  prs_merged: number;
  reviews_given: number;
  first_commit_at: string | null;
  last_commit_at: string | null;
}

export interface Commit {
  sha: string;
  author_login: string | null;
  author_name: string | null;
  message: string | null;
  authored_at: string;
  additions: number;
  deletions: number;
  churn: number;
  files_changed: number;
  is_merge: boolean;
  is_bugfix: boolean;
}

export interface FileChange {
  path: string;
  old_path: string | null;
  change_type: 'added' | 'modified' | 'deleted' | 'renamed';
  additions: number;
  deletions: number;
}

export interface CommitDetail extends Commit {
  file_changes: FileChange[];
}

export interface IssueLabel {
  name: string;
  colour: string | null;
}

export interface Issue {
  number: number;
  title: string;
  body: string | null;
  state: 'open' | 'closed';
  is_pull_request: boolean;
  author_login: string | null;
  author_association: string | null;
  comments_count: number;
  category: string | null;
  category_confidence: number | null;
  priority: string | null;
  effort_hours: number | null;
  resolution_hours: number | null;
  created_at: string;
  closed_at: string | null;
  html_url: string | null;
  labels: IssueLabel[];
}

export interface PullRequest {
  number: number;
  title: string;
  state: 'open' | 'closed' | 'merged';
  merged: boolean;
  author_login: string | null;
  merged_by: string | null;
  additions: number;
  deletions: number;
  size: number;
  changed_files: number;
  comments_count: number;
  review_comments_count: number;
  merge_duration_hours: number | null;
  created_at: string;
  closed_at: string | null;
  merged_at: string | null;
  html_url: string | null;
}

export interface Release {
  tag_name: string;
  name: string | null;
  author_login: string | null;
  is_draft: boolean;
  is_prerelease: boolean;
  published_at: string | null;
  html_url: string | null;
}

export interface LabelCount {
  name: string;
  count: number;
  open: number;
}

export interface CategoryCount {
  category: string;
  count: number;
  pct: number;
}

export interface RepositoryMetrics {
  total_commits: number;
  total_contributors: number;
  open_issues: number;
  closed_issues: number;
  total_pull_requests: number;
  merged_pull_requests: number;
  open_pull_requests: number;
  average_pr_size: number;
  average_pr_size_files: number;
  average_issue_resolution_hours: number | null;
  median_issue_resolution_hours: number | null;
  commit_frequency_per_week: number;
  commit_frequency_per_month: number;
  release_frequency_per_month: number;
  issue_closure_rate: number;
  pr_merge_rate: number;
  average_merge_duration_hours: number | null;
  total_additions: number;
  total_deletions: number;
  total_churn: number;
  bugfix_commits: number;
  bugfix_ratio: number;
  commits_last_30d: number;
  commits_last_90d: number;
  issues_opened_last_30d: number;
  active_contributors_last_90d: number;
  days_since_last_commit: number | null;
  top_contributors: Contributor[];
  most_changed_files: { path: string; changes: number; additions: number; deletions: number }[];
  label_distribution: LabelCount[];
  category_distribution: CategoryCount[];
}

export interface TrendPoint {
  period: string;
  commits: number;
  issues_opened: number;
  issues_closed: number;
  prs_opened: number;
  prs_merged: number;
  releases: number;
  bug_fixes: number;
  active_contributors: number;
  additions: number;
  deletions: number;
}

export interface TrendSeries {
  repository_id: number;
  full_name: string;
  granularity: Granularity;
  metric: string;
  points: TrendPoint[];
  totals: Record<string, number>;
}

export interface HealthIndicator {
  name: string;
  score: number;
  status: 'good' | 'warning' | 'critical';
  detail: string;
}

export interface RepositoryHealth {
  repository_id: number;
  full_name: string;
  overall_score: number;
  status: 'good' | 'warning' | 'critical';
  indicators: HealthIndicator[];
  defect_risk_summary: Record<string, unknown>;
}

export interface AnalyzeResponse {
  repository_id: number;
  full_name: string;
  job_id: number | null;
  status: SyncStatus;
  message: string;
}

export interface Job {
  id: number;
  job_type: string;
  target: string;
  status: string;
  rows_ingested: number;
  api_calls_made: number;
  error: string | null;
  duration_seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

// ------------------------------------------------------- teams and access types
export type TeamRole = 'manager' | 'member' | 'viewer';
export type RepoPermission = 'read' | 'write' | 'admin';
export type TeamCapability = 'view' | 'manage_members' | 'manage_access';

export interface TeamMember {
  id: number;
  user_id: number;
  username: string;
  email: string;
  role: TeamRole;
  /** When set, this member only reaches that one repository inside the team. */
  scoped_repository_id: number | null;
  joined_at: string | null;
}

export interface AccessGrant {
  id: number;
  repository_id: number;
  repository_name: string | null;
  grant_type: 'user' | 'team';
  team_id: number | null;
  team_name: string | null;
  user_id: number | null;
  username: string | null;
  permission: RepoPermission;
  granted_by_id: number | null;
  expires_at: string | null;
  note: string | null;
  is_expired: boolean;
}

export interface Team {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  manager_id: number | null;
  manager_username: string | null;
  is_active: boolean;
  member_count: number;
  repository_count: number;
  /** The signed-in user's role in this team, or null when they are not a member. */
  my_role: TeamRole | null;
  capabilities: TeamCapability[];
  created_at: string;
}

export interface TeamDetail extends Team {
  members: TeamMember[];
  repositories: AccessGrant[];
}

export interface RepositoryAccessSummary {
  repository_id: number;
  full_name: string;
  permission: RepoPermission | null;
  /** How the access was obtained. */
  source: 'admin' | 'direct' | 'team' | 'none';
  via_teams: string[];
}

export interface MyAccess {
  user_id: number;
  username: string;
  role: string;
  manages_teams: number;
  repositories: RepositoryAccessSummary[];
}

// ------------------------------------------------------------------ ML types
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';
export type PriorityLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
export type IssueCategory =
  | 'BUG'
  | 'FEATURE_REQUEST'
  | 'DOCUMENTATION'
  | 'QUESTION'
  | 'ENHANCEMENT'
  | 'OTHER';

export interface DefectRiskResponse {
  risk_level: RiskLevel;
  probability: number;
  message: string;
  contributing_factors: { feature: string; value: number; unit: string; model_importance: number }[];
  model_name: string;
  model_version: string;
  prediction_id: number | null;
  computed_at: string;
}

export interface ClassifyIssueResponse {
  category: IssueCategory;
  confidence: number;
  probabilities: Record<string, number>;
  top_features: string[];
  model_name: string;
  model_version: string;
  prediction_id: number | null;
  computed_at: string;
}

export interface PriorityResponse {
  priority: PriorityLevel;
  confidence: number;
  probabilities: Record<string, number>;
  contributing_factors: string[];
  disclaimer: string;
  model_name: string;
  model_version: string;
  prediction_id: number | null;
  computed_at: string;
}

export interface EffortResponse {
  estimated_hours: number;
  estimate_low_hours: number;
  estimate_high_hours: number;
  unit: string;
  confidence: number;
  contributors: Record<string, number>;
  methodology_note: string;
  model_name: string;
  model_version: string;
  prediction_id: number | null;
  computed_at: string;
}

export interface ModelVersion {
  id: number;
  name: string;
  version: number;
  tag: string;
  algorithm: string;
  task?: string | null;
  mlflow_run_id: string | null;
  dataset_version: string | null;
  data_hash: string | null;
  n_train: number | null;
  n_test: number | null;
  features: string[];
  metrics: Record<string, number | Record<string, number>>;
  is_active: boolean;
  trained_at: string | null;
  target_description: string | null;
  limitations: string | null;
}

export interface ModelComparisonRow {
  model: string;
  accuracy: number | null;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  roc_auc: number | null;
  mae: number | null;
  rmse: number | null;
  r2: number | null;
  cv_f1_mean: number | null;
  training_time_seconds: number;
  selected: boolean;
}

export interface ModelComparison {
  name: string;
  metric_focus: string;
  selection_criterion: string;
  rows: ModelComparisonRow[];
  selected_model: string;
  evaluated_at: string | null;
}

export interface EvaluationReport {
  name: string;
  version: number;
  algorithm: string;
  task_type: string;
  target_description: string;
  metrics: Record<string, number | Record<string, unknown>>;
  classification_report: Record<string, unknown> | null;
  confusion_matrix: number[][] | null;
  confusion_matrix_labels: string[] | null;
  cv_folds: Record<string, number>[];
  feature_importance: { feature: string; importance: number; kind: string }[];
  comparison: Record<string, number | string | boolean>[];
  hyperparameters: Record<string, unknown>;
  dataset: Record<string, unknown>;
  limitations: string | null;
  trained_at: string | null;
}

export interface HealthResponse {  status: string;
  version: string;
  environment: string;
  database: string;
  models_loaded: string[];
  timestamp: string;
}
