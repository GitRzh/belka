import { useState, useEffect, useRef } from 'react'
import { api } from '../api'

// Field split follows CHECKPOINT.txt's "FRONTEND UX NOTES":
// required/always-visible vs advanced/collapsible.

const REMOVAL_METHOD_FILTER_OPTIONS = ['', 'robotic_arm_or_net_capture', 'net_capture']

export default function PlanForm({ onSubmit, onCompare, onChange, submitting, comparing, globeRef, presetWeights }) {
  // 'site' | 'raw' — which start-position mode is active
  const [startMode, setStartMode] = useState('site')

  // launch-site catalog fetched from GET /launch-sites
  const [siteOptions, setSiteOptions] = useState([])
  const [sitesLoading, setSitesLoading] = useState(true)

  // launch-site form state
  const [siteForm, setSiteForm] = useState({
    launch_site: '',
    inclination_deg: '',  // optional override; empty = use site default
  })

  // raw orbit form state (existing path, unchanged)
  const [required, setRequired] = useState({
    start_altitude_km: '',
    start_inclination_deg: '',
    fuel_budget_km_s: '2.5',  // C1: sensible default so the demo shows visible budget differences
  })

  const [advanced, setAdvanced] = useState({
    pool_size: '',
    risk_penalty_scale: '',
    nets_carried: '',
    max_wait_days: '',
    removal_method_filter: '',
    start_raan_deg: '',
    // weights is a nested object per the API surface — left as raw JSON
    // text for now since its shape isn't pinned down yet in the backend docs.
    weights_json: '',
  })

  // Track whether the site-pin or orbit-pin is currently placed, so we can
  // remove stale entities when re-pinning or when the form input changes.
  const sitePinnedRef  = useRef(false)
  const orbitPinnedRef = useRef(false)

  // Pin the selected launch site on the globe.
  function handlePinSite() {
    if (!globeRef?.current) return
    const siteKey = siteForm.launch_site
    const siteData = siteOptions.find(([k]) => k === siteKey)
    if (!siteData) return
    const [, site] = siteData
    globeRef.current.addPinEntity('launch-site-pin', site.lon, site.lat, 0, 'site')
    globeRef.current.flyTo(site.lon, site.lat, 0)
    sitePinnedRef.current = true
  }

  // Pin the custom orbit position on the globe (calls backend for lat/lon).
  async function handlePinOrbit() {
    if (!globeRef?.current) return
    const alt  = Number(required.start_altitude_km)
    const incl = Number(required.start_inclination_deg)
    const raan = Number(advanced.start_raan_deg) || 0
    if (!alt || !incl) return  // nothing to show yet
    try {
      const pos = await api.previewOrbit({
        altitude_km:    alt,
        inclination_deg: incl,
        raan_deg:        raan,
        time_iso:        new Date().toISOString(),
      })
      globeRef.current.addPinEntity('orbit-pin', pos.lon, pos.lat, alt, 'orbit')
      globeRef.current.flyTo(pos.lon, pos.lat, alt)
      orbitPinnedRef.current = true
    } catch {
      // silent — pin is best-effort visualisation; don't block the form
    }
  }

  // When a preset's weights are applied from the comparison panel, populate
  // weights_json. Only weights_json changes — all other fields are untouched.
  useEffect(() => {
    if (presetWeights != null) {
      updateAdvanced('weights_json', JSON.stringify(presetWeights, null, 2))
    }
  }, [presetWeights])

  useEffect(() => {
    api.getLaunchSites()
      .then((data) => {
        // data is { cape_canaveral: {...}, vandenberg: {...}, ... }
        const sorted = Object.entries(data).sort((a, b) =>
          a[1].name.localeCompare(b[1].name)
        )
        setSiteOptions(sorted)
        if (sorted.length > 0) {
          setSiteForm((prev) => ({ ...prev, launch_site: sorted[0][0] }))
        }
        setSitesLoading(false)
      })
      .catch(() => {
        // catalog unavailable — fall back to raw mode silently
        setStartMode('raw')
        setSitesLoading(false)
      })
  }, [])

  function updateRequired(field, value) {
    setRequired((prev) => ({ ...prev, [field]: value }))
  }

  function updateAdvanced(field, value) {
    setAdvanced((prev) => ({ ...prev, [field]: value }))
  }

  function handleCompare(e) {
    e.preventDefault()
    // Build the same payload as handleSubmit but dispatch to onCompare.
    const budgetNum = Number(required.fuel_budget_km_s)
    if (!required.fuel_budget_km_s || !Number.isFinite(budgetNum) || budgetNum <= 0) {
      alert('Fuel budget must be a positive number (e.g. 2.5 km/s)')
      return
    }
    const payload = { fuel_budget_km_s: budgetNum }
    if (startMode === 'site') {
      payload.launch_site = siteForm.launch_site
      if (siteForm.inclination_deg !== '') payload.inclination_deg = Number(siteForm.inclination_deg)
    } else {
      payload.start_altitude_km     = Number(required.start_altitude_km)
      payload.start_inclination_deg = Number(required.start_inclination_deg)
    }
    if (advanced.pool_size)             payload.pool_size             = Number(advanced.pool_size)
    if (advanced.nets_carried)          payload.nets_carried          = Number(advanced.nets_carried)
    if (advanced.max_wait_days)         payload.max_wait_days         = Number(advanced.max_wait_days)
    if (advanced.removal_method_filter) payload.removal_method_filter = advanced.removal_method_filter
    if (advanced.start_raan_deg !== '') payload.start_raan_deg        = Number(advanced.start_raan_deg)
    // weights intentionally omitted — /compare always uses its own 3 fixed presets.
    onCompare?.(payload)
  }

  function handleSubmit(e) {
    e.preventDefault()

    // M1: validate fuel budget before touching the API
    const budgetNum = Number(required.fuel_budget_km_s)
    if (!required.fuel_budget_km_s || !Number.isFinite(budgetNum) || budgetNum <= 0) {
      alert('Fuel budget must be a positive number (e.g. 2.5 km/s)')
      return
    }

    const payload = {
      fuel_budget_km_s: budgetNum,
    }

    if (startMode === 'site') {
      // Launch-site path: send launch_site (+ optional inclination_deg).
      // The backend model_validator resolves altitude/inclination/RAAN.
      payload.launch_site = siteForm.launch_site
      if (siteForm.inclination_deg !== '') {
        payload.inclination_deg = Number(siteForm.inclination_deg)
      }
      // altitude defaults to 800 km on the backend when using launch_site
    } else {
      // Raw orbit path: existing behaviour, unchanged.
      payload.start_altitude_km    = Number(required.start_altitude_km)
      payload.start_inclination_deg = Number(required.start_inclination_deg)
    }

    if (advanced.pool_size) payload.pool_size = Number(advanced.pool_size)
    if (advanced.risk_penalty_scale) payload.risk_penalty_scale = Number(advanced.risk_penalty_scale)
    if (advanced.nets_carried) payload.nets_carried = Number(advanced.nets_carried)
    if (advanced.max_wait_days) payload.max_wait_days = Number(advanced.max_wait_days)
    if (advanced.removal_method_filter) payload.removal_method_filter = advanced.removal_method_filter
    if (advanced.start_raan_deg !== '') payload.start_raan_deg = Number(advanced.start_raan_deg)

    if (advanced.weights_json) {
      try {
        payload.weights = JSON.parse(advanced.weights_json)
      } catch {
        alert('weights field is not valid JSON')
        return
      }
    }

    onSubmit(payload)
  }

  return (
    <form className="mission-form" onSubmit={handleSubmit}>

      {/* ── Start position toggle — full width ───────────────────── */}
      <div className="field form-grid-span" data-testid="field-start-position">
        <span>Start position</span>
        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <button
            type="button"
            className={`btn btn-toggle${startMode === 'site' ? ' btn-primary' : ''}`}
            style={{ flex: 1 }}
            onClick={() => { setStartMode('site'); onChange?.() }}
          >
            Launch site
          </button>
          <button
            type="button"
            className={`btn btn-toggle${startMode === 'raw' ? ' btn-primary' : ''}`}
            style={{ flex: 1 }}
            onClick={() => { setStartMode('raw'); onChange?.() }}
          >
            Custom orbit
          </button>
        </div>
      </div>

      {/* ── 2-column grid starts here ─────────────────────────────── */}
      <div className="form-grid" data-testid="form-grid">

        {/* ── Launch-site mode ──────────────────────────────────── */}
        {startMode === 'site' && (
          <>
            {/* Select launch site spans full width (long dropdown) */}
            <div className="field form-grid-span">
              <span>Select launch site</span>
              <div className="field-with-pin">
                <select
                  required
                  value={siteForm.launch_site}
                  disabled={sitesLoading}
                  onChange={(e) => {
                    setSiteForm((prev) => ({ ...prev, launch_site: e.target.value }))
                    if (sitePinnedRef.current) {
                      globeRef?.current?.removePinEntity('launch-site-pin')
                      sitePinnedRef.current = false
                    }
                    onChange?.()
                  }}
                >
                  {sitesLoading && <option value="">Loading sites…</option>}
                  {siteOptions.map(([key, site]) => (
                    <option key={key} value={key}>
                      {site.name} — min {site.min_inclination}° incl, {site.lat}° lat
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="pin-btn"
                  title="Pin launch site on globe"
                  disabled={sitesLoading || !siteForm.launch_site}
                  onClick={handlePinSite}
                >
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
                    <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
                  </svg>
                </button>
              </div>
            </div>

            {/* Inclination override — single col (pairs with fuel budget) */}
            <label className="field">
              Incl. override (°)
              <input
                type="number"
                step="0.1"
                placeholder="Site default"
                value={siteForm.inclination_deg}
                onChange={(e) => {
                  setSiteForm((prev) => ({ ...prev, inclination_deg: e.target.value }))
                  onChange?.()
                }}
              />
            </label>
          </>
        )}

        {/* ── Raw orbit mode ──────────────────────────────────────── */}
        {startMode === 'raw' && (
          <>
            <label className="field">
              Altitude (km)
              <input
                type="number"
                required
                value={required.start_altitude_km}
                onChange={(e) => {
                  updateRequired('start_altitude_km', e.target.value)
                  if (orbitPinnedRef.current) {
                    globeRef?.current?.removePinEntity('orbit-pin')
                    orbitPinnedRef.current = false
                  }
                }}
              />
            </label>

            <label className="field">
              Inclination (°)
              <input
                type="number"
                required
                value={required.start_inclination_deg}
                onChange={(e) => {
                  updateRequired('start_inclination_deg', e.target.value)
                  if (orbitPinnedRef.current) {
                    globeRef?.current?.removePinEntity('orbit-pin')
                    orbitPinnedRef.current = false
                  }
                }}
              />
            </label>

            {/* Pin orbit button — spans full width */}
            <div className="form-grid-span" style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="pin-btn pin-btn--orbit"
                title="Preview orbital position on globe"
                disabled={!required.start_altitude_km || !required.start_inclination_deg}
                onClick={handlePinOrbit}
              >
                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
                  <path d="M17 3.34A10 10 0 0 1 22 12.01l-.01.44A10 10 0 1 1 17 3.34zm-4.39 13.97l1.31-1.31-2.83-2.83-1.31 1.31a1 1 0 0 0 0 1.41l1.42 1.42a1 1 0 0 0 1.41 0zm4.24-4.24a1 1 0 0 0 0-1.41l-1.42-1.42a1 1 0 0 0-1.41 0l-1.31 1.31 2.83 2.83 1.31-1.31z"/>
                </svg>
                <span style={{ marginLeft: 4, fontSize: 10, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Pin orbit
                </span>
              </button>
            </div>
          </>
        )}

        {/* ── Fuel budget ─────────────────────────────────────────── */}
        <label className="field">
          Fuel budget (km/s)
          <input
            type="number"
            required
            step="0.01"
            value={required.fuel_budget_km_s}
            onChange={(e) => updateRequired('fuel_budget_km_s', e.target.value)}
          />
        </label>

        {/* ── Advanced — pool size + risk penalty pair ─────────────── */}
        <label className="field">
          Pool size
          <input
            type="number"
            placeholder="default: 40"
            value={advanced.pool_size}
            onChange={(e) => updateAdvanced('pool_size', e.target.value)}
          />
        </label>

        <label className="field">
          Risk penalty
          <input
            type="number"
            step="0.01"
            placeholder="default: 3000"
            value={advanced.risk_penalty_scale}
            onChange={(e) => updateAdvanced('risk_penalty_scale', e.target.value)}
          />
        </label>

        <label className="field">
          Nets carried
          <input
            type="number"
            placeholder="default: 1"
            value={advanced.nets_carried}
            onChange={(e) => updateAdvanced('nets_carried', e.target.value)}
          />
        </label>

        <label className="field">
          Max wait (days)
          <input
            type="number"
            step="1"
            min="0"
            max="30"
            placeholder="default: 0 (off)"
            value={advanced.max_wait_days}
            onChange={(e) => updateAdvanced('max_wait_days', e.target.value)}
          />
        </label>

        <label className="field">
          RAAN (°)
          <input
            type="number"
            step="any"
            title="Spacecraft's current orbital plane orientation. Leave blank if unknown."
            value={advanced.start_raan_deg}
            onChange={(e) => updateAdvanced('start_raan_deg', e.target.value)}
            placeholder="Optional"
          />
        </label>

        {/* Removal method spans full width (long dropdown) */}
        <label className="field form-grid-span">
          Removal method
          <select
            value={advanced.removal_method_filter}
            onChange={(e) => updateAdvanced('removal_method_filter', e.target.value)}
          >
            {REMOVAL_METHOD_FILTER_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt === '' ? 'No restriction (all methods)' : opt}
              </option>
            ))}
          </select>
        </label>

        {/* Risk weights spans full width (textarea) */}
        <label className="field form-grid-span">
          Risk weights (JSON)
          <textarea
            value={advanced.weights_json}
            onChange={(e) => updateAdvanced('weights_json', e.target.value)}
            placeholder='e.g. {"proximity": 0.5, "lifetime": 0.3, "size": 0.2}'
          />
        </label>

      </div>{/* end .form-grid */}

      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <button type="submit" className="btn btn-primary" disabled={submitting || comparing} style={{ flex: 1 }}>
          {submitting ? 'Generating plan…' : 'Generate plan'}
        </button>
        <button
          type="button"
          className="btn"
          disabled={submitting || comparing}
          style={{ flex: 1 }}
          onClick={handleCompare}
        >
          {comparing ? 'Running 3 optimizer passes…' : 'Compare Presets'}
        </button>
      </div>
    </form>
  )
}
