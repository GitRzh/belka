import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import DataQualityBadge from './DataQualityBadge.jsx'

// Props:
//   step              — one step_breakdown entry from the plan:
//                       { from, to, delta_v_km_s, arrival_time_days, raan_drift_deg,
//                         recommended_wait_days, fuel_saved_km_s, data_quality }
//   fromNoradId       — integer NORAD id of the FROM node (-1 for depot)
//   toNoradId         — integer NORAD id of the TO node
//   legIndex          — 1-based display index shown in the header title
//   onClose()         — close this panel
//   depotAltitudeKm   — activePlan.depot.altitude_km; shown on the depot FROM card
//   depotInclinationDeg — activePlan.depot.inclination_deg; shown on the depot FROM card
function Row({ label, value }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="debris-modal-row">
      <span className="debris-modal-label">{label}</span>
      <span className="debris-modal-value">{value}</span>
    </div>
  )
}

function SectionHeading({ children }) {
  return <div className="debris-modal-section-heading">{children}</div>
}

// Single endpoint card — shown side by side in the FROM / TO columns.
function EndpointCard({ obj, label, depotAltitudeKm, depotInclinationDeg }) {
  if (!obj) return null
  if (obj.is_depot) {
    return (
      <div className="leg-panel-endpoint-card">
        <div className="leg-panel-endpoint-label">{label}</div>
        <div className="leg-panel-endpoint-name">Depot</div>
        <div className="leg-panel-endpoint-norad" style={{ color: 'var(--c-steel)', fontSize: 11 }}>
          spacecraft start
        </div>
        <Row label="Altitude" value={depotAltitudeKm != null ? `${depotAltitudeKm} km` : null} />
        <Row label="Inclination" value={depotInclinationDeg != null ? `${depotInclinationDeg}°` : null} />
      </div>
    )
  }
  return (
    <div className="leg-panel-endpoint-card">
      <div className="leg-panel-endpoint-label">{label}</div>
      <div className="leg-panel-endpoint-name">{obj.name ?? `NORAD ${obj.norad_id}`}</div>
      <div className="leg-panel-endpoint-norad">NORAD {obj.norad_id}</div>
      <div className="leg-panel-endpoint-row">
        <DataQualityBadge value={obj.data_quality} />
        {obj.epoch_age_days !== undefined && obj.epoch_age_days !== null && (
          <span className="leg-panel-endpoint-age">
            TLE {obj.epoch_age_days}d
          </span>
        )}
      </div>
      {obj.risk_score !== undefined && obj.risk_score !== null && (
        <div className="leg-panel-endpoint-risk">
          risk {Number(obj.risk_score).toFixed(4)}
        </div>
      )}
    </div>
  )
}

export default function LegDetailPanel({ step, fromNoradId, toNoradId, legIndex, onClose, depotAltitudeKm, depotInclinationDeg }) {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState(null)

  // Client-side cache keyed by "fromId:toId"
  const cacheRef = useRef(new Map())

  useEffect(() => {
    if (fromNoradId == null || toNoradId == null) return
    const key = `${fromNoradId}:${toNoradId}`
    if (cacheRef.current.has(key)) {
      setData(cacheRef.current.get(key))
      return
    }
    setLoading(true)
    setError(null)
    setData(null)
    api
      .getLegExplanation(fromNoradId, toNoradId, step)
      .then((res) => {
        cacheRef.current.set(key, res)
        setData(res)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [fromNoradId, toNoradId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!step) return null

  const hasWait = step.recommended_wait_days > 0 && step.fuel_saved_km_s > 0

  return (
    <aside className="debris-modal leg-panel reticle" role="complementary" aria-label="Leg detail">
      {/* Header */}
      <div className="debris-modal-header">
        <span className="debris-modal-title">
          Leg {String(legIndex).padStart(2, '0')} — Transfer
        </span>
        <div className="debris-modal-controls">
          <button
            className="btn debris-modal-close"
            onClick={onClose}
            aria-label="Close leg detail panel"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="debris-modal-body">

        {/* ── FROM / TO cards ───────────────────────────────────────── */}
        <div className="leg-panel-endpoints">
          <EndpointCard obj={data?.from_obj ?? (fromNoradId === -1 ? { is_depot: true } : null)} label="FROM" depotAltitudeKm={depotAltitudeKm} depotInclinationDeg={depotInclinationDeg} />
          <div className="leg-panel-endpoints-arrow">→</div>
          <EndpointCard obj={data?.to_obj ?? null} label="TO" />
        </div>

        {/* ── Leg math ──────────────────────────────────────────────── */}
        <SectionHeading>Transfer cost</SectionHeading>
        <Row label="Delta-v" value={`${step.delta_v_km_s} km/s`} />
        <Row label="Arrival (mission day)" value={step.arrival_time_days != null ? `${step.arrival_time_days} days` : null} />
        <Row label="RAAN drift" value={step.raan_drift_deg != null && step.raan_drift_deg !== 0 ? `${step.raan_drift_deg}°` : null} />

        {hasWait && (
          <>
            <SectionHeading>J2 nodal drift wait</SectionHeading>
            <Row label="Wait" value={`${step.recommended_wait_days} day(s)`} />
            <Row label="Fuel saved" value={`${step.fuel_saved_km_s} km/s`} />
          </>
        )}

        {/* ── LLM explanation ───────────────────────────────────────── */}
        <SectionHeading>Why this cost?</SectionHeading>
        {loading && (
          <p className="debris-modal-status">Generating explanation…</p>
        )}
        {error && (
          <p className="debris-modal-status debris-modal-status--error">{error}</p>
        )}
        {!loading && !error && data && (
          data.explanation_unavailable ? (
            <p className="debris-modal-reason-unavailable">
              Explanation unavailable (LLM service unreachable).
              Leg metrics above are computed from real orbital elements.
            </p>
          ) : (
            <p className="debris-modal-reason-text">{data.explanation}</p>
          )
        )}
      </div>
    </aside>
  )
}
