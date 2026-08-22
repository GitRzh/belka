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
import LaunchWindowPanel from './components/LaunchWindowPanel.jsx'
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
function EntryDetailView({ entry, entryNumber, isLatest, routeMode, activePlan, onSelectAI, onSelectNaive, onEditSelection, onReplan, onReroute, replanning, onApplyProposal, globeRef, debrisField, globePickedObject, onLegClick, onDebrisSelect, tabResult, tabParams, activeTabType }) {
  if (entry.status === 'running') {
    return <p className="history-summary" style={{ marginTop: 8 }}>Running…</p>
  }

  return (
    <>
      {/* Parameter summary — uses the active tab's own params snapshot when available */}
      {entry.kind === 'mission_cost' && (tabParams ?? entry.params) && (
        <dl className="history-params">
          {summariseParams(tabParams ?? entry.params).map(([label, value]) => (
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
      {entry.kind !== 'mission_cost' && (tabParams ?? entry.params) && (
        <dl className="history-params">
          {summariseParams(tabParams ?? entry.params).map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </Fragment>
          ))}
        </dl>
      )}

      {/* AI / Naive two-button selector — shown for plan/replan entries (not mission_cost) */}
      {entry.status === 'done' && entry.kind !== 'mission_cost' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <span className="working-sticky-label" style={{ flex: 1 }}>
            {routeMode === 'naive' ? 'Nearest-neighbour route' : 'AI-optimised route'}
          </span>
          <button
            className={`btn btn-toggle${routeMode === 'ai' ? ' btn-primary' : ''}`}
            style={{ fontSize: 11, padding: '4px 10px' }}
            onClick={isLatest ? onSelectAI : undefined}
            disabled={!isLatest && routeMode !== 'ai'}
          >
            AI
          </button>
          <button
            className={`btn btn-toggle${routeMode === 'naive' ? ' btn-primary' : ''}`}
            style={{ fontSize: 11, padding: '4px 10px' }}
            onClick={isLatest ? onSelectNaive : undefined}
            disabled={!isLatest}
            title={!isLatest ? 'Naive route not available for older entries' : undefined}
          >
            Naive
          </button>
        </div>
      )}

      {/* Route result — uses tabResult snapshot when available (non-latest tabs), else entry.result.
          Branch on activeTabType (the active tab's own type) for non-latest views so that a Plan tab
          always renders via the raw-plan path even when entry.kind has been overwritten to 'replan'. */}
      {entry.status === 'done' && (
        <>
          {(() => {
            // Determine which render branch to use for the current view.
            // isLatest: use entry.kind (live state is always correct for the latest tab).
            // non-latest: use activeTabType so a Plan tab's own shape is honoured even when
            //   entry.kind has been permanently overwritten to 'replan' by a later operation.
            const effectiveKind = isLatest ? entry.kind : (activeTabType ?? entry.kind)
            return effectiveKind === 'plan' && isLatest ? (
            <div className="history-live-result">
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
          ) : effectiveKind === 'plan' ? (
            <ReasoningPanel
              plan={tabResult ?? entry.result}
              proposals={(tabResult ?? entry.result)?.proposals}
              onApplyProposal={(proposal) => onApplyProposal?.(entry, proposal)}
              submitting={replanning}
              globeRef={globeRef}
              debrisField={debrisField}
              onLegClick={onLegClick}
              onDebrisSelect={onDebrisSelect}
            />
          ) : (effectiveKind === 'replan' || effectiveKind === 'reroute' || effectiveKind === 'fix') && isLatest ? (
            <div className="history-live-result">
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
          ) : (effectiveKind === 'replan' || effectiveKind === 'reroute' || effectiveKind === 'fix') ? (() => {
            const displayResult = tabResult ?? entry.result
            return (
              <div className="replan-result">
                {displayResult.explanation && (
                  <p className="explanation">{displayResult.explanation}</p>
                )}
                {displayResult.overrides_applied && Object.keys(displayResult.overrides_applied).length > 0 && (
                  <div className="overrides">
                    Overrides applied:{' '}
                    {Object.entries(displayResult.overrides_applied)
                      .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
                      .join(', ')}
                  </div>
                )}
                {displayResult.diff && (
                  <dl>
                    {displayResult.diff.added?.length > 0 && (
                      <><dt>Added stops</dt><dd>{displayResult.diff.added.join(', ')}</dd></>
                    )}
                    {displayResult.diff.dropped?.length > 0 && (
                      <><dt>Dropped stops</dt><dd>{displayResult.diff.dropped.join(', ')}</dd></>
                    )}
                    <dt>Fuel Δ</dt>
                    <dd>{displayResult.diff.fuel_delta_km_s > 0 ? '+' : ''}{displayResult.diff.fuel_delta_km_s} km/s</dd>
                    <dt>Risk Δ</dt>
                    <dd>{displayResult.diff.risk_delta > 0 ? '+' : ''}{displayResult.diff.risk_delta}</dd>
                  </dl>
                )}
                <ReasoningPanel plan={displayResult.new_plan} globeRef={globeRef} debrisField={debrisField} onLegClick={onLegClick} onDebrisSelect={onDebrisSelect} />
              </div>
            )
          })() : null
          })()}
          {entry.kind === 'mission_cost' && (mcResult => (
            <div className="mc-history-result">
              {entry.overridesApplied && Object.keys(entry.overridesApplied).length > 0 && (
                <div className="mc-overrides-applied">
                  Start position updated:{' '}
                  {Object.entries(entry.overridesApplied)
                    .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
                    .join(', ')}
                </div>
              )}
              {mcResult.warning && (
                <div className="mc-warning" role="alert">
                  {mcResult.warning}
                </div>
              )}
              {mcResult.explanation && (
                <p className="explanation" style={{ marginBottom: 8 }}>
                  {mcResult.explanation}
                </p>
              )}
              {mcResult.explanation_error && (
                <p style={{ marginBottom: 8, color: 'var(--color-muted, #57606a)', fontSize: 12 }}>
                  {mcResult.explanation_error}
                </p>
              )}
              <dl className="mc-stats">
                <dt>Targets</dt>
                <dd>{mcResult.visited_count}</dd>
                <dt>Fuel required</dt>
                <dd>{mcResult.total_fuel_cost_km_s} km/s</dd>
                {mcResult.total_fuel_saved_km_s > 0 && (
                  <>
                    <dt>Fuel saved by waiting</dt>
                    <dd>{mcResult.total_fuel_saved_km_s} km/s</dd>
                  </>
                )}
                <dt>Risk collected</dt>
                <dd>{mcResult.total_risk_collected}</dd>
                <dt>Nets required</dt>
                <dd>{mcResult.nets_carried_required}</dd>
              </dl>
              {mcResult.step_breakdown?.length > 0 && (
                <details className="mc-details" style={{ marginTop: 10 }}>
                  <summary>Flight manifest ({mcResult.step_breakdown.length} legs)</summary>
                  <div className="manifest-table-scroll">
                  <table className="manifest-table">
                    <thead>
                      <tr>
                        {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)', 'RAAN drift (°)', 'Wait (days)'].map((h) => (
                          <th key={h}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {mcResult.step_breakdown.map((step, i) => (
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
                  </div>
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
          ))(tabResult ?? entry.result)}
          {entry.status === 'error' && (
            <p className="history-error">{entry.error}</p>
          )}
        </>
      )}

      {/* Replan control — only available for the latest entry in its chain.
          An older entry (superseded by a newer history entry) is read-only:
          the action panel hides and the snapshot rendering above already
          shows that entry's own result. */}
      {isLatest && entry.status !== 'running' && onReplan && (
        <div className="workspace-replan" style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--c-line)' }}>
          <div className="working-sticky-label" style={{ marginBottom: 8 }}>Replan</div>
          <ReplanInput
            activePlan={activePlan}
            debrisField={debrisField}
            globePickedObject={globePickedObject}
            fuelBudgetKmS={entry.params?.fuel_budget_km_s}
            onReplan={(text) => onReplan(entry, text)}
            onReroute={(ap) => onReroute(entry, ap)}
            submitting={replanning}
          />
        </div>
      )}
    </>
  )
}

// Standalone helper (must be outside component so EntryDetailView can use it)
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
  if (params.pool_size != null && params.pool_size !== DEFAULT_POOL_SIZE)
    pairs.push(['pool', params.pool_size])
  if (params.weights)
    pairs.push(['weights', Object.entries(params.weights).map(([k, v]) => `${k}:${v}`).join(' ')])
  if (params.user_request_text)
    pairs.push(['request', params.user_request_text])
  return pairs
}

// Build the one-line field summary shown in the history row.
// For PLAN: launch site + orbit shape derived from params.
// For REPLAN: the diff-explanation text from entry.result.explanation — this is
//   the same object /replan returns, already consumed by the reasoning panel.
//   No duplicate logic: we read entry.result.explanation, not re-derive it.
// For REROUTE: fuel-ceiling change if overrides_applied has it, else excluded count.
// For FIX: short label from the result explanation (same source as REPLAN).
// For MISSION_COST: targets + fuel cost.
function buildHistorySummary(entry) {
  if (entry.status === 'running') return 'Running…'
  if (entry.status === 'error') return entry.error ?? 'Failed'

  if (entry.status === 'done' && entry.kind === 'plan') {
    const p = entry.params
    if (!p) return null
    let site = p.launch_site ? p.launch_site.replace(/_/g, ' ') : null
    let orbit = null
    if (p.start_altitude_km != null) {
      orbit = `circular ${p.start_altitude_km}km`
    } else if (p.inclination_deg != null) {
      orbit = `${p.inclination_deg}° incl`
    }
    if (site && orbit) return `${site} · ${orbit}`
    if (site) return site
    if (orbit) return orbit
    return null
  }

  if (entry.status === 'done' && entry.kind === 'replan') {
    // Distinguish reroute from replan from fix using the stored opKind.
    const opKind = entry.opKind ?? 'replan'

    if (opKind === 'reroute') {
      // Reroute summary: fuel ceiling change if present, else excluded-object count.
      const overrides = entry.result?.overrides_applied ?? {}
      if (overrides.fuel_budget_km_s != null) {
        const oldBudget = entry.params?.fuel_budget_km_s
        const newBudget = overrides.fuel_budget_km_s
        if (oldBudget != null && oldBudget !== newBudget) {
          return `fuel ceiling ${oldBudget}→${newBudget} km/s`
        }
        return `fuel ceiling ${newBudget} km/s`
      }
      const excludeCount = entry.params?.exclude_norad_ids?.length
      if (excludeCount > 0) return `excluded ${excludeCount} objects`
      const raw = entry.result?.explanation ?? ''
      return raw.length > 80 ? raw.slice(0, 79) + '…' : raw || null
    }

    if (opKind === 'fix') {
      // Fix summary: short label from diff-explanation (same path as replan,
      // no new derivation logic).
      const raw = entry.result?.explanation ?? ''
      return raw.length > 80 ? raw.slice(0, 79) + '…' : raw || null
    }

    // REPLAN: weights that changed, derived from overrides_applied, with
    // fallback to the diff-explanation text already on entry.result.explanation.
    const overrides = entry.result?.overrides_applied ?? {}
    if (overrides.weights && typeof overrides.weights === 'object') {
      const parts = Object.entries(overrides.weights).map(([k, v]) => `${k} ${v}`)
      return `weights: ${parts.join(' / ')}`
    }
    // Fallback: diff-explanation text from /replan response — reuse, no duplication.
    const raw = entry.result?.explanation ?? ''
    return raw.length > 80 ? raw.slice(0, 79) + '…' : raw || null
  }

  if (entry.status === 'done' && entry.kind === 'mission_cost') {
    let s = `${entry.result.visited_count} targets · ${entry.result.total_fuel_cost_km_s} km/s`
    if (entry.result.nets_carried_required > 1) s += ` · ${entry.result.nets_carried_required} nets`
    return s
  }
  return null
}

// Format a captured ISO timestamp as a short local time string.
function formatEntryTime(isoStr) {
  if (!isoStr) return null
  try {
    const d = new Date(isoStr)
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch {
    return null
  }
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

  // activeRouteTabIdx: index into the active workspace entry's own tabs array.
  // Tabs are stored per-chain on each history entry as entry.tabs[], so switching
  // workspace entries never loses another chain's tab history.
  const [activeRouteTabIdx, setActiveRouteTabIdx] = useState(0)

  // Independent per-kind labeling counters (ref-based, not derived from the
  // last visible tab's label — that approach breaks once
  // MAX_ROUTE_REPLAN_TABS trims old tabs off the front, which would cause
  // renumbering/collisions across kinds).
  const replanCounterRef = useRef(0)
  const rerouteCounterRef = useRef(0)
  const fixCounterRef = useRef(0)

  // activeWorkspaceId — id of the history entry currently open in the Workspace section.
  // null = empty/dimmed placeholder state.
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(null)

  // Accordion panel state — which of the three columns is currently expanded
  const [activePanel, setActivePanel] = useState('parameters')

  const [planning, setPlanning] = useState(false)
  const [replanning, setReplanning] = useState(false)
  const [comparing, setComparing] = useState(false)
  const [comparisonResult, setComparisonResult] = useState(null)
  // Wrapped as { weights, seq } so that clicking the same preset twice in a row
  // always produces a new object reference — even if preset.weights is the same
  // identity — guaranteeing PlanForm's useEffect re-fires on every click.
  const [presetWeightsToApply, setPresetWeightsToApply] = useState(null)
  const presetWeightsSeqRef = useRef(0)

  // Launch-window sweep state
  const [sweeping, setSweeping] = useState(false)
  const [sweepResult, setSweepResult] = useState(null)
  // Wrapped as { date, seq } so clicking the same date twice still fires the effect.
  const [sweepLaunchDateToApply, setSweepLaunchDateToApply] = useState(null)
  const sweepLaunchDateSeqRef = useRef(0)
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
    setSweepResult(null)
  }

  const MAX_HISTORY = 20

  async function handleGeneratePlan(payload) {
    setComparisonResult(null)
    setPlanning(true)
    setFormError(null)
    setNaivePlan(null)
    const id = crypto.randomUUID()
    setHistory(h => [...h, { id, kind: 'plan', opKind: 'plan', status: 'running', params: payload, result: null, error: null, timestamp: new Date().toISOString() }].slice(-MAX_HISTORY))
    // Open the new entry immediately in the Workspace and switch to workspace panel
    setActiveWorkspaceId(id)
    setActivePanel('workspace')
    try {
      const result = await api.plan(payload)
      setPlan(result)
      setRouteMode('ai')
      // Store Plan tab on the entry itself; reset per-kind counters for this new chain.
      // params is stored on the tab so EntryDetailView can show the correct budget/weights
      // when this tab is viewed after a later replan changes entry.params.
      const planTab = { label: 'Plan', route: result.route ?? [], type: 'plan', entryId: id, result, params: payload }
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', result, tabs: [planTab] } : e))
      setActiveRouteTabIdx(0)
      replanCounterRef.current = 0
      rerouteCounterRef.current = 0
      fixCounterRef.current = 0
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

  function handleUsePlan(preset) {
    // "Use these weights" — populate PlanForm's weights_json textarea with this
    // preset's weights. Does NOT close the comparison panel, fabricate a result,
    // touch History, set plan, or navigate to workspace. The panel stays open so
    // the user can compare other presets before generating. The panel closes when
    // Generate Plan is clicked (handleGeneratePlan calls setComparisonResult(null))
    // or when the user clicks the explicit Close button.
    presetWeightsSeqRef.current += 1
    setPresetWeightsToApply({ weights: preset.weights, seq: presetWeightsSeqRef.current })
  }

  async function handleSweep(payload) {
    setSweeping(true)
    setFormError(null)
    setSweepResult(null)
    try {
      const result = await api.sweepLaunchWindow(payload)
      setSweepResult(result)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
    } finally {
      setSweeping(false)
    }
  }

  function handleSelectSweepDate(dateStr) {
    // Clicking a scatter/bar point populates PlanForm's launch_date field ONLY.
    // No auto-submit, no History entry, no navigation — mirrors "Use these weights".
    sweepLaunchDateSeqRef.current += 1
    setSweepLaunchDateToApply({ date: dateStr, seq: sweepLaunchDateSeqRef.current })
  }

  // Append a new tab to the target entry's own tabs array (per-chain storage).
  // Drops the oldest non-plan tab if the chain is at cap, Plan tab always stays.
  // kind: 'replan' | 'reroute' | 'fix' | 'mission_cost'
  // result: full API response snapshot for this tab
  // params: the effective params that produced this result (stored on the tab for Bug A)
  function appendReplanTab(newRoute, kind, entryId, result, params) {
    const counterRef = kind === 'reroute' ? rerouteCounterRef
      : kind === 'fix' ? fixCounterRef
      : replanCounterRef
    counterRef.current += 1
    const label = kind === 'reroute' ? `Reroute #${counterRef.current}`
      : kind === 'fix' ? `Fix #${counterRef.current}`
      : kind === 'mission_cost' ? `Custom #${counterRef.current}`
      : `Replan #${counterRef.current}`
    const newTab = { label, route: newRoute ?? [], type: kind, entryId, result: result ?? null, params: params ?? null }
    setHistory(prev => prev.map(e => {
      if (e.id !== entryId) return e
      const planTab = e.tabs?.[0] ?? { label: 'Plan', route: [] }
      const replanTabs = (e.tabs ?? []).slice(1)
      const trimmed = replanTabs.length >= MAX_ROUTE_REPLAN_TABS
        ? replanTabs.slice(1)
        : replanTabs
      const nextTabs = [planTab, ...trimmed, newTab]
      // Schedule active-index update to point at the new last tab.
      setActiveRouteTabIdx(nextTabs.length - 1)
      return { ...e, tabs: nextTabs }
    }))
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
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', opKind: 'replan', params: effectiveParams, result, timestamp: e.timestamp ?? new Date().toISOString() } : e))
      // Append replan tab with full result snapshot (pass effectiveParams so the tab
      // shows its own budget/weights even after a later replan changes entry.params)
      appendReplanTab(result.new_plan?.route, 'replan', id, result, effectiveParams)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setReplanning(false)
    }
  }

  // Replan for a mission_cost entry: overwrites the same entry in-place and appends a tab.
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
      // Append tab with the new costResult snapshot (mission_cost uses replanCounterRef)
      appendReplanTab(costResult.route ?? [], 'mission_cost', id, costResult, newPayload)
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

  // handleSelectAI / handleSelectNaive replace the old single toggle.
  // Only callable on the latest entry; behavior is identical to the prior toggle logic.
  function handleSelectAI() {
    setRouteMode('ai')
  }

  async function handleSelectNaive() {
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
    const mcPayload = buildMissionCostPayload(startParams, targetNoradIds, maxWaitDays)
    const mcPlanTab = { label: 'Plan', route: costResult.route ?? [], type: 'mission_cost', entryId: id, result: costResult, params: mcPayload }
    setHistory(h => [...h, {
      id,
      kind: 'mission_cost',
      opKind: 'mission_cost',
      status: 'done',
      params: mcPayload,
      result: costResult,
      error: null,
      targetNoradIds,
      startParams,
      maxWaitDays: maxWaitDays ?? '',
      timestamp: new Date().toISOString(),
      tabs: [mcPlanTab],
    }].slice(-MAX_HISTORY))
    setActiveWorkspaceId(id)
    setActivePanel('workspace')
    setActiveRouteTabIdx(0)
    replanCounterRef.current = 0
    rerouteCounterRef.current = 0
    fixCounterRef.current = 0
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

  // Dispatch replan based on entry kind.
  // text may be a string (Replan mode) or an applied_proposal object (Reroute mode).
  function handleWorkspaceReplan(entry, text) {
    if (entry.kind === 'mission_cost') {
      handleMissionCostReplan(entry, text)
    } else {
      handleReplan(entry, text)
    }
  }

  // Reroute Enabler: apply a structured { start_altitude_km, start_inclination_deg,
  // exclude_norad_ids } payload directly — no LLM text parse.
  // start_altitude_km/start_inclination_deg go through applied_proposal (bypass LLM).
  // exclude_norad_ids is a top-level ReplanRequest field, sent separately.
  async function handleWorkspaceReroute(entry, appliedProposal) {
    const { exclude_norad_ids, start_altitude_km, start_inclination_deg, fuel_budget_km_s } = appliedProposal
    setReplanning(true)
    setFormError(null)
    const id = entry.id
    const replanParams = {
      ...entry.params,
      applied_proposal: {
        start_altitude_km,
        start_inclination_deg,
        // Only include fuel_budget_km_s when the user actually set it in the
        // Reroute form — omit rather than send an unparsed/empty value so
        // the backend's existing budget carries over unchanged.
        ...(fuel_budget_km_s != null ? { fuel_budget_km_s } : {}),
      },
      exclude_norad_ids: exclude_norad_ids ?? [],
    }
    setHistory(h => h.map(e => e.id === id ? { ...e, status: 'running', result: null, error: null } : e))
    try {
      const result = await api.replan(replanParams)
      setPlan(result.new_plan)
      setRouteMode('ai')
      setNaivePlan(null)
      const effectiveParams = { ...replanParams, ...(result.overrides_applied ?? {}) }
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', opKind: 'reroute', params: effectiveParams, result, kind: 'replan', timestamp: e.timestamp ?? new Date().toISOString() } : e))
      appendReplanTab(result.new_plan?.route, 'reroute', id, result, effectiveParams)
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setReplanning(false)
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
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', opKind: 'fix', params: effectiveParams, result, kind: 'replan', timestamp: e.timestamp ?? new Date().toISOString() } : e))
      // Append fix tab with full result snapshot
      appendReplanTab(result.new_plan?.route, 'fix', id, result, effectiveParams)
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

  const activeWorkspaceEntry = activeWorkspaceId ? history.find(e => e.id === activeWorkspaceId) : null
  const latestEntry = history.length > 0 ? history[history.length - 1] : null
  const summaryPrefilledStart = customSelectionEditEntry?.startParams ?? null

  // Per-chain tabs: each entry owns its own tabs array; derive the active entry's tabs.
  const activeWorkspaceTabs = activeWorkspaceEntry?.tabs ?? []

  // Route tab strip derived values (now reading from the active entry's own tabs).
  // Only resolve to a tab route when in AI mode — naive mode bypasses the tab strip.
  const activeTabRoute = routeMode === 'ai' && activeWorkspaceTabs.length > 0
    ? activeWorkspaceTabs[Math.min(activeRouteTabIdx, activeWorkspaceTabs.length - 1)].route
    : null

  // Route recency color for the active tab's polyline.
  const routeColor = (() => {
    if (activeWorkspaceTabs.length === 0) return 'white'
    const hasAnyReplan = activeWorkspaceTabs.some(t => t.type !== 'plan' && t.type !== 'mission_cost')
    const activeTab = activeWorkspaceTabs[Math.min(activeRouteTabIdx, activeWorkspaceTabs.length - 1)]
    if (!hasAnyReplan) return 'white'
    const lastReplanIdx = activeWorkspaceTabs.reduce((best, t, i) => (t.type !== 'plan' && t.type !== 'mission_cost') ? i : best, -1)
    const activeIdx = Math.min(activeRouteTabIdx, activeWorkspaceTabs.length - 1)
    if (activeTab.type !== 'plan' && activeTab.type !== 'mission_cost' && activeIdx === lastReplanIdx) return '#B4FF00'
    return 'orange'
  })()

  // Diff highlight: for any replan tab, find debris stops that differ from the previous tab.
  const diffHighlightIds = (() => {
    if (activeWorkspaceTabs.length < 2 || activeRouteTabIdx === 0) return null
    const prevRoute = activeWorkspaceTabs[activeRouteTabIdx - 1]?.route ?? []
    const currRoute = activeWorkspaceTabs[activeRouteTabIdx]?.route ?? []
    const prevIds = new Set(prevRoute.map(noradIdFromLabel).filter(Boolean))
    const currIds = new Set(currRoute.map(noradIdFromLabel).filter(Boolean))
    const diffIds = new Set([...currIds].filter(id => !prevIds.has(id)))
    return diffIds.size > 0 ? diffIds : null
  })()

  // The active tab's result and params snapshots — used to render per-tab Workspace content.
  const activeTab = activeWorkspaceTabs.length > 0
    ? activeWorkspaceTabs[Math.min(activeRouteTabIdx, activeWorkspaceTabs.length - 1)]
    : null
  const activeTabResult = activeTab?.result ?? null
  const activeTabParams = activeTab?.params ?? null

  // isLatestInWorkspace: true only when (a) the workspace entry is the latest overall
  // AND (b) the active tab is the last in that entry's tabs (so clicking back to the
  // Plan tab in a replanned chain still shows the Plan tab's own snapshot).
  const isLastTab = activeWorkspaceTabs.length === 0 || activeRouteTabIdx >= activeWorkspaceTabs.length - 1
  const isLatestInWorkspace = activeWorkspaceEntry?.id === latestEntry?.id && isLastTab

  // displayedRouteMode: for non-latest views, always show AI snapshot regardless of
  // global routeMode state (Bug 2 defense-in-depth — state may lag behind a switch).
  const displayedRouteMode = isLatestInWorkspace ? routeMode : 'ai'

  // Select a history entry: switch workspace, reset route mode (Bug 2), and jump to
  // the last tab in that entry's own chain so the user sees the most recent result.
  function selectWorkspaceEntry(entryId) {
    if (entryId !== activeWorkspaceId) {
      setRouteMode('ai')
      setNaivePlan(null)
    }
    setActiveWorkspaceId(entryId)
    setActivePanel('workspace')
    const entryTabs = history.find(e => e.id === entryId)?.tabs ?? []
    setActiveRouteTabIdx(entryTabs.length > 0 ? entryTabs.length - 1 : 0)
  }

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

          {/* Route tab strip — shown only when there is an active workspace entry and the
              user is on the History or Workspace panel (not Parameters). */}
          {activeWorkspaceTabs.length > 0 && activeWorkspaceId !== null && activePanel !== 'parameters' && (
            <div
              className={`route-tab-strip${pinnedDebris.size >= 2 ? ' route-tab-strip--below-clear-all' : ''}`}
              data-testid="route-tab-strip"
            >
              <span className="route-tab-strip-label">Route</span>
              {activeWorkspaceTabs.map((tab, idx) => (
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
            route={activeWorkspaceId !== null && activePanel !== 'parameters'
              ? (displayedRouteMode === 'ai' ? (activeTabRoute ?? activePlan?.route) : naivePlan?.route)
              : null}
            depot={activePlan?.depot}
            routeStyle={displayedRouteMode === 'ai' ? 'solid' : 'dashed'}
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
              depotAltitudeKm={activePlan?.depot?.altitude_km}
              depotInclinationDeg={activePlan?.depot?.inclination_deg}
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

            {/* Workspace tab — always clickable.
                Sized to match the Generate Plan button (.btn-primary):
                same padding (9px 14px), font-size (12px) and font-weight — see
                .workspace-tab-sized in global.css for the exact values. */}
            <div
              className={`dashboard-tab workspace-tab-sized${activePanel === 'workspace' ? ' dashboard-tab--active' : ''}`}
              role="tab"
              tabIndex={0}
              aria-selected={activePanel === 'workspace'}
              onClick={() => setActivePanel('workspace')}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActivePanel('workspace'); } }}
              data-testid="panel-tab-workspace"
            >
              {activeWorkspaceEntry ? (
                <span className="dashboard-section-title" data-testid="workspace-title">
                  Workspace #{getEntryNumber(activeWorkspaceEntry.id)}
                </span>
              ) : (
                <span className="dashboard-section-title workspace-title--empty" data-testid="workspace-title">
                  Workspace
                </span>
              )}
              {activeWorkspaceEntry && (
                <button
                  className="workspace-close-btn"
                  data-testid="workspace-close-btn"
                  aria-label="Clear workspace"
                  onClick={(e) => { e.stopPropagation(); setActiveWorkspaceId(null) }}
                >
                  ✕
                </button>
              )}
            </div>

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
                  onSweep={handleSweep}
                  onChange={handleFormChange}
                  submitting={planning}
                  comparing={comparing}
                  sweeping={sweeping}
                  globeRef={globeRef}
                  presetWeights={presetWeightsToApply}
                  sweepLaunchDate={sweepLaunchDateToApply}
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
                      onUsePlan={(preset) => handleUsePlan(preset)}
                      onClose={() => setComparisonResult(null)}
                    />
                  </div>
                )}

                {/* LaunchWindowPanel — shown while sweeping or when result is ready. */}
                {sweeping && !sweepResult && (
                  <div style={{ marginTop: 12, padding: '10px', border: '1px solid var(--c-line)', borderRadius: 'var(--radius)' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--c-steel)' }}>
                      Sweeping launch dates…
                    </span>
                  </div>
                )}
                {sweepResult && (
                  <div style={{ marginTop: 12 }}>
                    <LaunchWindowPanel
                      result={sweepResult}
                      onSelectDate={handleSelectSweepDate}
                      onClose={() => setSweepResult(null)}
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
                  const timeStr = formatEntryTime(entry.timestamp)
                  // Derive the display label from opKind (stored per-entry) so
                  // reroute and fix entries show their real type, not just 'Mod'.
                  const kindLabel = {
                    plan: 'Plan',
                    replan: 'Replan',
                    reroute: 'Reroute',
                    fix: 'Fix',
                    mission_cost: 'Custom',
                  }[entry.opKind ?? entry.kind] ?? 'Mod'
                  return (
                    <button
                      key={entry.id}
                      className={`history-tab${isActive ? ' history-tab--active' : ''}`}
                      data-testid={`history-tab-${n}`}
                      onClick={() => selectWorkspaceEntry(entry.id)}
                    >
                      <span className="history-entry-number">#{n}</span>
                      <span className={`history-tab-kind${isActive ? ' history-tab-kind--active' : ''}`}>
                        {kindLabel}
                      </span>
                      {entry.status === 'error' && (
                        <span className="history-tab-status history-tab-status--error">Fail</span>
                      )}
                      {entry.status === 'running' && (
                        <span className="history-tab-status history-tab-status--running">…</span>
                      )}
                      {summary && entry.status !== 'running' && (
                        <span className="history-tab-summary">{summary}</span>
                      )}
                      {timeStr && (
                        <span className="history-tab-time">{timeStr} UTC</span>
                      )}
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
                    routeMode={displayedRouteMode}
                    activePlan={activePlan}
                    onSelectAI={handleSelectAI}
                    onSelectNaive={handleSelectNaive}
                    onEditSelection={handleEditSelection}
                    globeRef={globeRef}
                    debrisField={debrisField}
                    globePickedObject={activeDebrisId != null ? (debrisField.find(d => d.norad_id === activeDebrisId) ?? null) : null}
                    onReplan={handleWorkspaceReplan}
                    onReroute={handleWorkspaceReroute}
                    onApplyProposal={handleApplyProposal}
                    replanning={replanning}
                    onLegClick={handleLegClick}
                    onDebrisSelect={handleDebrisSelect}
                    tabResult={activeTabResult}
                    tabParams={activeTabParams}
                    activeTabType={activeTab?.type ?? null}
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
