import { useState, useEffect, useRef } from 'react'

// Used as a draft replan card in App.jsx.
// Props:
//   activePlan       — the current plan object; supplies route_details for prefill/checklist
//   debrisField      — full debris array; used to look up altitude_km/inclination_deg by norad_id
//   globePickedObject — debris object most recently clicked on the globe (or null);
//                       when this changes while Reroute mode is active, its orbit
//                       data is copied into the altitude/inclination inputs.
//   fuelBudgetKmS    — the base history entry's live fuel_budget_km_s (single
//                       source of truth for both the fuel-remaining ceiling label
//                       and the prefill math; not a separate stored/snapshot value).
//   onReplan(text)   — called with trimmed request text (Replan mode)
//   onReroute(ap)    — called with { start_altitude_km, start_inclination_deg,
//                       exclude_norad_ids, fuel_budget_km_s? } (Reroute mode);
//                       App wraps into applied_proposal
//   onCancel         — called when the user cancels
//   submitting       — bool, disables inputs while API call is in flight
export default function ReplanInput({
  activePlan,
  debrisField,
  globePickedObject,
  fuelBudgetKmS,
  onReplan,
  onReroute,
  onCancel,
  submitting,
}) {
  const [mode, setMode] = useState('replan')

  // ── Replan mode state ─────────────────────────────────────────────────────
  const [text, setText] = useState('')

  // ── Reroute mode state ────────────────────────────────────────────────────
  const [altKm, setAltKm] = useState('')
  const [inclDeg, setInclDeg] = useState('')
  const [fuelKm, setFuelKm] = useState('')
  const [excludedIds, setExcludedIds] = useState(new Set())

  // Track whether Reroute fields have been prefilled at least once so we
  // only overwrite them on the first mode-open, not on every render.
  const prefillDoneRef = useRef(false)

  // Non-depot route_details entries that are valid snap/exclude targets.
  // route_details has norad_id + name; orbit values come from debrisField.
  const routeObjects = (() => {
    if (!activePlan?.route_details?.length) return []
    return activePlan.route_details
      .filter((d) => d.norad_id != null && d.norad_id !== -1)
      .map((d) => {
        const full = debrisField?.find((o) => o.norad_id === d.norad_id)
        return { norad_id: d.norad_id, name: d.name, altitude_km: full?.altitude_km, inclination_deg: full?.inclination_deg }
      })
  })()

  // Prefill on first switch to Reroute mode from the LAST-VISITED object.
  useEffect(() => {
    if (mode !== 'reroute') return
    if (prefillDoneRef.current) return
    const lastObj = routeObjects[routeObjects.length - 1]
    if (lastObj?.altitude_km != null) {
      setAltKm(String(round1(lastObj.altitude_km)))
      setInclDeg(String(round1(lastObj.inclination_deg ?? 0)))
      if (lastObj.norad_id != null) {
        const fuelPrefill = fuelPrefillForNoradId(lastObj.norad_id)
        if (fuelPrefill != null) setFuelKm(String(round1(fuelPrefill)))
      }
      prefillDoneRef.current = true
    }
  // Only fires when mode first becomes 'reroute'; routeObjects is derived
  // from props that don't change between renders unless the plan changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Globe-click: when a new object is picked while Reroute mode is open,
  // copy its orbit data into the inputs. globePickedObject only carries
  // valid orbit data for real tracked objects — empty-space clicks produce
  // null, which is correctly ignored here.
  useEffect(() => {
    if (mode !== 'reroute') return
    if (!globePickedObject) return
    if (globePickedObject.altitude_km != null) {
      setAltKm(String(round1(globePickedObject.altitude_km)))
    }
    if (globePickedObject.inclination_deg != null) {
      setInclDeg(String(round1(globePickedObject.inclination_deg)))
    }
    if (globePickedObject.norad_id != null) {
      const fuelPrefill = fuelPrefillForNoradId(globePickedObject.norad_id)
      if (fuelPrefill != null) setFuelKm(String(round1(fuelPrefill)))
    }
  }, [mode, globePickedObject])

  function round1(v) { return Math.round(Number(v) * 10) / 10 }
  function round2(v) { return Math.round(Number(v) * 100) / 100 }

  // Fuel-remaining prefill math (single source of truth, derived live — no
  // separate stored ceiling/snapshot field, per the fuel ceiling/prefill fix):
  //   ceiling = fuelBudgetKmS (baseEntry.params.fuel_budget_km_s, passed in as a prop)
  //   prefill = ceiling − Σ(delta_v_km_s for legs 0..clicked-object-index)
  // Returns null when there isn't enough data to compute a prefill (caller
  // leaves the field as-is in that case, matching the altitude/inclination
  // guard pattern used elsewhere in this file).
  //
  // NOTE: route_details has no delta_v_km_s field (see optimizer.py ~line 425).
  // The per-leg costs live in step_breakdown, which is built in the same solved
  // visit order as route_details — so they align by array index (no depot entry
  // appears in either array; both start at the first real visited object).
  function fuelPrefillForNoradId(noradId) {
    if (fuelBudgetKmS == null) return null
    if (!activePlan?.route_details?.length) return null
    if (!activePlan?.step_breakdown?.length) return null
    const idx = activePlan.route_details.findIndex((d) => d.norad_id === noradId)
    if (idx === -1) return null
    // Guard: step_breakdown must have an entry for this visit index.
    if (idx >= activePlan.step_breakdown.length) return null
    const cumulativeDv = activePlan.step_breakdown
      .slice(0, idx + 1)
      .reduce((sum, s) => sum + (Number(s.delta_v_km_s) || 0), 0)
    const raw = fuelBudgetKmS - cumulativeDv
    // Clamp to [0, 15] — 15 is the hard max on the number/range inputs (Q3).
    // Using the field's own hard ceiling here avoids setting a prefill value
    // that would immediately trigger the browser's native "must be ≤ 15" error
    // when the remaining fuel is legitimately high (e.g. early in a long route
    // with a large budget).
    return Math.min(15, Math.max(0, raw))
  }

  function handleSnapTo(obj) {
    if (obj.altitude_km != null) setAltKm(String(round1(obj.altitude_km)))
    if (obj.inclination_deg != null) setInclDeg(String(round1(obj.inclination_deg)))
    if (obj.norad_id != null) {
      const fuelPrefill = fuelPrefillForNoradId(obj.norad_id)
      if (fuelPrefill != null) setFuelKm(String(round1(fuelPrefill)))
    }
  }

  function toggleExclude(noradId) {
    setExcludedIds((prev) => {
      const next = new Set(prev)
      next.has(noradId) ? next.delete(noradId) : next.add(noradId)
      return next
    })
  }

  function handleReplanSubmit(e) {
    e.preventDefault()
    if (!text.trim()) return
    onReplan(text.trim())
  }

  function handleRerouteSubmit(e) {
    e.preventDefault()
    const alt = parseFloat(altKm)
    const incl = parseFloat(inclDeg)
    if (isNaN(alt) || isNaN(incl)) return
    const payload = {
      start_altitude_km: alt,
      start_inclination_deg: incl,
      exclude_norad_ids: [...excludedIds],
    }
    // Fuel is optional — if the field isn't a valid number, omit it so the
    // backend keeps the entry's existing budget unchanged.
    const fuel = parseFloat(fuelKm)
    if (!isNaN(fuel)) {
      // Q3 hard clamp: min 0, max 15, independent of the soft ceiling label.
      payload.fuel_budget_km_s = Math.min(15, Math.max(0, fuel))
    }
    onReroute(payload)
  }

  const sharedButtons = (
    <div style={{ display: 'flex', gap: 8 }}>
      <button
        type="submit"
        className="btn btn-primary"
        disabled={submitting}
        style={{ flex: 1 }}
      >
        {submitting ? 'Applying…' : 'Apply changes'}
      </button>
      {onCancel && (
        <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
      )}
    </div>
  )

  return (
    <div className="replan">
      {/* Mode toggle */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <button
          type="button"
          className={`btn${mode === 'replan' ? ' btn-primary' : ''}`}
          style={{ flex: 1, fontSize: 12 }}
          onClick={() => setMode('replan')}
          disabled={submitting}
        >
          Replan
        </button>
        <button
          type="button"
          className={`btn${mode === 'reroute' ? ' btn-primary' : ''}`}
          style={{ flex: 1, fontSize: 12 }}
          onClick={() => setMode('reroute')}
          disabled={submitting}
        >
          Reroute
        </button>
      </div>

      {/* ── Replan mode ─────────────────────────────────────────────────── */}
      {mode === 'replan' && (
        <form onSubmit={handleReplanSubmit}>
          <label className="field">
            Describe your change
            <textarea
              placeholder='e.g. "prioritize risk over fuel"'
              value={text}
              rows={3}
              onChange={(e) => setText(e.target.value)}
              disabled={submitting}
            />
          </label>
          {sharedButtons}
        </form>
      )}

      {/* ── Reroute mode ─────────────────────────────────────────────────── */}
      {mode === 'reroute' && (
        <form onSubmit={handleRerouteSubmit}>

          {/* Altitude */}
          <label className="field" style={{ marginBottom: 6 }}>
            Start altitude (km)
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="number"
                min={200} max={2000} step={0.1}
                value={altKm}
                onChange={(e) => setAltKm(e.target.value)}
                disabled={submitting}
                style={{ width: 80 }}
              />
              <input
                type="range"
                min={200} max={2000} step={0.1}
                value={isNaN(parseFloat(altKm)) ? 200 : Math.min(2000, Math.max(200, parseFloat(altKm)))}
                onChange={(e) => setAltKm(e.target.value)}
                disabled={submitting}
                style={{ flex: 1 }}
              />
            </div>
          </label>

          {/* Fuel remaining */}
          <label className="field" style={{ marginBottom: 10 }}>
            <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span>Fuel remaining (km/s)</span>
              {fuelBudgetKmS != null && (
                <span style={{ fontSize: 10, color: 'var(--c-steel)', textTransform: 'none', letterSpacing: 'normal' }}>
                  ceiling {round2(fuelBudgetKmS)} km/s
                </span>
              )}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="number"
                min={0} max={15} step={0.1}
                value={fuelKm}
                onChange={(e) => setFuelKm(e.target.value)}
                disabled={submitting}
                style={{ width: 80 }}
              />
              <input
                type="range"
                min={0} max={15} step={0.1}
                value={isNaN(parseFloat(fuelKm)) ? 0 : Math.min(15, Math.max(0, parseFloat(fuelKm)))}
                onChange={(e) => setFuelKm(e.target.value)}
                disabled={submitting}
                style={{ flex: 1 }}
              />
            </div>
          </label>

          {/* Inclination */}
          <label className="field" style={{ marginBottom: 10 }}>
            Start inclination (deg)
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="number"
                min={0} max={180} step={0.1}
                value={inclDeg}
                onChange={(e) => setInclDeg(e.target.value)}
                disabled={submitting}
                style={{ width: 80 }}
              />
              <input
                type="range"
                min={0} max={180} step={0.1}
                value={isNaN(parseFloat(inclDeg)) ? 0 : Math.min(180, Math.max(0, parseFloat(inclDeg)))}
                onChange={(e) => setInclDeg(e.target.value)}
                disabled={submitting}
                style={{ flex: 1 }}
              />
            </div>
          </label>

          {/* Snap-to-object buttons */}
          {routeObjects.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: 'var(--c-steel)', marginBottom: 4 }}>
                Snap to object
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {routeObjects.map((obj) => (
                  <button
                    key={obj.norad_id}
                    type="button"
                    className="btn"
                    style={{ fontSize: 11, padding: '2px 7px' }}
                    onClick={() => handleSnapTo(obj)}
                    disabled={submitting || obj.altitude_km == null}
                    title={obj.altitude_km != null
                      ? `${round1(obj.altitude_km)} km / ${round2(obj.inclination_deg ?? 0)}°`
                      : 'Orbit data unavailable'}
                  >
                    Match {obj.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Globe-click hint */}
          <div style={{ fontSize: 11, color: 'var(--c-steel)', marginBottom: 10 }}>
            Or click a tracked object on the globe to copy its orbit here.
          </div>

          {/* Exclude checklist */}
          {routeObjects.length > 0 && (
            <fieldset style={{ border: '1px solid var(--c-line)', borderRadius: 'var(--radius)', padding: '6px 10px', marginBottom: 10 }}>
              <legend style={{ fontSize: 11, color: 'var(--c-steel)', padding: '0 4px' }}>
                Exclude from new route
              </legend>
              {routeObjects.map((obj) => (
                <label key={obj.norad_id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 3, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={excludedIds.has(obj.norad_id)}
                    onChange={() => toggleExclude(obj.norad_id)}
                    disabled={submitting}
                  />
                  {obj.name}
                </label>
              ))}
            </fieldset>
          )}

          {sharedButtons}
        </form>
      )}
    </div>
  )
}
