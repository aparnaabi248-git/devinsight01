import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlertTriangle, Brain, Clock, ShieldAlert, Tags } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Input,
  ProgressBar,
  Textarea,
} from '@/components/ui';
import { formatMetric, percent } from '@/lib/format';
import { parseChanges } from '@/lib/parseChanges';
import { theme } from '@/lib/theme';
import type {
  ClassifyIssueResponse,
  DefectRiskResponse,
  EffortResponse,
  PriorityResponse,
} from '@/types/api';

const TONE_FOR_LEVEL: Record<string, 'good' | 'warning' | 'critical' | 'neutral'> = {
  LOW: 'good',
  MEDIUM: 'warning',
  HIGH: 'critical',
  CRITICAL: 'critical',
};

export default function PredictionsPage() {
  return (
    <>
      <PageHeader
        title="ML predictions"
        description="Four trained models served over REST. Every result reports the model version that produced it, and every prediction is written to the audit log with its input hash."
      />
      <div
        style={{
          padding: theme.space(8),
          display: 'grid',
          gap: theme.space(5),
          gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))',
          alignItems: 'start',
        }}
      >
        <DefectRiskPanel />
        <ClassifierPanel />
        <PriorityPanel />
        <EffortPanel />
        <LimitationsPanel />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ MODEL 1 */
