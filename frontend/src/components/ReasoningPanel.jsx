// Handles the edge states called out in CHECKPOINT.txt:
// - explanation_error -> plan still valid, show "explanation unavailable" inline
// - warning -> surface it, don't drop it

export default function ReasoningPanel({ plan }) {
  if (!plan) return null

  return (
    <div>
      <h3>Reasoning</h3>

      {plan.warning && <p role="alert">Warning: {plan.warning}</p>}

      {plan.explanation ? (
        <p>{plan.explanation}</p>
      ) : (
        <p>Explanation unavailable{plan.explanation_error ? ` (${plan.explanation_error})` : ''}.</p>
      )}

      <dl>
        <dt>Visited</dt>
        <dd>{plan.visited_count}</dd>
        <dt>Pool size used</dt>
        <dd>{plan.pool_size_used}</dd>
        <dt>Fuel used</dt>
        <dd>
          {plan.total_fuel_cost_km_s} / {plan.fuel_budget_km_s} km/s (
          {Math.round((plan.fuel_used_fraction ?? 0) * 100)}%)
        </dd>
        <dt>Total risk collected</dt>
        <dd>{plan.total_risk_collected}</dd>
        {plan.skipped_count > 0 && (
          <>
            <dt>Skipped</dt>
            <dd>
              {plan.skipped_count} ({plan.skipped_names?.join(', ')})
            </dd>
          </>
        )}
      </dl>

      {plan.step_breakdown?.length > 0 && (
        <details>
          <summary>Step-by-step breakdown</summary>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 6 }}>
            <thead>
              <tr>
                {['From', 'To', 'Δv (km/s)', 'Arrival (days)', 'RAAN drift (°)'].map((h) => (
                  <th key={h} style={{ textAlign: 'left', borderBottom: '1px solid #555', padding: '2px 4px' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {plan.step_breakdown.map((step, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #333' }}>
                  <td style={{ padding: '2px 4px' }}>{step.from}</td>
                  <td style={{ padding: '2px 4px' }}>{step.to}</td>
                  <td style={{ padding: '2px 4px' }}>{step.delta_v_km_s}</td>
                  <td style={{ padding: '2px 4px' }}>{step.arrival_time_days ?? '—'}</td>
                  <td style={{ padding: '2px 4px' }}>{step.raan_drift_deg ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}
