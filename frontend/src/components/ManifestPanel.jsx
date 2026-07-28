export default function ManifestPanel({ debrisField, plan, selectedNoradId, onSelectDebris }) {
  const routeIds = new Set((plan?.route ?? []).map(String));
  const sorted = [...debrisField].sort((a, b) => b.risk_score - a.risk_score);

  return (
    <div className="panel">
      <p className="panel-label">manifest · risk desc</p>
      <div className="manifest-list">
        {sorted.map((d) => {
          const isSelected = String(d.norad_id) === String(selectedNoradId);
          const isInRoute = routeIds.has(String(d.norad_id));
          const isMonitorOnly = d.removal_method === 'monitor_only';
          return (
            <button
              key={d.norad_id}
              className={`manifest-row ${isSelected ? 'is-selected' : ''} ${isInRoute ? 'is-in-route' : ''} ${isMonitorOnly ? 'is-muted' : ''}`}
              onClick={() => onSelectDebris(d.norad_id)}
              title={d.removal_method.replace(/_/g, ' ')}
            >
              <span className="manifest-name">{d.norad_id} {d.object_type === 'fragment' ? 'deb' : ''}</span>
              <span className="manifest-risk">{d.risk_score.toFixed(2)}</span>
            </button>
          );
        })}
      </div>
      <p className="panel-footnote">relative ranking within this batch, not collision probability</p>
    </div>
  );
}
