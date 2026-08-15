import { Fragment, useState, useEffect, useRef, useMemo } from 'react'
import DebrisGlobe from './components/DebrisGlobe.jsx'
import DebrisInfoModal from './components/DebrisInfoModal.jsx'
import LegDetailPanel from './components/LegDetailPanel.jsx'
import MissionClock from './components/MissionClock.jsx'
import PlanForm from './components/PlanForm.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import CustomSelectionSummary from './components/CustomSelectionSummary.jsx'
import ComparisonPanel from './components/ComparisonPanel.jsx'
import { api } from './api.js'

const REMOVAL_METHODS = [
  'robotic_arm_or_net_capture',
  'net_capture',
]

const FILTER_DEFAULTS = { minRisk: 0, methods: [] }

function FilterDropup({ filter, onChange, onClose }) {
  function setMinRisk(v) {
    const n = parseFloat(v)
    onChange({ ...filter, minRisk: Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0 })
  }
  function toggleMethod(method) {
    const methods = filter.methods.includes(method)
      ? filter.methods.filter((m) => m !== method)
      : [...filter.methods, method]
    onChange({ ...filter, methods })
  }
  const isDirty = filter.minRisk > 0 || filter.methods.length > 0
  return (
    <div className="cs-filter-dropup">
      <div className="cs-filter-header">
        <span className="cs-filter-title">FILTER DEBRIS</span>
        <button className="btn debris-modal-close" onClick={onClose} aria-label="Close filter">✕</button>
      </div>
      <div className="cs-filter-section">
        <div className="cs-filter-section-label">
          Min risk score <span className="cs-filter-hint">(0 = no gate)</span>
        </div>
        <div className="cs-filter-risk-row">
          <input
            type="range" min="0" max="1" step="0.01"
            className="cs-filter-slider"
            value={filter.minRisk}
            onChange={(e) => setMinRisk(e.target.value)}
          />
          <input
            type="number" min="0" max="1" step="0.01"
            className="cs-filter-risk-num"
            value={filter.minRisk}
            onChange={(e) => setMinRisk(e.target.value)}
          />
        </div>
      </div>
      <div className="cs-filter-section">
        <div className="cs-filter-section-label">Removal method</div>
        <div className="cs-filter-methods">
          {REMOVAL_METHODS.map((m) => (
            <label key={m} className="cs-filter-method-item">
              <input
                type="checkbox"
                checked={filter.methods.includes(m)}
                onChange={() => toggleMethod(m)}
              />
              {m}
            </label>
          ))}
        </div>
      </div>
      <button
        className="btn cs-filter-reset"
        disabled={!isDirty}
        onClick={() => onChange({ ...FILTER_DEFAULTS, methods: [] })}>
        Reset filter
      </button>
    </div>
  )
}