function DefectRiskPanel() {
  const [repository, setRepository] = useState('pallets/click');
  const [author, setAuthor] = useState('');
  const [files, setFiles] = useState(
    'src/click/core.py, +180, -40\ntests/test_core.py, +60, -5',
  );
  const [message, setMessage] = useState('Refactor parameter parsing');

  const mutation = useMutation({
    mutationFn: () =>
      api.defectRisk({
        repository: repository.trim(),
        author: author.trim() || undefined,
        commit_message: message || undefined,
        changes: parseChanges(files),
      }),
    onSuccess: (data) =>
      toast.success(`Defect risk: ${data.risk_level} (${percent(data.probability * 100, 0)})`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Prediction failed'),
  });

  return (
    <Card>
      <CardHeader
        title="Defect risk"
        subtitle="Binary classifier over 49 leakage-free change features. LOW < 0.33 ≤ MEDIUM < 0.66 ≤ HIGH."
      />
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        <Input label="Repository" value={repository} onChange={(e) => setRepository(e.target.value)} hint="owner/name" />
        <Input label="Author (optional)" value={author} onChange={(e) => setAuthor(e.target.value)} />
        <Input label="Commit message" value={message} onChange={(e) => setMessage(e.target.value)} />
        <Textarea
          label="Changed files"
          hint="One per line: path, +additions, -deletions"
          value={files}
          onChange={(e) => setFiles(e.target.value)}
          rows={3}
          style={{ fontFamily: theme.font.mono, fontSize: 12.5, minHeight: 78 }}
        />
        <Button onClick={() => mutation.mutate()} loading={mutation.isPending} full>
          <ShieldAlert size={15} /> Predict defect risk
        </Button>
        {mutation.data && <DefectRiskResult data={mutation.data} />}
      </div>
    </Card>
  );
}

function DefectRiskResult({ data }: { data: DefectRiskResponse }) {
  const tone = TONE_FOR_LEVEL[data.risk_level] ?? 'neutral';
  return (
    <div
      style={{
        marginTop: theme.space(3),
        padding: theme.space(4),
        borderRadius: theme.radius.md,
        background: theme.color.bgElevated,
        border: `1px solid ${theme.color.border}`,
        display: 'grid',
        gap: theme.space(3),
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: theme.space(3) }}>
        <span style={{ fontSize: 13, color: theme.color.textMuted }}>Defect risk</span>
        <Badge tone={tone}>{data.risk_level}</Badge>
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 5 }}>
          <span style={{ color: theme.color.textMuted }}>Probability</span>
          <strong className="mono" style={{ color: theme.color.text }}>
            {data.probability.toFixed(4)}
          </strong>
        </div>
        <ProgressBar
          value={data.probability * 100}
          tone={tone === 'critical' ? theme.color.danger : tone === 'warning' ? theme.color.warning : theme.color.success}
        />
      </div>
      <p style={{ margin: 0, fontSize: 12.5, color: theme.color.textMuted, lineHeight: 1.6 }}>{data.message}</p>
      {data.contributing_factors.length > 0 && (
        <div style={{ display: 'grid', gap: 5 }}>
          <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: theme.color.textFaint, fontWeight: 650 }}>
            Contributing factors
          </span>
          {data.contributing_factors.map((factor) => (
            <div key={factor.feature} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              <span className="mono" style={{ color: theme.color.textMuted }}>{factor.feature}</span>
              <span style={{ color: theme.color.textFaint }}>
                {factor.value} {factor.unit}
                {factor.model_importance > 0 && (
                  <span style={{ marginLeft: 8, color: theme.color.primaryHover }}>
                    w={factor.model_importance.toFixed(3)}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      <ModelStamp version={data.model_version} id={data.prediction_id} />
    </div>
  );
}

/* ------------------------------------------------------------- MODELS 2 & 3 */
const SAMPLE = {
  title: 'AttributeError: _Token has no attribute _name when using iter_errors',
  body: 'I get a traceback on click 8.1 when parsing a value with a metavar. The stack trace points at TokenWrapper.__getattr__. This is a regression from 8.0.',
};

/** Model 2 — six-way issue classification. */
function ClassifierPanel() {
  const [title, setTitle] = useState(SAMPLE.title);
  const [body, setBody] = useState(SAMPLE.body);
  const [labels, setLabels] = useState('bug, regression');

  const classification = useMutation({
    mutationFn: () => api.classifyIssue({ title, body, labels: splitLabels(labels) }),
    onSuccess: (d) => toast.success(`Category: ${d.category} (${percent(d.confidence * 100, 0)})`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Classification failed'),
  });

  return (
    <Card>
      <CardHeader
        title="Issue classification"
        subtitle="Six categories from TF-IDF over word 1–2-grams and character 3–5-grams of the cleaned issue text."
      />
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        <Input label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea label="Body" value={body} onChange={(e) => setBody(e.target.value)} rows={3} />
        <Input label="Labels" value={labels} onChange={(e) => setLabels(e.target.value)} hint="Comma separated" />
        <Button onClick={() => classification.mutate()} loading={classification.isPending} full>
          <Tags size={15} /> Classify issue
        </Button>
        {classification.data && <CategoryResult data={classification.data} />}
      </div>
    </Card>
  );
}

/** Model 3 — advisory urgency estimate, with the disclaimer always rendered. */
function PriorityPanel() {
  const [title, setTitle] = useState(SAMPLE.title);
  const [body, setBody] = useState(SAMPLE.body);
  const [labels, setLabels] = useState('bug');
  const [comments, setComments] = useState(3);
  const [association, setAssociation] = useState('CONTRIBUTOR');

  const mutation = useMutation({
    mutationFn: () =>
      api.predictPriority({
        title,
        body,
        labels: splitLabels(labels),
        comments_count: comments,
        author_association: association,
      }),
    onSuccess: (d) => toast.success(`Priority: ${d.priority}`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Priority prediction failed'),
  });

  return (
    <Card>
      <CardHeader
        title="Issue priority"
        subtitle="Hybrid model: TF-IDF over the title plus structured severity, engagement and association signals."
      />
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        <Input label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea label="Body" value={body} onChange={(e) => setBody(e.target.value)} rows={3} />
        <Input label="Labels" value={labels} onChange={(e) => setLabels(e.target.value)} hint="Comma separated" />
        <div style={{ display: 'grid', gap: theme.space(3), gridTemplateColumns: '1fr 1fr' }}>
          <Input
            label="Comment count"
            type="number"
            min={0}
            value={comments}
            onChange={(e) => setComments(Number(e.target.value))}
          />
          <Input
            label="Author association"
            value={association}
            onChange={(e) => setAssociation(e.target.value)}
          />
        </div>
        <Button onClick={() => mutation.mutate()} loading={mutation.isPending} full>
          <Brain size={15} /> Estimate priority
        </Button>
        {mutation.data && <PriorityResult data={mutation.data} />}
      </div>
    </Card>
  );
}

function splitLabels(raw: string) {
  return raw.split(',').map((l) => l.trim()).filter(Boolean);
}

function CategoryResult({ data }: { data: ClassifyIssueResponse }) {
  return (
    <ResultBox title="Classification">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: theme.space(3) }}>
        <Badge tone="primary">{data.category.replace('_', ' ')}</Badge>
        <span className="mono" style={{ fontSize: 12.5, color: theme.color.textMuted }}>
          {percent(data.confidence * 100, 1)} confident
        </span>
      </div>
      <DistributionBars probabilities={data.probabilities} />
      {data.top_features.length > 0 && (
        <div style={{ marginTop: theme.space(3) }}>
          <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: theme.color.textFaint, fontWeight: 650 }}>
            Discriminative terms in this text
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
            {data.top_features.slice(0, 10).map((term) => (
              <span
                key={term}
                className="mono"
                style={{
                  fontSize: 11,
                  padding: '2px 7px',
                  borderRadius: theme.radius.sm,
                  background: theme.color.primarySoft,
                  color: theme.color.primaryHover,
                }}
              >
                {term}
              </span>
            ))}
          </div>
        </div>
      )}
      <ModelStamp version={data.model_version} id={data.prediction_id} />
    </ResultBox>
  );
}

function PriorityResult({ data }: { data: PriorityResponse }) {
  return (
    <ResultBox title="Priority estimate">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: theme.space(3) }}>
        <Badge tone={TONE_FOR_LEVEL[data.priority] ?? 'neutral'}>{data.priority}</Badge>
        <span className="mono" style={{ fontSize: 12.5, color: theme.color.textMuted }}>
          {percent(data.confidence * 100, 1)} confident
        </span>
      </div>
      <DistributionBars probabilities={data.probabilities} />
      {data.contributing_factors.length > 0 && (
        <div style={{ marginTop: theme.space(3), display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {data.contributing_factors.slice(0, 8).map((factor) => (
            <span key={factor} className="mono" style={{ fontSize: 11, color: theme.color.textFaint }}>
              {factor}
            </span>
          ))}
        </div>
      )}
      <Disclaimer text={data.disclaimer} />
      <ModelStamp version={data.model_version} id={data.prediction_id} />
    </ResultBox>
  );
}

/* ------------------------------------------------------------------ MODEL 4 */
function EffortPanel() {
  const [title, setTitle] = useState('Implement OAuth2 device-code login flow');
  const [body, setBody] = useState(
    'Users want to authenticate from headless environments. Needs a new provider, token storage, and tests.',
  );
  const [labels, setLabels] = useState('feature, enhancement');

  const mutation = useMutation({
    mutationFn: () => api.estimateEffort({ title, body, labels: splitLabels(labels) }),
    onSuccess: (d) => toast.success(`Estimated effort: ${d.estimated_hours} h`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Effort estimation failed'),
  });

  return (
    <Card>
      <CardHeader
        title="Effort estimation"
        subtitle="Regression on time-to-close of historical issues, with an interval from the residual spread."
      />
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        <Input label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea label="Body" value={body} onChange={(e) => setBody(e.target.value)} rows={3} />
        <Input label="Labels" value={labels} onChange={(e) => setLabels(e.target.value)} />
        <Button onClick={() => mutation.mutate()} loading={mutation.isPending} full>
          <Clock size={15} /> Estimate effort
        </Button>
        {mutation.data && <EffortResult data={mutation.data} />}
      </div>
    </Card>
  );
}

function EffortResult({ data }: { data: EffortResponse }) {
  const mae = data.contributors.mean_absolute_error_hours ?? 0;
  return (
    <ResultBox title="Effort estimate">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: theme.space(2), marginBottom: theme.space(2) }}>
        <span style={{ fontSize: 30, fontWeight: 700, color: theme.color.primaryHover, letterSpacing: '-0.02em' }}>
          {data.estimated_hours.toFixed(1)}
        </span>
        <span style={{ fontSize: 14, color: theme.color.textMuted }}>{data.unit}</span>
      </div>
      <div style={{ fontSize: 12.5, color: theme.color.textMuted, marginBottom: theme.space(3) }}>
        Range {data.estimate_low_hours.toFixed(1)} – {data.estimate_high_hours.toFixed(1)}{' '}
        {data.unit} (±{mae.toFixed(1)} h typical error)
      </div>
      <div style={{ display: 'grid', gap: 5, fontSize: 12 }}>
        {data.contributors.r2 !== undefined && (
          <Row label="Model R²" value={formatMetric(data.contributors.r2, 4)} />
        )}
        {data.contributors.within_20pct_rate !== undefined && (
          <Row label="Within ±20% of actual" value={`${formatMetric(data.contributors.within_20pct_rate, 1)}%`} />
        )}
        <Row label="Confidence" value={percent(data.confidence * 100, 0)} />
      </div>
      <Disclaimer text={data.methodology_note} />
      <ModelStamp version={data.model_version} id={data.prediction_id} />
    </ResultBox>
  );
}

/* ------------------------------------------------------------------ shared */
function ResultBox({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        marginTop: theme.space(3),
        padding: theme.space(4),
        borderRadius: theme.radius.md,
        background: theme.color.bgElevated,
        border: `1px solid ${theme.color.border}`,
        display: 'grid',
        gap: theme.space(2),
      }}
    >
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: theme.color.textFaint, fontWeight: 650 }}>
        {title}
      </span>
      {children}
    </div>
  );
}

