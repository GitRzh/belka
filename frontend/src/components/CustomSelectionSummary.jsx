import { useState, useEffect } from 'react'
import { api } from '../api.js'

// Inline start-position form for Custom Selection.
// No fuel budget, no advanced options — just the start orbit, which is all
// /mission-cost requires.  Mirrors PlanForm's launch-site / raw-orbit toggle
// but is scoped to this one context so it can expose its values upward.
function StartPositionForm({ value, onChange, siteOptions, sitesLoading }) {
  // value shape: { mode: 'site'|'raw', launch_site, inclination_deg, start_altitude_km, start_inclination_deg, start_raan_deg }
  const { mode } = value

  function set(field, v) {
    onChange({ ...value, [field]: v })
  }

  return (
    <div className="mc-start-form">
      <div className="mc-start-label">Start position</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <button
          type="button"
          className={`btn btn-toggle${mode === 'site' ? ' btn-primary' : ''}`}
          style={{ flex: 1, fontSize: 11, padding: '5px 8px' }}
          onClick={() => set('mode', 'site')}
        >
          Launch site
        </button>
        <button
          type="button"
          className={`btn btn-toggle${mode === 'raw' ? ' btn-primary' : ''}`}
          style={{ flex: 1, fontSize: 11, padding: '5px 8px' }}
          onClick={() => set('mode', 'raw')}
        >
          Custom orbit
        </button>
      </div>

      {mode === 'site' && (
        <>
          <label className="mc-field">
            Launch site
            <select
              value={value.launch_site}
              disabled={sitesLoading}
              onChange={(e) => set('launch_site', e.target.value)}
            >
              {sitesLoading && <option value="">Loading…</option>}
              {siteOptions.map(([key, site]) => (
                <option key={key} value={key}>
                  {site.name} — {site.lat}°
                </option>
              ))}
            </select>
          </label>
          <label className="mc-field">
            Inclination override (optional)
            <input
              type="number"
              step="0.1"
              placeholder="Leave blank for site default"
              value={value.inclination_deg}
              onChange={(e) => set('inclination_deg', e.target.value)}
            />
          </label>
        </>
      )}

      {mode === 'raw' && (
        <>
          <label className="mc-field">
            Altitude (km)
            <input
              type="number"
              step="1"
              placeholder="e.g. 800"
              value={value.start_altitude_km}
              onChange={(e) => set('start_altitude_km', e.target.value)}
            />
          </label>
          <label className="mc-field">
            Inclination (deg)
            <input
              type="number"
              step="0.1"
              placeholder="e.g. 74"
              value={value.start_inclination_deg}
              onChange={(e) => set('start_inclination_deg', e.target.value)}
            />
          </label>
          <label className="mc-field">
            RAAN (deg, optional)
            <input
              type="number"
              step="any"
              placeholder="Leave blank if unknown"
              value={value.start_raan_deg}
              onChange={(e) => set('start_raan_deg', e.target.value)}
            />
          </label>
        </>
      )}
    </div>
  )
}

