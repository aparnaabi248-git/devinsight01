import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, FlaskConical, Info } from 'lucide-react';

import { api } from '@/api/client';
import { PageHeader } from '@/components/AppLayout';
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui';
import { formatMetric, number, relativeTime } from '@/lib/format';
import { theme } from '@/lib/theme';

const MODEL_LABELS: Record<string, { title: string; task: string; metric: string }> = {
  defect_risk: { title: 'Defect risk', task: 'Binary classification', metric: 'F1' },
  issue_classifier: { title: 'Issue classification', task: 'Multiclass (6)', metric: 'F1-macro' },
  issue_priority: { title: 'Issue priority', task: 'Multiclass (4)', metric: 'F1-macro' },
  issue_effort: { title: 'Effort estimation', task: 'Regression', metric: 'MAE' },
};

export default function ModelsPage() {
  const modelsQuery = useQuery({ queryKey: ['models'], queryFn: () => api.models() });
  const comparisonQuery = useQuery({ queryKey: ['modelComparison'], queryFn: () => api.modelComparison() });
  const [selected, setSelected] = useState<string>('defect_risk');
  const evaluationQuery = useQuery({
    queryKey: ['evaluation', selected],
    queryFn: () => api.evaluation(selected),
    enabled: Boolean(selected),
  });

  if (modelsQuery.isError) {
    return (
      <>
        <PageHeader title="Model performance" />
        <div style={{ padding: theme.space(8) }}>
          <ErrorState error={modelsQuery.error} onRetry={() => modelsQuery.refetch()} />
        </div>
      </>
    );
  }

  const items = modelsQuery.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Model performance"
        description="Every metric below was measured on a held-out test split during training and is read from the artefact manifest at runtime — nothing is hard-coded."
      />

      <div style={{ padding: theme.space(8), display: 'grid', gap: theme.space(5) }}>
        {modelsQuery.isLoading ? (
          <LoadingState rows={5} />
        ) : !items.length ? (
          <Card>
            <EmptyState
              icon={<FlaskConical size={30} />}
              title="No trained models"
              description="Run `python ml/training/run_all.py` to train the four DevInsight models and generate their evaluation reports."
            />
          </Card>
        ) : (
          <>
            <Card padded={false}>
              <div style={{ padding: theme.space(5) }}>
                <CardHeader
                  title="Model registry"
                  subtitle="Every trained version, its algorithm, dataset size and real test metrics"
                />
              </div>
              <Table>
                <thead>
                  <tr>
                    <Th>Model</Th>
                    <Th>Version</Th>
                    <Th>Algorithm</Th>
                    <Th>Task</Th>
                    <Th align="right">Train rows</Th>
                    <Th align="right">Test rows</Th>
                    <Th align="right">Primary metric</Th>
                    <Th align="right">Accuracy</Th>
                    <Th align="right">Precision</Th>
                    <Th align="right">Recall</Th>
                    <Th align="right">F1</Th>
                    <Th align="right">MAE / R²</Th>
                    <Th>Trained</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((model) => {
                    const m = model.metrics as Record<string, number>;
                    const label = MODEL_LABELS[model.name];
                    return (
                      <tr
                        key={model.tag}
                        onClick={() => setSelected(model.name)}
                        style={{
                          cursor: 'pointer',
                          background: selected === model.name ? theme.color.primarySoft : undefined,
                        }}
                      >
                        <Td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                            <Activity size={13} color={theme.color.textFaint} aria-hidden />
                            <span style={{ color: theme.color.text, fontWeight: 600 }}>
                              {label?.title ?? model.name}
                            </span>
                          </div>
                        </Td>
                        <Td>
                          <Badge tone={model.is_active ? 'good' : 'neutral'}>
                            {model.is_active ? `v${model.version} active` : `v${model.version}`}
                          </Badge>
                        </Td>
                        <Td mono>{model.algorithm}</Td>
                        <Td>{model.task ?? '—'}</Td>
                        <Td align="right">{number(model.n_train)}</Td>
                        <Td align="right">{number(model.n_test)}</Td>
                        <Td align="right">
                          <strong style={{ color: theme.color.primaryHover }}>
                            {m.f1_macro !== undefined
                              ? formatMetric(m.f1_macro)
                              : m.mae !== undefined
                                ? formatMetric(m.mae, 2)
                                : '—'}
                          </strong>
                        </Td>
                        <Td align="right">{formatMetric(m.accuracy, 4)}</Td>
                        <Td align="right">{formatMetric(m.precision, 4)}</Td>
                        <Td align="right">{formatMetric(m.recall, 4)}</Td>
                        <Td align="right">{formatMetric(m.f1, 4)}</Td>
                        <Td align="right">
                          {m.mae !== undefined
                            ? `${formatMetric(m.mae, 2)} / ${formatMetric(m.r2, 3)}`
                            : '—'}
                        </Td>
                        <Td>{relativeTime(model.trained_at)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </Card>

            {comparisonQuery.data?.map((comparison) => (
              <Card key={comparison.name} padded={false}>
                <div style={{ padding: theme.space(5) }}>
                  <CardHeader
                    title={`${MODEL_LABELS[comparison.name]?.title ?? comparison.name} — candidate comparison`}
                    subtitle={`Selection criterion: ${comparison.selection_criterion || 'see manifest'}`}
                  />
                </div>
                <Table>
                  <thead>
                    <tr>
                      <Th>Candidate model</Th>
                      <Th align="right">Accuracy</Th>
                      <Th align="right">Precision</Th>
                      <Th align="right">Recall</Th>
                      <Th align="right">F1</Th>
                      <Th align="right">ROC-AUC</Th>
                      <Th align="right">MAE</Th>
                      <Th align="right">RMSE</Th>
                      <Th align="right">R²</Th>
                      <Th align="right">CV score</Th>
                      <Th align="right">Train time (s)</Th>
                      <Th>Selected</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparison.rows.map((row) => (
                      <tr
                        key={row.model}
                        style={{
                          background: row.selected ? theme.color.successSoft : undefined,
                        }}
                      >
                        <Td mono>{row.model}</Td>
                        <Td align="right">{formatMetric(row.accuracy, 4)}</Td>
                        <Td align="right">{formatMetric(row.precision, 4)}</Td>
                        <Td align="right">{formatMetric(row.recall, 4)}</Td>
                        <Td align="right">{formatMetric(row.f1, 4)}</Td>
                        <Td align="right">{formatMetric(row.roc_auc, 4)}</Td>
                        <Td align="right">{formatMetric(row.mae, 2)}</Td>
                        <Td align="right">{formatMetric(row.rmse, 2)}</Td>
                        <Td align="right">{formatMetric(row.r2, 4)}</Td>
                        <Td align="right">{formatMetric(row.cv_f1_mean, 4)}</Td>
                        <Td align="right">{formatMetric(row.training_time_seconds, 2)}</Td>
                        <Td>
                          {row.selected ? <Badge tone="good">selected</Badge> : <span style={{ color: theme.color.textFaint }}>—</span>}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>
            ))}

            <Card>
              <CardHeader
                title={`${MODEL_LABELS[selected]?.title ?? selected} — evaluation detail`}
                subtitle="Held-out test metrics, confusion matrix, CV folds and feature importance from the saved manifest"
                actions={
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {Object.keys(MODEL_LABELS).map((key) => (
                      <button
                        key={key}
                        onClick={() => setSelected(key)}
                        aria-pressed={selected === key}
                        style={{
                          padding: '5px 11px',
                          fontSize: 12,
                          borderRadius: theme.radius.pill,
                          cursor: 'pointer',
                          fontFamily: 'inherit',
                          fontWeight: 600,
                          background: selected === key ? theme.color.primary : theme.color.bgElevated,
                          color: selected === key ? '#fff' : theme.color.textMuted,
                          border: `1px solid ${selected === key ? 'transparent' : theme.color.border}`,
                        }}
                      >
                        {MODEL_LABELS[key].title}
                      </button>
                    ))}
                  </div>
                }
              />
              {evaluationQuery.isLoading ? (
                <LoadingState rows={5} />
              ) : evaluationQuery.data ? (
                <EvaluationDetail data={evaluationQuery.data} />
              ) : (
                <EmptyState title="No report for this model" />
              )}
            </Card>
          </>
        )}
      </div>
    </>
  );
}

function EvaluationDetail({
  data,
}: {
  data: Awaited<ReturnType<typeof api.evaluation>>;
}) {
  const m = data.metrics as Record<string, unknown>;
  const perClass = m.per_class as Record<string, Record<string, number>> | undefined;
  const dataset = data.dataset as Record<string, unknown>;
  const numericMetrics = Object.entries(m).filter(
    (entry): entry is [string, number] => typeof entry[1] === 'number',
  );

  return (
    <div style={{ display: 'grid', gap: theme.space(5) }}>
      <div style={{ display: 'grid', gap: theme.space(3) }}>
        <p style={{ margin: 0, fontSize: 13, color: theme.color.textMuted, lineHeight: 1.65 }}>
          {data.target_description}
        </p>
        <div
          style={{
            display: 'grid',
            gap: theme.space(3),
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          }}
        >
          {numericMetrics.slice(0, 10).map(([key, value]) => (
              <div
                key={key}
                style={{
                  padding: theme.space(3),
                  borderRadius: theme.radius.md,
                  background: theme.color.bgElevated,
                  border: `1px solid ${theme.color.border}`,
                }}
              >
                <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.06em', color: theme.color.textFaint, fontWeight: 650 }}>
                  {key.replace(/_/g, ' ')}
                </div>
                <div className="mono" style={{ fontSize: 17, fontWeight: 650, color: theme.color.text, marginTop: 3 }}>
                  {formatMetric(value, 4)}
                </div>
              </div>
            ))}
        </div>
      </div>

      {data.confusion_matrix && data.confusion_matrix_labels && (
        <div>
          <h3 style={{ margin: '0 0 10px', fontSize: 13, fontWeight: 650 }}>Confusion matrix</h3>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ padding: 8, color: theme.color.textFaint, textAlign: 'left' }} />
                  {data.confusion_matrix_labels.map((label) => (
                    <th
                      key={label}
                      scope="col"
                      style={{ padding: 8, color: theme.color.textFaint, fontWeight: 600, whiteSpace: 'nowrap' }}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.confusion_matrix.map((row, i) => (
                  <tr key={i}>
                    <th
                      scope="row"
                      style={{ padding: 8, textAlign: 'left', color: theme.color.textMuted, fontWeight: 600, whiteSpace: 'nowrap' }}
                    >
                      {data.confusion_matrix_labels![i]}
                    </th>
                    {row.map((value, j) => {
                      const max = Math.max(...data.confusion_matrix!.flat());
                      const intensity = max ? value / max : 0;
                      const correct = i === j;
                      return (
                        <td
                          key={j}
                          style={{
                            padding: 10,
                            textAlign: 'center',
                            fontFamily: theme.font.mono,
                            fontWeight: value > 0 ? 650 : 400,
                            color: value > 0 ? theme.color.text : theme.color.textFaint,
                            background: correct
                              ? `rgba(34, 197, 94, ${0.08 + intensity * 0.3})`
                              : value > 0
                                ? `rgba(239, 68, 68, ${0.08 + intensity * 0.3})`
                                : 'transparent',
                          }}
                        >
                          {value}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 11.5, color: theme.color.textFaint }}>
            Rows = actual, columns = predicted. Green = correct, red = misclassified.
          </p>
        </div>
      )}

      {perClass && (
        <div>
          <h3 style={{ margin: '0 0 10px', fontSize: 13, fontWeight: 650 }}>Per-class performance</h3>
          <Table>
            <thead>
              <tr>
                <Th>Class</Th>
                <Th align="right">Precision</Th>
                <Th align="right">Recall</Th>
                <Th align="right">F1</Th>
                <Th align="right">Support</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(perClass).map(([label, scores]) => (
                <tr key={label}>
                  <Td>{label}</Td>
                  <Td align="right">{formatMetric(scores.precision, 3)}</Td>
                  <Td align="right">{formatMetric(scores.recall, 3)}</Td>
                  <Td align="right">{formatMetric(scores.f1, 3)}</Td>
                  <Td align="right">{number(scores.support)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      {data.feature_importance.length > 0 && (
        <div>
          <h3 style={{ margin: '0 0 10px', fontSize: 13, fontWeight: 650 }}>
            Feature importance <span style={{ fontWeight: 400, color: theme.color.textFaint }}>(normalised)</span>
          </h3>
          <div style={{ display: 'grid', gap: 6 }}>
            {data.feature_importance.slice(0, 14).map((feature) => (
              <div key={feature.feature} style={{ display: 'grid', gridTemplateColumns: '190px 1fr 58px', gap: 9, alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: 11.5, color: theme.color.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={feature.feature}>
                  {feature.feature}
                </span>
                <div
                  style={{
                    height: 6,
                    background: theme.color.bgElevated,
                    borderRadius: 3,
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      width: `${Math.max(1, (feature.importance ?? 0) * 100 * 3)}%`,
                      height: '100%',
                      background: theme.color.primary,
                    }}
                  />
                </div>
                <span className="mono" style={{ fontSize: 11, color: theme.color.textFaint, textAlign: 'right' }}>
                  {formatMetric(feature.importance, 4)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gap: theme.space(3), gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
        <div>
          <h3 style={{ margin: '0 0 6px', fontSize: 13, fontWeight: 650 }}>Dataset</h3>
          <div style={{ display: 'grid', gap: 3, fontSize: 12 }}>
            {Object.entries(dataset)
              .filter(([, v]) => v === null || ['string', 'number', 'boolean'].includes(typeof v))
              .map(([key, value]) => (
                <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: theme.space(3) }}>
                  <span style={{ color: theme.color.textMuted }}>{key.replace(/_/g, ' ')}</span>
                  <span className="mono" style={{ color: theme.color.text }}>{String(value)}</span>
                </div>
              ))}
          </div>
        </div>
        {data.limitations && (
          <div>
            <h3 style={{ margin: '0 0 6px', fontSize: 13, fontWeight: 650, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Info size={13} color={theme.color.warning} aria-hidden /> Limitations
            </h3>
            <p style={{ margin: 0, fontSize: 12, color: theme.color.textMuted, lineHeight: 1.65 }}>
              {data.limitations}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
