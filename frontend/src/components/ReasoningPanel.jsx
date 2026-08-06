// Handles the edge states called out in CHECKPOINT.txt:
// - explanation_error -> plan still valid, show "explanation unavailable" inline
// - warning -> surface it, don't drop it

export default function ReasoningPanel({ plan, explanationOverride }) {
  if (!plan) return null

  return (
    <div className="reasoning">
      <h3>Route analysis</h3>

      {plan.warning && <p className="warning" role="alert">{plan.warning}</p>}

      {explanationOverride != null ? (
        <p className="explanation">{explanationOverride}</p>
      ) : plan.explanation ? (
        <p className="explanation">{plan.explanation}</p>
      ) : (
        <p className="explanation" style={{ fontStyle: 'italic' }}>
          Explanation unavailable{plan.explanation_error ? ` — ${plan.explanation_error}` : ''}.
        </p>
      )}

      <dl>
        <dt>Targets visited</dt>
        <dd>{plan.visited_count} of {plan.pool_size_used}</dd>
        <dt>Fuel used</dt>
        <dd>
          {plan.total_fuel_cost_km_s} / {plan.fuel_budget_km_s} km/s ({Math.round((plan.fuel_used_fraction ?? 0) * 100)}%)
        </dd>
        <dt>Risk score collected</dt>
        <dd>{plan.total_risk_collected}</dd>
        {plan.skipped_count > 0 && (
          <>
            <dt>Skipped targets</dt>
            <dd>
              {/* M4: null guard — skipped_names may be null (not just undefined)
                  on older cached responses; fall back to empty array before join. */}
              {plan.skipped_count} ({(plan.skipped_names ?? []).join(', ')})
            </dd>
          </>
        )}
      </dl>

      {plan.step_breakdown?.length > 0 && (
        <details>
          <summary>Flight manifest ({plan.step_breakdown.length} legs)</summary>
          <table className="manifest-table">
            <thead>
              <tr>
                {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)', 'RAAN drift (°)'].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {plan.step_breakdown.map((step, i) => (
                <tr key={i}>
                  <td className="leg-index">{String(i + 1).padStart(2, '0')}</td>
                  <td>{step.from}</td>
                  <td>{step.to}</td>
                  <td>{step.delta_v_km_s}</td>
                  <td>{step.arrival_time_days ?? '—'}</td>
                  <td>{step.raan_drift_deg ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}