// Compact result view for a /mission-cost response.
// Mirrors ReasoningPanel's structure but scoped to forced-visit semantics
// (no pool_size_used, no fuel_budget, no skipped_count).
function MissionCostResult({ result }) {
  return (
    <div className="mc-result">
      {result.warning && (
        <div className="mc-warning" role="alert">
          {result.warning}
        </div>
      )}
      {result.explanation && (
        <p className="explanation" style={{ marginBottom: 8 }}>
          {result.explanation}
        </p>
      )}
      {result.explanation_error && (
        <p className="mc-explanation-error" style={{ marginBottom: 8, color: 'var(--color-muted, #57606a)', fontSize: 12 }}>
          {result.explanation_error}
        </p>
      )}
      <dl className="mc-stats">
        <dt>Targets</dt>
        <dd>{result.visited_count}</dd>
        <dt>Fuel required</dt>
        <dd>{result.total_fuel_cost_km_s} km/s</dd>
        <dt>Risk collected</dt>
        <dd>{result.total_risk_collected}</dd>
        <dt>Nets required</dt>
        <dd>{result.nets_carried_required}</dd>
      </dl>

      {result.route_details?.length > 0 && (
        <details className="mc-details">
          <summary>Visit order ({result.route_details.length} targets)</summary>
          <ol className="mc-route-list">
            {result.route_details.map((d) => (
              <li key={d.norad_id} className="mc-route-item">
                <span className="mc-route-name">{d.name}</span>
                <span className="mc-route-method">{d.removal_method}</span>
              </li>
            ))}
          </ol>
        </details>
      )}

      {result.step_breakdown?.length > 0 && (
        <details className="mc-details">
          <summary>Flight manifest ({result.step_breakdown.length} legs)</summary>
          <table className="manifest-table">
            <thead>
              <tr>
                {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)'].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.step_breakdown.map((step, i) => (
                <tr key={i}>
                  <td className="leg-index">{String(i + 1).padStart(2, '0')}</td>
                  <td>{step.from}</td>
                  <td>{step.to}</td>
                  <td>{step.delta_v_km_s}</td>
                  <td>{step.arrival_time_days ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

const EMPTY_START = {
  mode: 'site',
  launch_site: '',
  inclination_deg: '',
  start_altitude_km: '',
  start_inclination_deg: '',
  start_raan_deg: '',
}

// Props:
//   debrisField        — full debris array (for name lookup)
//   selectedIds        — Set<number> of currently selected NORAD IDs
//   onRemoveItem(id)   — remove one item from the selection
//   onClose()          — cancel & clear selection entirely
//   onConfirm(result, startParams, targetNoradIds)
//                      — user confirmed; push to history
//   prefilledStart     — optional: { mode, ... } for Edit Selection; null = fresh
export default function CustomSelectionSummary({
  debrisField,
  selectedIds,
  onRemoveItem,
  onClose,
  onConfirm,
  prefilledStart = null,
}) {
  const selected = debrisField.filter((d) => selectedIds.has(d.norad_id))

  // Site catalog for the inline start-position form
  const [siteOptions, setSiteOptions] = useState([])
  const [sitesLoading, setSitesLoading] = useState(true)

  // Start-position form state — reset to empty for fresh entry, pre-fill for Edit Selection
  const [startParams, setStartParams] = useState(prefilledStart ?? EMPTY_START)

  // Compute state
  const [computing, setComputing] = useState(false)
  const [costResult, setCostResult] = useState(null)
  const [costError, setCostError] = useState(null)

  // When prefilledStart changes (e.g. switching from fresh to edit), update form
  useEffect(() => {
    setStartParams(prefilledStart ?? EMPTY_START)
    // Clear any prior result when selection context changes
    setCostResult(null)
    setCostError(null)
  }, [prefilledStart])

  useEffect(() => {
    api.getLaunchSites()
      .then((data) => {
        const sorted = Object.entries(data).sort((a, b) => a[1].name.localeCompare(b[1].name))
        setSiteOptions(sorted)
        // Only set a default site if we're in a fresh (not prefilled) context and mode is 'site'
        if (sorted.length > 0 && !prefilledStart) {
          setStartParams((prev) =>
            prev.launch_site === '' ? { ...prev, launch_site: sorted[0][0] } : prev
          )
        }
        setSitesLoading(false)
      })
      .catch(() => {
        setSiteOptions([])
        setSitesLoading(false)
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Gating: at least 1 item selected AND start position is valid
  function startPositionValid() {
    if (startParams.mode === 'site') {
      return !!startParams.launch_site
    }
    const alt = Number(startParams.start_altitude_km)
    const incl = Number(startParams.start_inclination_deg)
    return (
      startParams.start_altitude_km !== '' && Number.isFinite(alt) &&
      startParams.start_inclination_deg !== '' && Number.isFinite(incl)
    )
  }

  const canCompute = selected.length > 0 && startPositionValid() && !computing

  async function handleCompute() {
    setCostResult(null)
    setCostError(null)
    setComputing(true)

    // Build the /mission-cost payload
    const payload = {
      target_norad_ids: selected.map((d) => d.norad_id),
    }
    if (startParams.mode === 'site') {
      payload.launch_site = startParams.launch_site
      if (startParams.inclination_deg !== '') {
        payload.inclination_deg = Number(startParams.inclination_deg)
      }
    } else {
      payload.start_altitude_km = Number(startParams.start_altitude_km)
      payload.start_inclination_deg = Number(startParams.start_inclination_deg)
      if (startParams.start_raan_deg !== '') {
        payload.start_raan_deg = Number(startParams.start_raan_deg)
      }
    }

    try {
      const result = await api.missionCost(payload)
      setCostResult(result)
    } catch (err) {
      setCostError(err.body?.detail || err.message || 'Request failed')
    } finally {
      setComputing(false)
    }
  }

  function handleConfirm() {
    onConfirm(costResult, startParams, selected.map((d) => d.norad_id))
  }

  return (
    <div className="custom-selection-summary panel reticle">
      <div className="custom-selection-summary-header">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          Custom — {selected.length} object{selected.length !== 1 ? 's' : ''}
        </span>
        <button className="btn debris-modal-close" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {selected.length === 0 ? (
        <p className="history-summary" style={{ marginTop: 8 }}>No objects selected.</p>
      ) : (
        <div className="custom-selection-list-scroll">
          <ul className="custom-selection-list">
            {selected.map((d) => (
              <li key={d.norad_id} className="custom-selection-item">
                <span className="custom-selection-name">{d.name}</span>
                <span className="custom-selection-norad">{d.norad_id}</span>
                <button
                  className="btn custom-selection-remove"
                  aria-label={`Remove ${d.name}`}
                  onClick={() => onRemoveItem(d.norad_id)}
                  title="Remove from selection"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <StartPositionForm
        value={startParams}
        onChange={(v) => { setStartParams(v); setCostResult(null); setCostError(null) }}
        siteOptions={siteOptions}
        sitesLoading={sitesLoading}
      />

      {costError && (
        <div className="mc-error" role="alert">
          {costError}
        </div>
      )}

      {costResult && <MissionCostResult result={costResult} />}

      <button
        className="btn btn-primary"
        disabled={!canCompute}
        title={
          selected.length === 0
            ? 'Select at least one debris object first.'
            : !startPositionValid()
            ? 'Fill in a start position above.'
            : undefined
        }
        style={{ width: '100%', marginTop: 6 }}
        onClick={handleCompute}
      >
        {computing ? 'Computing…' : 'Compute Mission Cost'}
      </button>

      {costResult && !computing && (
        <button
          className="btn btn-primary"
          style={{ width: '100%', marginTop: 6 }}
          onClick={handleConfirm}
        >
          Add to Plan History
        </button>
      )}
    </div>
  )
}