// Render the full detail view for a single history entry.
// Extracted so it can be used in Workspace without duplication.
function EntryDetailView({ entry, entryNumber, isLatest, routeMode, activePlan, onToggleNaive, onEditSelection, onReplan, replanning, onApplyProposal, globeRef, debrisField, onLegClick, onDebrisSelect }) {
  if (entry.status === 'running') {
    return <p className="history-summary" style={{ marginTop: 8 }}>Running…</p>
  }

  return (
    <>
      {/* Parameter summary */}
      {entry.kind === 'mission_cost' && entry.params && (
        <dl className="history-params">
          {summariseParams(entry.params).map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </Fragment>
          ))}
          {entry.targetNoradIds && (
            <Fragment key="targets">
              <dt>targets</dt>
              <dd>{entry.targetNoradIds.length} selected</dd>
            </Fragment>
          )}
        </dl>
      )}
      {entry.kind !== 'mission_cost' && entry.params && (
        <dl className="history-params">
          {summariseParams(entry.params).map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </Fragment>
          ))}
        </dl>
      )}

      {/* Live AI/Naive toggle for latest plan/replan entry */}
      {isLatest && entry.status === 'done' && entry.kind !== 'mission_cost' ? (
        <div className="history-live-result">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <span className="working-sticky-label">
              {routeMode === 'naive' ? 'Nearest-neighbour route' : 'AI-optimised route'}
            </span>
            <button className="btn btn-toggle" onClick={onToggleNaive} style={{ fontSize: 11, padding: '4px 10px' }}>
              {routeMode === 'ai' ? 'AI Route' : 'Naive Route'}
            </button>
          </div>
          <ReasoningPanel
            plan={activePlan}
            explanationOverride={
              routeMode === 'naive'
                ? 'Nearest-neighbor baseline (no AI optimization)'
                : undefined
            }
            proposals={activePlan?.proposals}
            onApplyProposal={(proposal) => onApplyProposal?.(entry, proposal)}
            submitting={replanning}
            globeRef={globeRef}
            debrisField={debrisField}
            onLegClick={onLegClick}
            onDebrisSelect={onDebrisSelect}
          />
        </div>
      ) : (
        <>
          {entry.status === 'done' && entry.kind === 'plan' && (
            <ReasoningPanel
              plan={entry.result}
              proposals={entry.result?.proposals}
              onApplyProposal={(proposal) => onApplyProposal?.(entry, proposal)}
              submitting={replanning}
              globeRef={globeRef}
              debrisField={debrisField}
              onLegClick={onLegClick}
              onDebrisSelect={onDebrisSelect}
            />
          )}
          {entry.status === 'done' && entry.kind === 'replan' && (
            <div className="replan-result">
              {entry.result.explanation && (
                <p className="explanation">{entry.result.explanation}</p>
              )}
              {entry.result.overrides_applied && Object.keys(entry.result.overrides_applied).length > 0 && (
                <div className="overrides">
                  Overrides applied:{' '}
                  {Object.entries(entry.result.overrides_applied)
                    .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
                    .join(', ')}
                </div>
              )}
              {entry.result.diff && (
                <dl>
                  {entry.result.diff.added?.length > 0 && (
                    <><dt>Added stops</dt><dd>{entry.result.diff.added.join(', ')}</dd></>
                  )}
                  {entry.result.diff.dropped?.length > 0 && (
                    <><dt>Dropped stops</dt><dd>{entry.result.diff.dropped.join(', ')}</dd></>
                  )}
                  <dt>Fuel Δ</dt>
                  <dd>{entry.result.diff.fuel_delta_km_s > 0 ? '+' : ''}{entry.result.diff.fuel_delta_km_s} km/s</dd>
                  <dt>Risk Δ</dt>
                  <dd>{entry.result.diff.risk_delta > 0 ? '+' : ''}{entry.result.diff.risk_delta}</dd>
                </dl>
              )}
              <ReasoningPanel plan={entry.result.new_plan} globeRef={globeRef} debrisField={debrisField} onLegClick={onLegClick} onDebrisSelect={onDebrisSelect} />
            </div>
          )}
          {entry.status === 'done' && entry.kind === 'mission_cost' && (
            <div className="mc-history-result">
              {entry.overridesApplied && Object.keys(entry.overridesApplied).length > 0 && (
                <div className="mc-overrides-applied">
                  Start position updated:{' '}
                  {Object.entries(entry.overridesApplied)
                    .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
                    .join(', ')}
                </div>
              )}
              {entry.result.warning && (
                <div className="mc-warning" role="alert">
                  {entry.result.warning}
                </div>
              )}
              {entry.result.explanation && (
                <p className="explanation" style={{ marginBottom: 8 }}>
                  {entry.result.explanation}
                </p>
              )}
              {entry.result.explanation_error && (
                <p style={{ marginBottom: 8, color: 'var(--color-muted, #57606a)', fontSize: 12 }}>
                  {entry.result.explanation_error}
                </p>
              )}
              <dl className="mc-stats">
                <dt>Targets</dt>
                <dd>{entry.result.visited_count}</dd>
                <dt>Fuel required</dt>
                <dd>{entry.result.total_fuel_cost_km_s} km/s</dd>
                {entry.result.total_fuel_saved_km_s > 0 && (
                  <>
                    <dt>Fuel saved by waiting</dt>
                    <dd>{entry.result.total_fuel_saved_km_s} km/s</dd>
                  </>
                )}
                <dt>Risk collected</dt>
                <dd>{entry.result.total_risk_collected}</dd>
                <dt>Nets required</dt>
                <dd>{entry.result.nets_carried_required}</dd>
              </dl>
              {entry.result.step_breakdown?.length > 0 && (
                <details className="mc-details" style={{ marginTop: 10 }}>
                  <summary>Flight manifest ({entry.result.step_breakdown.length} legs)</summary>
                  <table className="manifest-table">
                    <thead>
                      <tr>
                        {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)', 'RAAN drift (°)', 'Wait (days)'].map((h) => (
                          <th key={h}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {entry.result.step_breakdown.map((step, i) => (
                        <tr key={i}>
                          <td className="leg-index">{String(i + 1).padStart(2, '0')}</td>
                          <td>{step.from}</td>
                          <td>{step.to}</td>
                          <td>{step.delta_v_km_s}</td>
                          <td>{step.arrival_time_days ?? '—'}</td>
                          <td>{step.raan_drift_deg ?? '—'}</td>
                          <td>{step.recommended_wait_days ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              )}
              {onEditSelection && (
                <button
                  className="btn"
                  style={{ width: '100%', marginTop: 10, fontSize: 11 }}
                  onClick={() => onEditSelection(entry)}
                >
                  Edit Selection
                </button>
              )}
            </div>
          )}
          {entry.status === 'error' && (
            <p className="history-error">{entry.error}</p>
          )}
        </>
      )}

      {/* Replan control — always available in workspace view */}
      {entry.status !== 'running' && onReplan && (
        <div className="workspace-replan" style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--c-line)' }}>
          <div className="working-sticky-label" style={{ marginBottom: 8 }}>Replan</div>
          <ReplanInput
            baseEntry={entry}
            onReplan={(text) => onReplan(entry, text)}
            submitting={replanning}
          />
        </div>
      )}
    </>
  )
}

// Standalone helper (must be outside component so EntryDetailView can use it)
const DEFAULT_RISK_PENALTY_SCALE = 3000
const DEFAULT_POOL_SIZE = 40
function summariseParams(params) {
  if (!params) return []
  const pairs = []
  if (params.launch_site) {
    pairs.push(['site', params.launch_site])
    if (params.inclination_deg != null) pairs.push(['incl', `${params.inclination_deg}°`])
  } else {
    if (params.start_altitude_km != null)     pairs.push(['alt',  `${params.start_altitude_km} km`])
    if (params.start_inclination_deg != null)  pairs.push(['incl', `${params.start_inclination_deg}°`])
  }
  if (params.fuel_budget_km_s != null) pairs.push(['budget', `${params.fuel_budget_km_s} km/s`])
  if (params.risk_penalty_scale != null && params.risk_penalty_scale !== DEFAULT_RISK_PENALTY_SCALE)
    pairs.push(['risk×', params.risk_penalty_scale])
  if (params.pool_size != null && params.pool_size !== DEFAULT_POOL_SIZE)
    pairs.push(['pool', params.pool_size])
  if (params.weights)
    pairs.push(['weights', Object.entries(params.weights).map(([k, v]) => `${k}:${v}`).join(' ')])
  if (params.user_request_text)
    pairs.push(['request', params.user_request_text])
  return pairs
}

function buildHistorySummary(entry) {
  if (entry.status === 'running') return 'Running…'
  if (entry.status === 'error') return entry.error ?? 'Failed'
  if (entry.status === 'done' && entry.kind === 'plan') {
    const pct = Math.round(((entry.result.fuel_used_fraction) ?? 0) * 100)
    return `${entry.result.visited_count} of ${entry.result.pool_size_used} targets · ${entry.result.total_fuel_cost_km_s}/${entry.result.fuel_budget_km_s} km/s (${pct}%)`
  }
  if (entry.status === 'done' && entry.kind === 'replan') {
    const raw = entry.result.explanation ?? ''
    return raw.length > 90 ? raw.slice(0, 89) + '…' : raw
  }
  if (entry.status === 'done' && entry.kind === 'mission_cost') {
    let s = `${entry.result.visited_count} targets · ${entry.result.total_fuel_cost_km_s} km/s`
    if (entry.result.nets_carried_required > 1) s += ` · ${entry.result.nets_carried_required} nets`
    return s
  }
  return null
}

// Maximum number of replan tabs (not counting the pinned Plan tab).
const MAX_ROUTE_REPLAN_TABS = 5

// Parse norad id from a route label string like "COSMOS 2251 (22675)".
// Returns null for labels without a trailing numeric id (e.g. depot).
function noradIdFromLabel(label) {
  const m = /\((\d+)\)$/.exec(label)
  return m ? Number(m[1]) : null
}

export default function App() {
  const globeRef = useRef(null)

  const [debrisField, setDebrisField] = useState([])
  const [debrisFieldError, setDebrisFieldError] = useState(null)
  const [cacheMetadata, setCacheMetadata] = useState(null)

  const [pinnedDebris, setPinnedDebris] = useState(new Map())
  const [activeDebrisId, setActiveDebrisId] = useState(null)

  // activeLeg: { step, fromNoradId, toNoradId, legIndex } | null
  // Populated when a user clicks a manifest leg-index button.
  const [activeLeg, setActiveLeg] = useState(null)

  const [plan, setPlan] = useState(null)
  const [naivePlan, setNaivePlan] = useState(null)
  const [routeMode, setRouteMode] = useState('ai')
  const [focusMode, setFocusMode] = useState('dim')
  const [history, setHistory] = useState([])

  // Route tab strip — always: Plan tab (index 0) + up to MAX_ROUTE_REPLAN_TABS replan tabs.
  // Each entry: { label: string, route: string[] }
  const [routeTabs, setRouteTabs] = useState([])
  const [activeRouteTabIdx, setActiveRouteTabIdx] = useState(0)

  // activeWorkspaceId — id of the history entry currently open in the Workspace section.
  // null = empty/dimmed placeholder state.
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(null)

  // Accordion panel state — which of the three columns is currently expanded
  const [activePanel, setActivePanel] = useState('parameters')

  const [planning, setPlanning] = useState(false)
  const [replanning, setReplanning] = useState(false)
  const [comparing, setComparing] = useState(false)
  const [comparisonResult, setComparisonResult] = useState(null)
  const [formError, setFormError] = useState(null)

  // Visualization arrow panel open/closed state
  const [vizOpen, setVizOpen] = useState(false)

  // Custom selection state
  const [customSelecting, setCustomSelecting] = useState(false)
  const [customSelectedIds, setCustomSelectedIds] = useState(new Set())
  const [customSelectionDone, setCustomSelectionDone] = useState(false)
  const [customSelectionEditEntry, setCustomSelectionEditEntry] = useState(null)
  const [customFilterConfig, setCustomFilterConfig] = useState(FILTER_DEFAULTS)
  const [customFilterOpen, setCustomFilterOpen] = useState(false)

  useEffect(() => {
    api
      .getDebrisField()
      .then((res) => {
        setDebrisField(res.debris_field)
        setCacheMetadata({ data_fetched_at: res.data_fetched_at, data_stale: res.data_stale })
      })
      .catch((err) => setDebrisFieldError(err.message))
  }, [])

  function handleFormChange() {
    setPlan(null)
    setNaivePlan(null)
    setRouteMode('ai')
    setComparisonResult(null)
  }

  const MAX_HISTORY = 20

  async function handleGeneratePlan(payload) {
    setPlanning(true)
    setFormError(null)
    setNaivePlan(null)
    const id = crypto.randomUUID()
    setHistory(h => [...h, { id, kind: 'plan', status: 'running', params: payload, result: null, error: null }].slice(-MAX_HISTORY))
    // Open the new entry immediately in the Workspace and switch to workspace panel
    setActiveWorkspaceId(id)
    setActivePanel('workspace')
    try {
      const result = await api.plan(payload)
      setPlan(result)
      setRouteMode('ai')
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', result } : e))
      // Reset route tab strip to just the Plan tab
      setRouteTabs([{ label: 'Plan', route: result.route ?? [], type: 'plan' }])
      setActiveRouteTabIdx(0)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setPlanning(false)
    }
  }

  const comparePayloadRef = useRef(null)

  async function handleCompare(payload) {
    setComparing(true)
    setFormError(null)
    setComparisonResult(null)
    comparePayloadRef.current = payload
    try {
      const result = await api.compare(payload)
      setComparisonResult(result)
      // Keep parameters panel active so the comparison replaces the form area
      // (it renders in place of the normal plan output, below the form buttons).
    } catch (err) {
      setFormError(err.body?.detail || err.message)
    } finally {
      setComparing(false)
    }
  }

  function handleUsePlan(preset, payload) {
    // "Use this plan" — commit chosen preset's route to History as a 'plan' entry.
    // route_details from the preset become the canonical result, shaped like a
    // normal /plan response so EntryDetailView renders it with ReasoningPanel.
    const id = crypto.randomUUID()
    const fakeResult = {
      route: preset.route_details.map(d => d.name ? `${d.name} (${d.norad_id})` : String(d.norad_id)),
      route_details:         preset.route_details,
      visited_count:         preset.visited_count,
      total_fuel_cost_km_s:  preset.total_fuel_cost_km_s,
      total_risk_collected:  preset.total_risk_collected,
      fuel_budget_km_s:      payload?.fuel_budget_km_s,
      fuel_used_fraction:    payload?.fuel_budget_km_s
        ? preset.total_fuel_cost_km_s / payload.fuel_budget_km_s
        : 0,
      pool_size_used:        preset.visited_count,
      skipped_count:         0,
      explanation: `${preset.label} preset selected from comparison. Weights: proximity=${preset.weights.proximity}, lifetime=${preset.weights.lifetime}, size=${preset.weights.size}.`,
    }
    const entryParams = {
      ...(payload ?? {}),
      weights: preset.weights,
    }
    setHistory(h => [...h, {
      id,
      kind: 'plan',
      status: 'done',
      params: entryParams,
      result: fakeResult,
      error: null,
    }].slice(-MAX_HISTORY))
    setPlan(fakeResult)
    setRouteMode('ai')
    setRouteTabs([{ label: 'Plan', route: fakeResult.route ?? [], type: 'plan' }])
    setActiveRouteTabIdx(0)
    setActiveWorkspaceId(id)
    setActivePanel('workspace')
    setComparisonResult(null)
  }

  // Helper: append a new replan tab (drops oldest replan if over cap; Plan always stays).
  // Returns the next tabs array so callers can compute the new active index synchronously.
  function appendReplanTab(newRoute) {
    setRouteTabs(prev => {
      const planTab = prev[0] ?? { label: 'Plan', route: [] }
      const replanTabs = prev.slice(1)
      // Drop oldest replan if at cap
      const trimmed = replanTabs.length >= MAX_ROUTE_REPLAN_TABS
        ? replanTabs.slice(1)
        : replanTabs
      // Derive the next sequential replan number from the last tab's label.
      const lastReplanNum = trimmed.length > 0
        ? Number(trimmed[trimmed.length - 1].label.replace('Replan #', ''))
        : 0
      const nextTabs = [planTab, ...trimmed, { label: `Replan #${lastReplanNum + 1}`, route: newRoute ?? [], type: 'replan' }]
      // Schedule active-index update to point at the new last tab.
      setActiveRouteTabIdx(nextTabs.length - 1)
      return nextTabs
    })
  }

  // Replan now OVERWRITES the same entry in-place (no branching).
  async function handleReplan(baseEntry, userRequestText) {
    setReplanning(true)
    setFormError(null)
    const id = baseEntry.id
    const replanParams = { ...baseEntry.params, user_request_text: userRequestText }
    // Mark the entry as running in-place
    setHistory(h => h.map(e => e.id === id ? { ...e, status: 'running', params: replanParams, result: null, error: null } : e))
    try {
      const result = await api.replan(replanParams)
      setPlan(result.new_plan)
      setRouteMode('ai')
      setNaivePlan(null)
      // Fold overrides_applied back into params so handleToggleNaive uses the
      // effective post-replan values (e.g. fuel_budget_km_s after a budget change)
      // rather than the original pre-replan values.
      const effectiveParams = { ...replanParams, ...(result.overrides_applied ?? {}) }
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', params: effectiveParams, result } : e))
      // Append replan tab
      appendReplanTab(result.new_plan?.route)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setReplanning(false)
    }
  }

  // Replan for a mission_cost entry: overwrites the same entry in-place.
  async function handleMissionCostReplan(baseEntry, userRequestText) {
    setReplanning(true)
    setFormError(null)
    const id = baseEntry.id
    setHistory(h => h.map(e => e.id === id ? { ...e, status: 'running', result: null, error: null } : e))

    try {
      const baseParams = buildMissionCostPayload(baseEntry.startParams, baseEntry.targetNoradIds, baseEntry.maxWaitDays)
      const syntheticParams = { ...baseParams, fuel_budget_km_s: 1.0 }
      const replanResult = await api.replan({
        ...syntheticParams,
        user_request_text: userRequestText,
      })

      const applied = replanResult.overrides_applied ?? {}
      const startPositionKeys = new Set([
        'launch_site', 'inclination_deg',
        'start_altitude_km', 'start_inclination_deg', 'start_raan_deg',
      ])
      const startOverrides = Object.fromEntries(
        Object.entries(applied).filter(([k]) => startPositionKeys.has(k))
      )

      if (Object.keys(startOverrides).length === 0) {
        const noopMsg = 'No applicable change found for this plan type. Replan can only change starting position for Custom Selection entries — use Edit Selection to change the target list.'
        setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: noopMsg } : e))
        setReplanning(false)
        return
      }

      const newStartParams = mergeStartOverrides(baseEntry.startParams, startOverrides)
      const newPayload = buildMissionCostPayload(newStartParams, baseEntry.targetNoradIds, baseEntry.maxWaitDays)
      const costResult = await api.missionCost(newPayload)

      setHistory(h => h.map(e => e.id === id ? {
        ...e,
        status: 'done',
        params: newPayload,
        result: costResult,
        targetNoradIds: baseEntry.targetNoradIds,
        startParams: newStartParams,
        overridesApplied: startOverrides,
      } : e))
    } catch (err) {
      setHistory(h => h.map(e => e.id === id ? {
        ...e, status: 'error', error: err.body?.detail || err.message,
      } : e))
    } finally {
      setReplanning(false)
    }
  }

  function buildMissionCostPayload(startParams, targetNoradIds, maxWaitDays) {
    const payload = { target_norad_ids: targetNoradIds }
    if (startParams.mode === 'site') {
      payload.launch_site = startParams.launch_site
      if (startParams.inclination_deg !== '' && startParams.inclination_deg != null) {
        payload.inclination_deg = Number(startParams.inclination_deg)
      }
    } else {
      payload.start_altitude_km = Number(startParams.start_altitude_km)
      payload.start_inclination_deg = Number(startParams.start_inclination_deg)
      if (startParams.start_raan_deg !== '' && startParams.start_raan_deg != null) {
        payload.start_raan_deg = Number(startParams.start_raan_deg)
      }
    }
    if (maxWaitDays != null && maxWaitDays !== '') payload.max_wait_days = Number(maxWaitDays)
    return payload
  }

  function mergeStartOverrides(baseStartParams, overrides) {
    const merged = { ...baseStartParams }
    if ('launch_site' in overrides) {
      merged.mode = 'site'
      merged.launch_site = overrides.launch_site
      if ('inclination_deg' in overrides) merged.inclination_deg = String(overrides.inclination_deg ?? '')
    }
    if ('start_altitude_km' in overrides) {
      merged.mode = 'raw'
      merged.start_altitude_km = String(overrides.start_altitude_km ?? '')
    }
    if ('start_inclination_deg' in overrides) {
      merged.mode = 'raw'
      merged.start_inclination_deg = String(overrides.start_inclination_deg ?? '')
    }
    if ('start_raan_deg' in overrides) {
      merged.start_raan_deg = String(overrides.start_raan_deg ?? '')
    }
    return merged
  }

  async function handleToggleNaive() {
    if (routeMode === 'ai') {
      if (!naivePlan) {
        const latestDone = [...history].reverse().find(e => e.status === 'done')
        if (!latestDone) return
        try {
          const result = await api.getNaiveRoute(latestDone.params)
          setNaivePlan(result)
        } catch (err) {
          setFormError(err.message)
          return
        }
      }
      setRouteMode('naive')
    } else {
      setRouteMode('ai')
    }
  }

  const activePlan = routeMode === 'ai' ? plan : naivePlan

  // Stable Set of norad IDs in the current active plan's route.
  // useMemo ensures the Set reference only changes when the route content
  // actually changes — not on every App re-render.  This prevents the
  // DebrisInfoModal tab-reset effect from firing spuriously on unrelated
  // renders, which was causing the Reason tab to be immediately overwritten
  // back to Info even for debris objects that ARE in the current route.
  const activeRouteNoradIds = useMemo(() => {
    if (!activePlan) return null
    const ids = new Set()
    if (activePlan.route_details?.length) {
      for (const d of activePlan.route_details) {
        if (d.norad_id != null) ids.add(d.norad_id)
      }
    } else if (activePlan.route?.length) {
      for (const label of activePlan.route) {
        const m = label.match(/\((\d+)\)$/)
        if (m) ids.add(Number(m[1]))
      }
    }
    return ids.size > 0 ? ids : null
  // Depend on the route array/details reference — changes only when activePlan changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePlan?.route, activePlan?.route_details])

  function handleDebrisSelect(debris) {
    setActiveDebrisId(debris.norad_id)
    // Opening a debris panel supersedes the leg panel to avoid z-index conflicts.
    setActiveLeg(null)
  }

  function handleDebrisClose() {
    setActiveDebrisId(null)
  }

  function handleLegClick(step, fromNoradId, toNoradId, legIndex) {
    setActiveLeg({ step, fromNoradId, toNoradId, legIndex })
    // Close the debris modal if open so the two panels don't overlap.
    setActiveDebrisId(null)
  }

  function handleLegClose() {
    setActiveLeg(null)
  }

  function handleDebrisPin(debris) {
    const id = debris.norad_id
    setPinnedDebris(prev => {
      const next = new Map(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.set(id, debris)
      }
      return next
    })
  }

  function handlePinnedTabClose(noradId) {
    setPinnedDebris(prev => {
      const next = new Map(prev)
      next.delete(noradId)
      return next
    })
    setActiveDebrisId(prev => prev === noradId ? null : prev)
  }

  function handlePinnedTabClick(noradId) {
    setActiveDebrisId(noradId)
  }

  function handleDebrisClearAll() {
    setPinnedDebris(new Map())
    setActiveDebrisId(null)
  }

  function handleDebrisToggleSelect(debris) {
    setCustomSelectedIds(prev => {
      const next = new Set(prev)
      next.has(debris.norad_id) ? next.delete(debris.norad_id) : next.add(debris.norad_id)
      return next
    })
  }

  function handleRemoveCustomItem(noradId) {
    setCustomSelectedIds(prev => {
      const next = new Set(prev)
      next.delete(noradId)
      return next
    })
  }

  function handleConfirmMissionCost(costResult, startParams, targetNoradIds, maxWaitDays) {
    const id = crypto.randomUUID()
    setHistory(h => [...h, {
      id,
      kind: 'mission_cost',
      status: 'done',
      params: buildMissionCostPayload(startParams, targetNoradIds, maxWaitDays),
      result: costResult,
      error: null,
      targetNoradIds,
      startParams,
      maxWaitDays: maxWaitDays ?? '',
    }].slice(-MAX_HISTORY))
    setActiveWorkspaceId(id)
    setActivePanel('workspace')
    setCustomSelectionDone(false)
    setCustomSelecting(false)
    setCustomSelectedIds(new Set())
    setCustomSelectionEditEntry(null)
    setCustomFilterConfig(FILTER_DEFAULTS)
    setCustomFilterOpen(false)
  }

  function handleEditSelection(entry) {
    setCustomSelectionEditEntry(entry)
    setCustomSelectedIds(new Set(entry.targetNoradIds))
    setCustomSelecting(false)
    setCustomSelectionDone(true)
  }

  // Dispatch replan based on entry kind
  function handleWorkspaceReplan(entry, text) {
    if (entry.kind === 'mission_cost') {
      handleMissionCostReplan(entry, text)
    } else {
      handleReplan(entry, text)
    }
  }

  // Apply a constraint-resolution proposal directly via the applied_proposal shortcut.
  // Calls /replan with applied_proposal set to the proposal's params — no free-text
  // LLM parse, zero extra LLM calls.  Re-renders the same entry as any other replan.
  async function handleApplyProposal(baseEntry, proposal) {
    setReplanning(true)
    setFormError(null)
    const id = baseEntry.id
    const replanParams = {
      ...baseEntry.params,
      // applied_proposal carries the proposal's params merged with its fix_type,
      // matching the field's own docstring: "the 'params' dict … merged with its
      // fix_type".  The backend's _translate_proposal_params() uses fix_type to
      // route the param key to the correct canonical override key.
      // user_request_text is intentionally omitted — the backend accepts an empty
      // string when applied_proposal is present.
      applied_proposal: { ...proposal.params, fix_type: proposal.fix_type },
    }
    setHistory(h => h.map(e => e.id === id ? { ...e, status: 'running', result: null, error: null } : e))
    try {
      const result = await api.replan(replanParams)
      setPlan(result.new_plan)
      setRouteMode('ai')
      setNaivePlan(null)
      // Fold overrides_applied back into params so handleToggleNaive uses the
      // effective post-replan values (e.g. fuel_budget_km_s after a budget change)
      // rather than the original pre-replan values.
      const effectiveParams = { ...replanParams, ...(result.overrides_applied ?? {}) }
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', params: effectiveParams, result, kind: 'replan' } : e))
      // Append replan tab
      appendReplanTab(result.new_plan?.route)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setReplanning(false)
    }
  }

  const focusButtonsDisabled = !activePlan || !(activePlan.visited_count > 0)

  // Stable #N assignment: entry number = 1-based index in history array (never renumbered)
  function getEntryNumber(entryId) {
    return history.findIndex(e => e.id === entryId) + 1
  }

  // Route tab strip derived values.
  // Only resolve to a tab route when in AI mode — naive mode always bypasses
  // the tab strip and reads naivePlan.route directly on the globe prop below.
  const activeTabRoute = routeMode === 'ai' && routeTabs.length > 0
    ? routeTabs[Math.min(activeRouteTabIdx, routeTabs.length - 1)].route
    : null

  // Route recency color for the active tab's polyline.
  // Rule: white if plan tab with no replans; green if this is the latest replan tab;
  // orange for everything else (plan tab once any replan exists, or older replan tabs).
  const routeColor = (() => {
    if (routeTabs.length === 0) return 'white'
    const hasAnyReplan = routeTabs.some(t => t.type === 'replan')
    const activeTab = routeTabs[Math.min(activeRouteTabIdx, routeTabs.length - 1)]
    if (!hasAnyReplan) return 'white'
    const lastReplanIdx = routeTabs.reduce((best, t, i) => t.type === 'replan' ? i : best, -1)
    const activeIdx = Math.min(activeRouteTabIdx, routeTabs.length - 1)
    if (activeTab.type === 'replan' && activeIdx === lastReplanIdx) return '#B4FF00'
    return 'orange'
  })()

  // Diff highlight: for any replan tab, find debris stops that differ from the previous tab.
  const diffHighlightIds = (() => {
    if (routeTabs.length < 2 || activeRouteTabIdx === 0) return null
    const prevRoute = routeTabs[activeRouteTabIdx - 1]?.route ?? []
    const currRoute = routeTabs[activeRouteTabIdx]?.route ?? []
    const prevIds = new Set(prevRoute.map(noradIdFromLabel).filter(Boolean))
    const currIds = new Set(currRoute.map(noradIdFromLabel).filter(Boolean))
    // Stops present in current tab but not in the previous tab (added/changed).
    const diffIds = new Set([...currIds].filter(id => !prevIds.has(id)))
    return diffIds.size > 0 ? diffIds : null
  })()

  const activeWorkspaceEntry = activeWorkspaceId ? history.find(e => e.id === activeWorkspaceId) : null
  const latestEntry = history.length > 0 ? history[history.length - 1] : null
  const isLatestInWorkspace = activeWorkspaceEntry?.id === latestEntry?.id
  const summaryPrefilledStart = customSelectionEditEntry?.startParams ?? null

  if (debrisFieldError) {
    return (
      <div className="app-shell">
        <div className="panel error-panel" style={{ margin: 24 }}>
          Failed to load debris field: {debrisFieldError}
        </div>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <header className="mission-header">
        <div className="wordmark">
          Orbital<span className="wordmark-dim">–</span>Clean
        </div>
        <div className="header-meta">
          <span className="status-chip">{debrisField.length} objects tracked</span>
          <span className="header-divider" />
          <MissionClock />
        </div>
      </header>

      <div className="app-body">
        {/* ── LEFT: Globe pane (50%) ──────────────────────────────────── */}
        <div className="globe-pane reticle" style={{ position: 'relative' }}>

          {/* Route tab strip — shown once a plan exists.
              When the "Clear All" button is also visible (2+ pinned objects),
              push the strip down one row to avoid overlapping it. */}
          {routeTabs.length > 0 && (
            <div
              className={`route-tab-strip${pinnedDebris.size >= 2 ? ' route-tab-strip--below-clear-all' : ''}`}
              data-testid="route-tab-strip"
            >
              <span className="route-tab-strip-label">Route</span>
              {routeTabs.map((tab, idx) => (
                <button
                  key={idx}
                  className={`route-tab-btn${activeRouteTabIdx === idx ? ' route-tab-btn--active' : ''}`}
                  onClick={() => setActiveRouteTabIdx(idx)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}

          <DebrisGlobe
            ref={globeRef}
            debrisField={debrisField}
            route={routeMode === 'ai' ? (activeTabRoute ?? activePlan?.route) : naivePlan?.route}
            depot={activePlan?.depot}
            routeStyle={routeMode === 'ai' ? 'solid' : 'dashed'}
            routeColor={routeColor}
            cacheMetadata={cacheMetadata}
            focusMode={focusMode}
            activeDebrisId={activeDebrisId}
            pinnedIds={new Set(pinnedDebris.keys())}
            customSelecting={customSelecting}
            customSelectedIds={customSelectedIds}
            customFilterConfig={customFilterConfig}
            diffHighlightIds={diffHighlightIds}
            onDebrisSelect={handleDebrisSelect}
            onDebrisToggleSelect={handleDebrisToggleSelect}
            onBackgroundClick={() => {
              if (activeDebrisId !== null) handleDebrisClose()
            }}
          />

          {/* Visualization arrow — right edge of globe, vertically centered */}
          <div className="globe-viz-arrow" data-testid="globe-viz-arrow">
            {vizOpen && (
              <div className="globe-viz-options" data-testid="globe-viz-options">
                <span style={{
                  fontFamily: 'var(--font-display)',
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.1em',
                  color: 'var(--c-steel)',
                  padding: '0 2px 4px',
                  display: 'block',
                  borderBottom: '1px solid var(--c-line)',
                  marginBottom: 3,
                }}>Visualization</span>
                {[
                  { id: 'all',   label: 'All' },
                  { id: 'dim',   label: 'Highlight' },
                  { id: 'focus', label: 'Route only' },
                ].map(({ id, label }) => (
                  <button
                    key={id}
                    className={`btn btn-toggle${focusMode === id ? ' btn-primary' : ''}`}
                    onClick={() => setFocusMode(id)}
                    disabled={focusButtonsDisabled}
                    title={focusButtonsDisabled ? 'Generate a plan first to enable this control' : undefined}
                    data-viz-id={id}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            <button
              className="globe-viz-arrow-btn"
              data-testid="globe-viz-arrow-btn"
              aria-label={vizOpen ? 'Collapse visualization options' : 'Expand visualization options'}
              aria-expanded={vizOpen}
              onClick={() => setVizOpen(o => !o)}
            >
              {vizOpen ? '›' : '‹'}
            </button>
          </div>

          {/* Clear All — visible when 2+ objects pinned */}
          {pinnedDebris.size >= 2 && (
            <button
              className="btn debris-clear-all"
              onClick={handleDebrisClearAll}
            >
              Clear all ({pinnedDebris.size})
            </button>
          )}

          {/* Custom selection toolbar */}
          {customSelecting && customFilterOpen && (
            <FilterDropup
              filter={customFilterConfig}
              onChange={setCustomFilterConfig}
              onClose={() => setCustomFilterOpen(false)}
            />
          )}
          {customSelecting && (
            <div className="custom-selection-banner">
              <span className="custom-selection-count">{customSelectedIds.size} selected</span>
              <button
                className={`btn cs-filter-btn${
                  (customFilterConfig.minRisk > 0 || customFilterConfig.methods.length > 0)
                    ? ' cs-filter-btn--active' : ''
                }`}
                onClick={() => setCustomFilterOpen((o) => !o)}
                aria-expanded={customFilterOpen}
                aria-label="Open filter panel"
              >
                {(customFilterConfig.minRisk > 0 || customFilterConfig.methods.length > 0)
                  ? 'Filter (active)' : 'Filter'}
              </button>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setCustomSelecting(false)
                  setCustomSelectionDone(true)
                  setCustomFilterOpen(false)
                }}
              >
                Finish Selection
              </button>
              <button
                className="btn"
                onClick={() => {
                  setCustomSelecting(false)
                  setCustomSelectedIds(new Set())
                  setCustomSelectionDone(false)
                  setCustomSelectionEditEntry(null)
                  setCustomFilterConfig(FILTER_DEFAULTS)
                  setCustomFilterOpen(false)
                }}
              >
                Cancel
              </button>
            </div>
          )}

          {/* Summary card — shown after picking is done or directly from Edit Selection */}
          {customSelectionDone && !customSelecting && (
            <CustomSelectionSummary
              debrisField={debrisField}
              selectedIds={customSelectedIds}
              onRemoveItem={handleRemoveCustomItem}
              onClose={() => {
                setCustomSelectionDone(false)
                setCustomSelectedIds(new Set())
                setCustomSelectionEditEntry(null)
                setCustomFilterConfig(FILTER_DEFAULTS)
              }}
              onConfirm={handleConfirmMissionCost}
              prefilledStart={summaryPrefilledStart}
            />
          )}

          {/* Debris info modal */}
          {activeDebrisId !== null && (() => {
            const debris = debrisField.find(d => d.norad_id === activeDebrisId)
            if (!debris) return null
            const isPinned = pinnedDebris.has(activeDebrisId)
            // activeRouteNoradIds is memoized at component level — stable reference
            // that only changes when the active plan's route content changes.
            return (
              <DebrisInfoModal
                debris={debris}
                pinned={isPinned}
                onPin={() => handleDebrisPin(debris)}
                onClose={handleDebrisClose}
                activeRouteNoradIds={activeRouteNoradIds}
              />
            )
          })()}

          {/* Leg detail panel (Decision Provenance Inspector) */}
          {activeLeg !== null && (
            <LegDetailPanel
              step={activeLeg.step}
              fromNoradId={activeLeg.fromNoradId}
              toNoradId={activeLeg.toNoradId}
              legIndex={activeLeg.legIndex}
              onClose={handleLegClose}
            />
          )}

          {/* Pinned tab bar */}
          {pinnedDebris.size > 0 && (
            <div className="debris-tab-bar">
              {[...pinnedDebris.entries()].map(([id, debris]) => (
                <div
                  key={id}
                  className={`debris-tab debris-tab--pinned${activeDebrisId === id ? ' debris-tab--active' : ''}`}
                  role="button"
                  title="Click to open detail"
                  onClick={() => handlePinnedTabClick(id)}
                >
                  <span className="debris-tab-name">
                    {debris.name ?? `NORAD ${id}`}
                  </span>
                  <span className="debris-tab-risk">
                    {debris.risk_score != null ? Number(debris.risk_score).toFixed(2) : '—'}
                  </span>
                  <button
                    className="btn debris-tab-close"
                    aria-label={`Unpin ${debris.name}`}
                    title="Remove pin"
                    onClick={(e) => { e.stopPropagation(); handlePinnedTabClose(id) }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── RIGHT: Dashboard (~50%) — browser-tab bar + single content pane ── */}
        <aside className="dashboard-column" data-testid="dashboard-column">

          {/* ── TAB BAR ─────────────────────────────────────────────── */}
          <div className="dashboard-tab-bar" role="tablist">

            {/* Parameters tab */}
            <button
              className={`dashboard-tab${activePanel === 'parameters' ? ' dashboard-tab--active' : ''}`}
              role="tab"
              aria-selected={activePanel === 'parameters'}
              onClick={() => setActivePanel('parameters')}
              data-testid="panel-tab-parameters"
            >
              <span className="dashboard-section-title">Parameters</span>
            </button>

            {/* History tab */}
            <button
              className={`dashboard-tab${activePanel === 'history' ? ' dashboard-tab--active' : ''}`}
              role="tab"
              aria-selected={activePanel === 'history'}
              onClick={() => setActivePanel('history')}
              data-testid="panel-tab-history"
            >
              <span className="dashboard-section-title">History</span>
            </button>

            {/* Workspace tab — disabled until there is at least one history entry */}
            {(() => {
              const workspaceEnabled = history.length > 0
              return (
                <button
                  className={`dashboard-tab${activePanel === 'workspace' ? ' dashboard-tab--active' : ''}${!workspaceEnabled ? ' dashboard-tab--disabled' : ''}`}
                  role="tab"
                  aria-selected={activePanel === 'workspace'}
                  disabled={!workspaceEnabled}
                  onClick={() => { if (workspaceEnabled) setActivePanel('workspace') }}
                  data-testid="panel-tab-workspace"
                >
                  {activeWorkspaceEntry ? (
                    <span className="dashboard-section-title" data-testid="workspace-title">
                      Workspace #{getEntryNumber(activeWorkspaceEntry.id)}
                    </span>
                  ) : (
                    <span className={`dashboard-section-title${!workspaceEnabled ? ' workspace-title--empty' : ''}`} data-testid="workspace-title">
                      Workspace
                    </span>
                  )}
                  {activeWorkspaceEntry && (
                    <button
                      className="btn workspace-close-btn"
                      data-testid="workspace-close-btn"
                      aria-label="Clear workspace"
                      onClick={(e) => { e.stopPropagation(); setActiveWorkspaceId(null) }}
                    >
                      ✕
                    </button>
                  )}
                </button>
              )
            })()}

          </div>{/* end tab bar */}

          {/* ── CONTENT PANE ────────────────────────────────────────── */}
          <div className="dashboard-pane" data-testid="dashboard-pane">

            {/* Parameters panel */}
            {activePanel === 'parameters' && (
              <div className="dashboard-section-body" data-testid="section-parameters" style={{ padding: 10 }}>
                <div style={{ marginBottom: 10 }}>
                  <button
                    className="btn"
                    style={{ width: '100%', fontSize: 11 }}
                    disabled={customSelecting}
                    data-testid="custom-selection-btn"
                    onClick={() => {
                      setCustomSelecting(true)
                      setCustomSelectedIds(new Set())
                      setCustomSelectionDone(false)
                      setCustomSelectionEditEntry(null)
                    }}
                  >
                    Custom Selection Filter
                  </button>
                </div>

                {formError && (
                  <div className="panel error-panel" role="alert" style={{ marginBottom: 10, padding: '8px 30px 8px 10px' }}>
                    <button
                      className="error-panel-dismiss"
                      onClick={() => setFormError(null)}
                      aria-label="Dismiss error"
                    >
                      ✕
                    </button>
                    {formError}
                  </div>
                )}

                <PlanForm
                  onSubmit={handleGeneratePlan}
                  onCompare={handleCompare}
                  onChange={handleFormChange}
                  submitting={planning}
                  comparing={comparing}
                  globeRef={globeRef}
                />

                {/* ComparisonPanel — takes over the right column when a compare result is ready.
                    Dismissed when the user clicks "Use this plan" or "Close". */}
                {comparing && !comparisonResult && (
                  <div style={{ marginTop: 12, padding: '10px', border: '1px solid var(--c-line)', borderRadius: 'var(--radius)' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--c-steel)' }}>
                      Running 3 optimizer passes…
                    </span>
                  </div>
                )}
                {comparisonResult && (
                  <div style={{ marginTop: 12 }}>
                    <ComparisonPanel
                      result={comparisonResult}
                      onUsePlan={(preset) => handleUsePlan(preset, comparePayloadRef.current)}
                      onClose={() => setComparisonResult(null)}
                    />
                  </div>
                )}
              </div>
            )}

            {/* History panel */}
            {activePanel === 'history' && (
              <div className="history-tab-row" data-testid="history-tab-row">
                {history.length === 0 && (
                  <span className="history-tab-empty">No plans yet</span>
                )}
                {history.map((entry) => {
                  const n = getEntryNumber(entry.id)
                  const isActive = activeWorkspaceId === entry.id
                  const summary = buildHistorySummary(entry)
                  return (
                    <button
                      key={entry.id}
                      className={`history-tab${isActive ? ' history-tab--active' : ''}`}
                      data-testid={`history-tab-${n}`}
                      onClick={() => { setActiveWorkspaceId(entry.id); setActivePanel('workspace') }}
                      title={summary || undefined}
                    >
                      <span className="history-entry-number">#{n}</span>
                      <span className="history-tab-kind">
                        {entry.kind === 'plan' ? 'Plan'
                          : entry.kind === 'mission_cost' ? 'Custom'
                          : 'Mod'}
                      </span>
                      <span className={`history-tab-status${entry.status === 'error' ? ' history-tab-status--error' : entry.status === 'running' ? ' history-tab-status--running' : ''}`}>
                        {entry.status === 'running' ? '…' : entry.status === 'done' ? 'Done' : 'Fail'}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}

            {/* Workspace panel */}
            {activePanel === 'workspace' && (
              <div className="dashboard-section-body" data-testid="section-workspace">
                {activeWorkspaceEntry ? (
                  <EntryDetailView
                    entry={activeWorkspaceEntry}
                    entryNumber={getEntryNumber(activeWorkspaceEntry.id)}
                    isLatest={isLatestInWorkspace}
                    routeMode={routeMode}
                    activePlan={activePlan}
                    onToggleNaive={handleToggleNaive}
                    onEditSelection={handleEditSelection}
                    globeRef={globeRef}
                    debrisField={debrisField}
                    onReplan={handleWorkspaceReplan}
                    onApplyProposal={handleApplyProposal}
                    replanning={replanning}
                    onLegClick={handleLegClick}
                    onDebrisSelect={handleDebrisSelect}
                  />
                ) : (
                  <p className="workspace-empty-label" data-testid="workspace-empty-label">
                    Select a history entry or generate a new plan to view details here.
                  </p>
                )}
              </div>
            )}

          </div>{/* end content pane */}

        </aside>
      </div>
    </div>
  )
}
