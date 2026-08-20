// Handles the edge states called out in CHECKPOINT.txt:
// - explanation_error -> plan still valid, show "explanation unavailable" inline
// - warning -> surface it, don't drop it
//
// Props:
//   plan               — the route result object from /plan or /replan new_plan
//   explanationOverride — string that replaces plan.explanation when set
//   proposals          — array of validated proposal objects from /plan (only
//                        present when visited_count == 0 and at least one
//                        proposal survived validation); omit or pass [] for
//                        the normal (successful-plan) case
//   onApplyProposal    — callback(proposal) invoked when the user clicks Apply
//   submitting         — bool, disables buttons while a replan is in flight
//   globeRef           — optional ref with .flyTo(lon, lat, altKm) and
//                        .flyToLeg(fromDebris, toDebris) — when provided,
//                        leg-index numbers become clickable to pan the globe camera
//   debrisField        — optional debris array — needed alongside globeRef to resolve
//                        step label "NAME (norad_id)" to actual coordinates
//   onLegClick(step, fromNoradId, toNoradId, legIndex) — optional callback fired when
//                        a leg-index button is clicked (in addition to the flyTo pan)
//   onDebrisSelect(debris) — optional callback fired when a debris name in the
//                        manifest is clicked (mirrors App.handleDebrisSelect)

import DataQualityBadge from './DataQualityBadge.jsx'

// Parse the trailing norad_id integer out of a route label like "COSMOS 2251 (22675)".
// Returns null for depot or any label without a trailing numeric group.
function noradIdFromLabel(label) {
  const m = /\((\d+)\)$/.exec(label ?? '')
  return m ? Number(m[1]) : null
}

export default function ReasoningPanel({ plan, explanationOverride, proposals, onApplyProposal, submitting, globeRef, debrisField, onLegClick, onDebrisSelect }) {
  if (!plan) return null

  const showProposals = Array.isArray(proposals) && proposals.length > 0

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
        {plan.total_fuel_saved_km_s > 0 && (
          <>
            <dt>Fuel saved by waiting</dt>
            <dd>{plan.total_fuel_saved_km_s} km/s</dd>
          </>
        )}
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
          <div className="manifest-table-scroll">
          <table className="manifest-table">
            <thead>
              <tr>
                {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)', 'RAAN drift (°)', 'Wait (days)', 'Data'].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {plan.step_breakdown.map((step, i) => {
                // Resolve step.to → debris object → flyTo coords when globeRef present.
                const toNoradId   = noradIdFromLabel(step.to)
                const fromNoradId = noradIdFromLabel(step.from)  // null for depot
                const toDebris = (globeRef && debrisField && toNoradId != null)
                  ? debrisField.find(d => d.norad_id === toNoradId)
                  : null
                // Resolve debris objects for name-click (debrisField may be absent)
                const fromDebris = (debrisField && fromNoradId != null)
                  ? debrisField.find(d => d.norad_id === fromNoradId)
                  : null
                const toDebrisForName = (debrisField && toNoradId != null)
                  ? debrisField.find(d => d.norad_id === toNoradId)
                  : null
                // Effective from/to IDs for leg explanation: -1 for depot
                const effectiveFromId = fromNoradId ?? -1
                const effectiveToId   = toNoradId
                return (
                <tr key={i}>
                  <td className="leg-index">
                    {(toDebris || (onLegClick && effectiveToId != null)) ? (
                      <button
                        className="leg-index-btn"
                        title={`Frame leg ${i + 1} · Click for leg detail`}
                        onClick={() => {
                          // Frame both FROM and TO debris together.
                          // fromDebris is null for depot legs — flyToLeg handles that gracefully.
                          globeRef?.current?.flyToLeg(fromDebris, toDebris ?? toDebrisForName)
                          // New: open leg detail panel
                          if (onLegClick && effectiveToId != null) {
                            onLegClick(step, effectiveFromId, effectiveToId, i + 1)
                          }
                        }}
                      >
                        {String(i + 1).padStart(2, '0')}
                      </button>
                    ) : (
                      String(i + 1).padStart(2, '0')
                    )}
                  </td>
                  <td>
                    {fromDebris && onDebrisSelect ? (
                      <button
                        className="manifest-debris-name-btn"
                        title={`Open detail for ${fromDebris.name ?? step.from}`}
                        onClick={() => {
                          globeRef?.current?.flyTo(fromDebris.longitude, fromDebris.latitude, fromDebris.altitude_km)
                          onDebrisSelect(fromDebris)
                        }}
                      >
                        {step.from}
                      </button>
                    ) : step.from}
                  </td>
                  <td>
                    {toDebrisForName && onDebrisSelect ? (
                      <button
                        className="manifest-debris-name-btn"
                        title={`Open detail for ${toDebrisForName.name ?? step.to}`}
                        onClick={() => {
                          globeRef?.current?.flyTo(toDebrisForName.longitude, toDebrisForName.latitude, toDebrisForName.altitude_km)
                          onDebrisSelect(toDebrisForName)
                        }}
                      >
                        {step.to}
                      </button>
                    ) : step.to}
                  </td>
                  <td>{step.delta_v_km_s}</td>
                  <td>{step.arrival_time_days ?? '—'}</td>
                  <td>{step.raan_drift_deg ?? '—'}</td>
                  <td>{step.recommended_wait_days ?? '—'}</td>
                  <td><DataQualityBadge value={step.data_quality} /></td>
                </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        </details>
      )}
      {/* Proposal buttons — only rendered when visited_count == 0 AND the
          backend returned at least one validated proposal. Each button fires
          the Apply shortcut: POST /replan with applied_proposal, zero LLM parse. */}
      {showProposals && (
        <div className="proposals" style={{ marginTop: 12 }}>
          <div className="working-sticky-label" style={{ marginBottom: 6 }}>Suggested fixes</div>
          {proposals.map((p, i) => (
            <div key={i} className="proposal-item" style={{ marginBottom: 8 }}>
              <button
                className="btn btn-proposal"
                disabled={submitting}
                onClick={() => onApplyProposal?.(p)}
                style={{ width: '100%', textAlign: 'left', padding: '6px 10px' }}
              >
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 2 }}>
                  {p.proposal}
                </span>
                <span style={{ fontSize: 11, color: 'var(--color-muted, #57606a)' }}>
                  {p.reason}
                </span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
