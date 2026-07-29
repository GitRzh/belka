import { useState } from 'react'

// Field split follows CHECKPOINT.txt's "FRONTEND UX NOTES":
// required/always-visible vs advanced/collapsible.

const REMOVAL_METHOD_FILTER_OPTIONS = ['', 'robotic_arm_or_net_capture', 'net_capture']

export default function PlanForm({ onSubmit, submitting }) {
  const [required, setRequired] = useState({
    start_altitude_km: '',
    start_inclination_deg: '',
    fuel_budget_km_s: '',
  })

  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [advanced, setAdvanced] = useState({
    pool_size: '',
    risk_penalty_scale: '',
    nets_carried: '',
    removal_method_filter: '',
    target_norad_id: '',
    start_raan_deg: '',
    // weights is a nested object per the API surface — left as raw JSON
    // text for now since its shape isn't pinned down yet in the backend docs.
    weights_json: '',
  })

  function updateRequired(field, value) {
    setRequired((prev) => ({ ...prev, [field]: value }))
  }

  function updateAdvanced(field, value) {
    setAdvanced((prev) => ({ ...prev, [field]: value }))
  }

  function handleSubmit(e) {
    e.preventDefault()

    const payload = {
      start_altitude_km: Number(required.start_altitude_km),
      start_inclination_deg: Number(required.start_inclination_deg),
      fuel_budget_km_s: Number(required.fuel_budget_km_s),
    }

    if (advanced.pool_size) payload.pool_size = Number(advanced.pool_size)
    if (advanced.risk_penalty_scale) payload.risk_penalty_scale = Number(advanced.risk_penalty_scale)
    if (advanced.nets_carried) payload.nets_carried = Number(advanced.nets_carried)
    if (advanced.removal_method_filter) payload.removal_method_filter = advanced.removal_method_filter
    if (advanced.target_norad_id) payload.target_norad_id = advanced.target_norad_id
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
    <form onSubmit={handleSubmit}>
      <label>
        Start altitude (km)
        <input
          type="number"
          required
          value={required.start_altitude_km}
          onChange={(e) => updateRequired('start_altitude_km', e.target.value)}
        />
      </label>

      <label>
        Start inclination (deg)
        <input
          type="number"
          required
          value={required.start_inclination_deg}
          onChange={(e) => updateRequired('start_inclination_deg', e.target.value)}
        />
      </label>

      <label>
        Fuel budget (km/s)
        <input
          type="number"
          required
          step="0.01"
          value={required.fuel_budget_km_s}
          onChange={(e) => updateRequired('fuel_budget_km_s', e.target.value)}
        />
      </label>

      <button type="button" onClick={() => setAdvancedOpen((o) => !o)}>
        {advancedOpen ? 'Hide advanced options' : 'Advanced options'}
      </button>

      {advancedOpen && (
        <fieldset>
          <label>
            Pool size
            <input
              type="number"
              value={advanced.pool_size}
              onChange={(e) => updateAdvanced('pool_size', e.target.value)}
            />
          </label>

          <label>
            Risk penalty scale
            <input
              type="number"
              step="0.01"
              value={advanced.risk_penalty_scale}
              onChange={(e) => updateAdvanced('risk_penalty_scale', e.target.value)}
            />
          </label>

          <label>
            Nets carried
            <input
              type="number"
              value={advanced.nets_carried}
              onChange={(e) => updateAdvanced('nets_carried', e.target.value)}
            />
          </label>

          <label>
            Removal method filter
            <select
              value={advanced.removal_method_filter}
              onChange={(e) => updateAdvanced('removal_method_filter', e.target.value)}
            >
              {REMOVAL_METHOD_FILTER_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {opt || '(none)'}
                </option>
              ))}
            </select>
          </label>

          <label>
            Target NORAD ID
            <input
              type="text"
              value={advanced.target_norad_id}
              onChange={(e) => updateAdvanced('target_norad_id', e.target.value)}
            />
          </label>

          <label>
            Start RAAN (deg)
            <input
              type="number"
              step="any"
              title="Spacecraft's current orbital plane orientation. Leave blank if unknown."
              value={advanced.start_raan_deg}
              onChange={(e) => updateAdvanced('start_raan_deg', e.target.value)}
              placeholder="Leave blank if unknown"
            />
          </label>

          <label>
            Weights (raw JSON)
            <textarea
              value={advanced.weights_json}
              onChange={(e) => updateAdvanced('weights_json', e.target.value)}
              placeholder='{"risk": 0.7, "fuel": 0.3}'
            />
          </label>
        </fieldset>
      )}

      <button type="submit" disabled={submitting}>
        {submitting ? 'Generating…' : 'Generate Plan'}
      </button>
    </form>
  )
}