function DistributionBars({ probabilities }: { probabilities: Record<string, number> }) {
  const entries = Object.entries(probabilities).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;
  return (
    <div style={{ display: 'grid', gap: 5 }}>
      {entries.map(([label, probability], index) => (
        <div key={label} style={{ display: 'grid', gridTemplateColumns: '112px 1fr 46px', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: theme.color.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {label}
          </span>
          <ProgressBar
            value={probability * 100}
            tone={index === 0 ? theme.color.primary : theme.color.borderStrong}
          />
          <span className="mono" style={{ fontSize: 11, color: theme.color.textFaint, textAlign: 'right' }}>
            {(probability * 100).toFixed(1)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function Disclaimer({ text }: { text: string }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        marginTop: theme.space(3),
        padding: theme.space(3),
        borderRadius: theme.radius.sm,
        background: theme.color.warningSoft,
        color: theme.color.warning,
        fontSize: 11.5,
        lineHeight: 1.55,
      }}
    >
      <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden />
      <span>{text}</span>
    </div>
  );
}

function ModelStamp({ version, id }: { version: string; id: number | null }) {
  return (
    <div
      style={{
        marginTop: theme.space(2),
        paddingTop: theme.space(2),
        borderTop: `1px solid ${theme.color.border}`,
        display: 'flex',
        gap: theme.space(2),
        flexWrap: 'wrap',
        fontSize: 11,
        color: theme.color.textFaint,
      }}
    >
      <Badge tone="neutral">{version}</Badge>
      {id !== null && <span>prediction #{id}</span>}
    </div>
  );
}

/** Surfaces each model's stated target definition and limitations, from its manifest. */
function LimitationsPanel() {
  const modelsQuery = useQuery({ queryKey: ['models'], queryFn: () => api.models() });
  const items = modelsQuery.data?.items ?? [];
  if (!items.length) return null;

  return (
    <Card>
      <CardHeader
        title="How to read these predictions"
        subtitle="Target definitions and limitations recorded in each model's training manifest."
      />
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        {items.map((model) => (
          <div
            key={model.tag}
            style={{
              borderTop: `1px solid ${theme.color.border}`,
              paddingTop: theme.space(3),
              display: 'grid',
              gap: 6,
            }}
          >
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 13 }}>{model.name}</strong>
              <Badge tone="neutral">{model.tag}</Badge>
              <span className="mono" style={{ fontSize: 11, color: theme.color.textFaint }}>
                {model.algorithm}
              </span>
            </div>
            {model.target_description && (
              <p style={{ margin: 0, fontSize: 12, color: theme.color.textMuted, lineHeight: 1.6 }}>
                {model.target_description}
              </p>
            )}
            {model.limitations && <Disclaimer text={model.limitations} />}
          </div>
        ))}
      </div>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: theme.space(3) }}>
      <span style={{ color: theme.color.textMuted }}>{label}</span>
      <span className="mono" style={{ color: theme.color.text }}>{value}</span>
    </div>
  );
}
