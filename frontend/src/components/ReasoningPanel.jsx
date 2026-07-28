export default function ReasoningPanel({ plan, loading, showNaive }) {
  if (loading) {
    return (
      <div className="panel">
        <p className="panel-label">reasoning trace</p>
        <p className="console-line">computing route…</p>
      </div>
    );
  }

  if (!plan) {
    return (
      <div className="panel">
        <p className="panel-label">reasoning trace</p>
        <p className="console-line panel-footnote">generate a plan to see reasoning here.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <p className="panel-label">reasoning trace {showNaive ? '· naive baseline' : '· optimized'}</p>
      <div className="console">
        <p className="console-line">pool: {plan.pool_size_used} candidates</p>
        <p className="console-line">route: {plan.visited_count} targets</p>
        <p className="console-line">
          fuel: {plan.total_fuel_cost_km_s.toFixed(1)} / {plan.fuel_budget_km_s.toFixed(1)} km/s
          {' '}({Math.round(plan.fuel_used_fraction * 100)}%)
        </p>
        <p className="console-line">risk collected: {plan.total_risk_collected.toFixed(2)}</p>
        {plan.skipped_count > 0 && (
          <p className="console-line panel-footnote">skipped: {plan.skipped_count} objects</p>
        )}
        <p className="console-line console-explanation">
          {plan.explanation_error ? 'explanation unavailable this run.' : plan.explanation}
        </p>
      </div>
    </div>
  );
}
