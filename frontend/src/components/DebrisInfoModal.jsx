import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Friendly human labels for the removal_method enum values from removal_method.py
const REMOVAL_METHOD_LABELS = {
  robotic_arm_or_net_capture: 'Robotic arm or net capture',
  net_capture: 'Net capture',
  monitor_only: 'Monitor only (no active removal)',
}

// Maps method_maturity values to a short descriptor
const MATURITY_LABELS = {
  flight_demonstrated: 'flight-demonstrated',
  conceptual: 'conceptual',
  operational: 'operational',
}

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

// Props:
//   debris    — the debris object (from debrisField); required
//   pinned    — bool: whether this panel is currently pinned
//   onPin()   — toggle pin state
//   onClose() — close the modal
export default function DebrisInfoModal({ debris, pinned, onPin, onClose }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Fetch full detail whenever the selected debris changes
  useEffect(() => {
    if (!debris) {
      setDetail(null)
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    setDetail(null)
    api
      .getDebrisById(debris.norad_id)
      .then((data) => setDetail(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [debris?.norad_id])

  if (!debris) return null

  // Format helpers
  const fmt = (v, decimals = 2) =>
    v !== null && v !== undefined ? Number(v).toFixed(decimals) : null

  const riskBreakdown = detail
    ? [
        `proximity ${fmt(detail.proximity_score, 4)}`,
        `lifetime ${fmt(detail.lifetime_score, 4)}`,
        detail.size_score_available
          ? `size ${fmt(detail.size_score, 4)}`
          : 'size n/a',
      ].join('  ·  ')
    : null

  const possibleMethods = detail?.possible_methods?.length
    ? detail.possible_methods
        .map((m) => {
          const maturity = detail.method_maturity?.[m]
          const matLabel = maturity ? ` (${MATURITY_LABELS[maturity] ?? maturity})` : ''
          return (m === 'robotic_arm' ? 'Robotic arm' : m === 'net_capture' ? 'Net capture' : m) + matLabel
        })
        .join(', ')
    : null

  return (
    <aside className="debris-modal reticle" role="complementary" aria-label="Debris detail">
      {/* Header row: title + pin + minimize (when pinned) + close */}
      <div className="debris-modal-header">
        <span className="debris-modal-title">
          {debris.name ?? `NORAD ${debris.norad_id}`}
        </span>
        <div className="debris-modal-controls">
          <button
            className={`btn debris-modal-pin${pinned ? ' debris-modal-pin--active' : ''}`}
            onClick={onPin}
            title={pinned ? 'Unpin (remove from tab bar)' : 'Pin to tab bar (persists when viewing other debris)'}
            aria-pressed={pinned}
          >
            {pinned ? '⊙' : '○'} Pin
          </button>
          <button className="btn debris-modal-close" onClick={onClose} aria-label="Close debris panel">
            ✕
          </button>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="debris-modal-body">
        {loading && (
          <p className="debris-modal-status">Loading…</p>
        )}
        {error && (
          <p className="debris-modal-status debris-modal-status--error">{error}</p>
        )}

        {detail && (
          <>
            <SectionHeading>Identity</SectionHeading>
            <Row label="Name" value={detail.name} />
            <Row label="NORAD ID" value={detail.norad_id} />
            <Row label="Object type" value={detail.object_type === 'intact' ? 'Intact object' : 'Fragment (DEB)'} />
            <Row label="Data quality" value={detail.data_quality} />
            <Row label="TLE epoch age" value={detail.epoch_age_days !== undefined ? `${detail.epoch_age_days} days` : null} />

            <SectionHeading>Position</SectionHeading>
            <Row label="Altitude" value={detail.altitude_km !== undefined ? `${detail.altitude_km} km` : null} />
            <Row label="Latitude" value={detail.latitude !== undefined ? `${detail.latitude}°` : null} />
            <Row label="Longitude" value={detail.longitude !== undefined ? `${detail.longitude}°` : null} />

            <SectionHeading>Orbital elements</SectionHeading>
            <Row label="Inclination" value={detail.inclination_deg !== undefined ? `${detail.inclination_deg}°` : null} />
            <Row label="RAAN" value={detail.raan_deg !== undefined ? `${detail.raan_deg}°` : null} />
            <Row
              label="BSTAR (drag)"
              value={detail.bstar !== undefined ? detail.bstar.toExponential(4) : null}
            />

            <SectionHeading>Size</SectionHeading>
            <Row
              label="Radar cross-section"
              value={detail.rcs_m2 !== null && detail.rcs_m2 !== undefined ? `${detail.rcs_m2} m²` : 'Not catalogued'}
            />

            <SectionHeading>Risk assessment</SectionHeading>
            <Row label="Risk score" value={detail.risk_score !== undefined ? fmt(detail.risk_score, 4) : null} />
            <Row label="Breakdown" value={riskBreakdown} />

            <SectionHeading>Removal</SectionHeading>
            <Row
              label="Recommended method"
              value={REMOVAL_METHOD_LABELS[detail.removal_method] ?? detail.removal_method}
            />
            <Row label="Applicable techniques" value={possibleMethods} />
            {detail.removal_method_explanation && (
              <div className="debris-modal-explanation">
                {detail.removal_method_explanation}
                {detail.removal_method_explanation_source && (
                  <span className="debris-modal-explanation-source">
                    {' '}— {detail.removal_method_explanation_source}
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
