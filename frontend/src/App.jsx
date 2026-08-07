import { Fragment, useEffect, useRef, useState } from 'react'
import DebrisGlobe from './components/DebrisGlobe.jsx'
import PlanForm from './components/PlanForm.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import MissionClock from './components/MissionClock.jsx'
import DebrisInfoModal from './components/DebrisInfoModal.jsx'
import CustomSelectionSummary from './components/CustomSelectionSummary.jsx'
import { api } from './api.js'

// ── Custom Selection filter ──────────────────────────────────────────────────
// Constants sourced from app/removal_method.py — only three values exist.
const REMOVAL_METHODS = [
  { value: 'net_capture',               label: 'Net capture' },
  { value: 'robotic_arm_or_net_capture', label: 'Robotic arm / net' },
  { value: 'monitor_only',              label: 'Monitor only' },
]
const FILTER_DEFAULTS = { minRisk: 0, methods: [] }

// Drop-up filter panel. Rendered above its anchor via position:absolute.
// Closes on outside click. Passed filter state and onChange from the banner.
function FilterDropup({ filter, onChange, onClose }) {
  const panelRef = useRef(null)

  useEffect(() => {
    function handlePointerDown(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [onClose])

  function setMinRisk(v) {
    const num = Math.min(1, Math.max(0, Number(v)))
    onChange({ ...filter, minRisk: Number.isFinite(num) ? num : 0 })
  }

  function toggleMethod(method) {
    const next = filter.methods.includes(method)
      ? filter.methods.filter((m) => m !== method)
      : [...filter.methods, method]
    onChange({ ...filter, methods: next })
  }

  const isDefault = filter.minRisk === 0 && filter.methods.length === 0

  return (
    <div className="cs-filter-dropup" ref={panelRef}>
      <div className="cs-filter-header">
        <span className="cs-filter-title">FILTER</span>
        <button className="btn debris-modal-close" style={{ fontSize: 10 }} onClick={onClose} aria-label="Close filter">✕</button>
      </div>

      <div className="cs-filter-section">
        <div className="cs-filter-section-label">
          RISK SCORE <span className="cs-filter-hint">≥ threshold shown at full opacity</span>
        </div>
        <div className="cs-filter-risk-row">
          <input type="range" className="cs-filter-slider" min={0} max={1} step={0.01}
            value={filter.minRisk} onChange={(e) => setMinRisk(e.target.value)} />
          <input type="number" className="cs-filter-risk-num" min={0} max={1} step={0.01}
            value={filter.minRisk} onChange={(e) => setMinRisk(e.target.value)} />
        </div>
      </div>

      <div className="cs-filter-section">
        <div className="cs-filter-section-label">
          REMOVAL METHOD <span className="cs-filter-hint">none selected = all shown</span>
        </div>
        <div className="cs-filter-methods">
          {REMOVAL_METHODS.map(({ value, label }) => (
            <label key={value} className="cs-filter-method-item">
              <input type="checkbox" checked={filter.methods.includes(value)} onChange={() => toggleMethod(value)} />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </div>

      <button className="btn cs-filter-reset" disabled={isDefault}
        onClick={() => onChange({ ...FILTER_DEFAULTS, methods: [] })}>
        Reset filters
      </button>
    </div>
  )
}
// ────────────────────────────────────────────────────────────────────────────

export default function App() {
  const [debrisField, setDebrisField] = useState([])
  const [debrisFieldError, setDebrisFieldError] = useState(null)
  const [cacheMetadata, setCacheMetadata] = useState(null) // { data_fetched_at, data_stale }

  // Debris info panel state — simplified two-concept model:
  //   activeDebrisId  — norad_id of the debris whose modal is currently open (null = closed)
  //   pinnedDebris    — Map<noradId, debris> of explicitly-pinned objects shown in the tab bar
  //                     Pinned objects persist across clicks on other debris.
  const [pinnedDebris, setPinnedDebris] = useState(new Map())
  const [activeDebrisId, setActiveDebrisId] = useState(null)

  const [plan, setPlan] = useState(null) // current active plan shown on the globe
  const [naivePlan, setNaivePlan] = useState(null)
  const [routeMode, setRouteMode] = useState('ai') // 'ai' | 'naive'
  const [focusMode, setFocusMode] = useState('dim') // 'all' | 'dim' | 'focus'
  const [history, setHistory] = useState([]) // chronological plan/replan attempts, newest last
  const [expandedIds, setExpandedIds] = useState(new Set()) // ids of currently expanded cards

  const [planning, setPlanning] = useState(false)
  const [replanning, setReplanning] = useState(false)
  const [formError, setFormError] = useState(null)

  // Replan flow state
  const [pickingReplanBase, setPickingReplanBase] = useState(false)
  const [replanDraftBase, setReplanDraftBase] = useState(null) // entry or null

  // Custom selection state
  const [customSelecting, setCustomSelecting] = useState(false)
  const [customSelectedIds, setCustomSelectedIds] = useState(new Set())
  const [customSelectionDone, setCustomSelectionDone] = useState(false) // show summary card
  // Non-null when Edit Selection is in progress: the history entry being edited.
  // Carries prefilledStart so the summary card can restore the prior start params.
  const [customSelectionEditEntry, setCustomSelectionEditEntry] = useState(null)
  // Filter state for Custom Selection — owns the filter panel open/close and values.
  // Applied as visual dimming in DebrisGlobe while customSelecting is true.
  // Reset when custom selection is exited (not persisted).
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

  // M2: clear active plan immediately when the user changes any form input so
  // the globe never shows a stale route from a different set of parameters.
  function handleFormChange() {
    setPlan(null)
    setNaivePlan(null)
    setRouteMode('ai')
  }

  const MAX_HISTORY = 20  // L2: cap history so it never grows unbounded

  async function handleGeneratePlan(payload) {
    setPlanning(true)
    setFormError(null)
    setNaivePlan(null) // invalidate cached naive route when plan inputs change
    const id = crypto.randomUUID()
    // L2: slice to last MAX_HISTORY entries so memory stays bounded
    setHistory(h => [...h, { id, kind: 'plan', status: 'running', params: payload, result: null, error: null }].slice(-MAX_HISTORY))
    // Only the newest entry auto-expands; replaces the set instead of adding to it
    setExpandedIds(new Set([id]))
    try {
      const result = await api.plan(payload)
      setPlan(result)
      setRouteMode('ai')
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', result } : e))
    } catch (err) {
      // 404 (bad target_norad_id, or excluded-by-filter hint) and
      // 422 (bad removal_method_filter / monitor_only target) both land here —
      // err.body holds the detail message from the backend.
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setPlanning(false)
    }
  }

  // baseParams: the params of the entry being branched from (replaces lastPlanRequest)
  async function handleReplan(baseParams, userRequestText) {
    setReplanning(true)
    setFormError(null)
    const replanParams = { ...baseParams, user_request_text: userRequestText }
    const id = crypto.randomUUID()
    // L2: same cap as handleGeneratePlan
    setHistory(h => [...h, { id, kind: 'replan', status: 'running', params: replanParams, result: null, error: null }].slice(-MAX_HISTORY))
    // Only the newest entry auto-expands
    setExpandedIds(new Set([id]))
    setReplanDraftBase(null)
    try {
      const result = await api.replan(replanParams)
      setPlan(result.new_plan)
      setRouteMode('ai')
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'done', result } : e))
    } catch (err) {
      setFormError(err.body?.detail || err.message)
      setHistory(h => h.map(e => e.id === id ? { ...e, status: 'error', error: err.message } : e))
    } finally {
      setReplanning(false)
    }
  }

  // Replan for a mission_cost entry: start-position change only.
  // The LLM parses the request for start-position overrides; target_norad_ids
  // are always inherited unchanged from the base entry.
  async function handleMissionCostReplan(baseEntry, userRequestText) {
    setReplanning(true)
    setFormError(null)
    const id = crypto.randomUUID()
    setHistory(h => [...h, {
      id,
      kind: 'mission_cost',
      status: 'running',
      params: null,
      result: null,
      error: null,
      // Carry through the base IDs and start for rendering purposes
      targetNoradIds: baseEntry.targetNoradIds,
      startParams: baseEntry.startParams,
    }].slice(-MAX_HISTORY))
    setExpandedIds(new Set([id]))
    setReplanDraftBase(null)

    try {
      // Parse the user's text for start-position overrides via /replan, but
      // only extract the start-position fields we care about.
      // We pass a synthetic plan params that has the base start fields so the
      // LLM can resolve relative instructions (e.g. "higher inclination").
      const baseParams = buildMissionCostPayload(baseEntry.startParams, baseEntry.targetNoradIds)
      // Add a stub fuel_budget_km_s so /replan's PlanRequest validation passes —
      // this value is never used for the actual /mission-cost call.
      const syntheticParams = { ...baseParams, fuel_budget_km_s: 1.0 }
      const replanResult = await api.replan({
        ...syntheticParams,
        user_request_text: userRequestText,
      })

      // Extract only the start-position fields from the applied overrides
      const applied = replanResult.overrides_applied ?? {}
      const startPositionKeys = new Set([
        'launch_site', 'inclination_deg',
        'start_altitude_km', 'start_inclination_deg', 'start_raan_deg',
      ])
      const startOverrides = Object.fromEntries(
        Object.entries(applied).filter(([k]) => startPositionKeys.has(k))
      )

      if (Object.keys(startOverrides).length === 0) {
        // No applicable change for a mission_cost entry
        const noopMsg = 'No applicable change found for this plan type. Replan can only change starting position for Custom Selection entries — use Edit Selection to change the target list.'
        setHistory(h => h.map(e => e.id === id ? {
          ...e,
          status: 'error',
          error: noopMsg,
        } : e))
        setReplanning(false)
        return
      }

      // Merge start overrides into the base start params
      const newStartParams = mergeStartOverrides(baseEntry.startParams, startOverrides)
      const newPayload = buildMissionCostPayload(newStartParams, baseEntry.targetNoradIds)
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

  // Build a /mission-cost payload from startParams (the CustomSelectionSummary's
  // internal form state) and a target NORAD ID array.
  function buildMissionCostPayload(startParams, targetNoradIds) {
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
    return payload
  }

  // Merge /replan's applied start-position overrides back into startParams shape.
  function mergeStartOverrides(baseStartParams, overrides) {
    const merged = { ...baseStartParams }
    // If a launch_site key was applied, switch to site mode
    if ('launch_site' in overrides) {
      merged.mode = 'site'
      merged.launch_site = overrides.launch_site
      if ('inclination_deg' in overrides) merged.inclination_deg = String(overrides.inclination_deg ?? '')
    }
    // Raw fields
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
        // Use the latest done plan entry's params as the base
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

  // Click a debris marker → open modal for that debris.
  // Switching to a different debris simply updates activeDebrisId; modal re-renders.
  // Pinned tabs are unaffected.
  function handleDebrisSelect(debris) {
    setActiveDebrisId(debris.norad_id)
  }

  // Close the modal without removing pin.
  // If the debris was not pinned, it simply stops being tracked.
  function handleDebrisClose() {
    setActiveDebrisId(null)
  }

  // Toggle pin from the modal header or from a tab.
  // Pinning adds the debris to pinnedDebris map; unpinning removes it.
  // Modal stays open either way (activeDebrisId unchanged).
  function handleDebrisPin(debris) {
    const id = debris.norad_id
    setPinnedDebris(prev => {
      const next = new Map(prev)
      if (next.has(id)) {
        next.delete(id)    // unpin → tab disappears
      } else {
        next.set(id, debris) // pin → tab appears
      }
      return next
    })
  }

  // Close a pinned tab (X button). Removes from pinnedDebris.
  // If this tab's debris is currently active, close the modal too.
  function handlePinnedTabClose(noradId) {
    setPinnedDebris(prev => {
      const next = new Map(prev)
      next.delete(noradId)
      return next
    })
    setActiveDebrisId(prev => prev === noradId ? null : prev)
  }

  // Click a pinned tab → open modal for that debris.
  function handlePinnedTabClick(noradId) {
    setActiveDebrisId(noradId)
  }

  // Clear all pinned tabs and close modal.
  function handleDebrisClearAll() {
    setPinnedDebris(new Map())
    setActiveDebrisId(null)
  }

  // Toggle membership of a debris object in the custom selection set
  function handleDebrisToggleSelect(debris) {
    setCustomSelectedIds(prev => {
      const next = new Set(prev)
      next.has(debris.norad_id) ? next.delete(debris.norad_id) : next.add(debris.norad_id)
      return next
    })
  }

  // Remove a single item from the selection (called by the per-item X button)
  function handleRemoveCustomItem(noradId) {
    setCustomSelectedIds(prev => {
      const next = new Set(prev)
      next.delete(noradId)
      return next
    })
  }

  // Confirm a computed mission cost: push a new mission_cost history entry
  // and exit the summary card.
  function handleConfirmMissionCost(costResult, startParams, targetNoradIds) {
    const id = crypto.randomUUID()
    setHistory(h => [...h, {
      id,
      kind: 'mission_cost',
      status: 'done',
      // params: the /mission-cost payload (for Replan base compatibility)
      params: buildMissionCostPayload(startParams, targetNoradIds),
      result: costResult,
      error: null,
      // Stored for Edit Selection and Replan
      targetNoradIds,
      startParams,
    }].slice(-MAX_HISTORY))
    setExpandedIds(new Set([id]))
    // Exit custom selection mode — reset filter (not persisted)
    setCustomSelectionDone(false)
    setCustomSelecting(false)
    setCustomSelectedIds(new Set())
    setCustomSelectionEditEntry(null)
    setCustomFilterConfig(FILTER_DEFAULTS)
    setCustomFilterOpen(false)
  }

  // Edit Selection: re-enter the summary card pre-populated from a history entry
  function handleEditSelection(entry) {
    setCustomSelectionEditEntry(entry)
    setCustomSelectedIds(new Set(entry.targetNoradIds))
    setCustomSelecting(false) // not in globe-picking mode — directly to summary
    setCustomSelectionDone(true)
  }

  // Produces a compact [label, value] list for a plan/replan params object.
  // Skips defaults that add noise; always shows orbit origin and fuel budget.
  const DEFAULT_RISK_PENALTY_SCALE = 3000 // mirrors optimizer.py RISK_PENALTY_SCALE
  const DEFAULT_POOL_SIZE = 40            // mirrors cost_matrix.py DEFAULT_POOL_SIZE
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

  function toggleExpanded(id) {
    setExpandedIds(s => {
      const next = new Set(s)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // Entry-header click handler — differs based on whether we're picking a replan base
  function handleEntryHeaderClick(entry) {
    if (pickingReplanBase) {
      selectReplanBase(entry)
    } else {
      toggleExpanded(entry.id)
    }
  }

  function selectReplanBase(entry) {
    setPickingReplanBase(false)
    setReplanDraftBase(entry)
  }

  function cancelReplan() {
    setPickingReplanBase(false)
    setReplanDraftBase(null)
  }

  // Disable All/Dim/Focus when there's nothing meaningful to toggle:
  // no active plan, or plan exists but visited nothing (empty route / 0 visits).
  const focusButtonsDisabled = !activePlan || !(activePlan.visited_count > 0)

  const latestEntry = history.length > 0 ? history[history.length - 1] : null

  if (debrisFieldError) {
    return (
      <div className="app-shell">
        <div className="panel error-panel" style={{ margin: 24 }}>
          Failed to load debris field: {debrisFieldError}
        </div>
      </div>
    )
  }

  // Determine the prefilledStart to pass to CustomSelectionSummary.
  // null = fresh entry (rule 1: cleared params); non-null = Edit Selection (rule 7: pre-filled).
  const summaryPrefilledStart = customSelectionEditEntry?.startParams ?? null

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
        <aside className="mission-column">
          <section className="panel reticle">
            <h2 className="panel-title">Mission parameters</h2>
            <PlanForm onSubmit={handleGeneratePlan} onChange={handleFormChange} submitting={planning} />
          </section>

          {formError && (
            <div className="panel error-panel" role="alert">
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
        </aside>

        <div className="globe-pane reticle" style={{ position: 'relative' }}>
          <DebrisGlobe
            debrisField={debrisField}
            route={activePlan?.route}
            depot={activePlan?.depot}
            routeStyle={routeMode === 'ai' ? 'solid' : 'dashed'}
            cacheMetadata={cacheMetadata}
            focusMode={focusMode}
            activeDebrisId={activeDebrisId}
            pinnedIds={new Set(pinnedDebris.keys())}
            customSelecting={customSelecting}
            customSelectedIds={customSelectedIds}
            customFilterConfig={customFilterConfig}
            onDebrisSelect={handleDebrisSelect}
            onDebrisToggleSelect={handleDebrisToggleSelect}
            onBackgroundClick={() => {
              // Background click closes the modal.
              // Pinned tabs stay; only the open modal clears.
              if (activeDebrisId !== null) handleDebrisClose()
            }}
          />

          {/* Clear All — visible when 2+ objects pinned */}
          {pinnedDebris.size >= 2 && (
            <button
              className="btn debris-clear-all"
              onClick={handleDebrisClearAll}
            >
              Clear all ({pinnedDebris.size})
            </button>
          )}

          {/* Custom selection banner — shown while globe-picking */}
          {customSelecting && (
            <div className="custom-selection-banner" style={{ position: 'relative' }}>
              {customFilterOpen && (
                <FilterDropup
                  filter={customFilterConfig}
                  onChange={setCustomFilterConfig}
                  onClose={() => setCustomFilterOpen(false)}
                />
              )}
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

          {/* Debris info modal — shown whenever activeDebrisId is set.
              Debris object resolved from debrisField so it works for both
              pinned and transiently-clicked (unpinned) objects. */}
          {activeDebrisId !== null && (() => {
            const debris = debrisField.find(d => d.norad_id === activeDebrisId)
            if (!debris) return null
            const isPinned = pinnedDebris.has(activeDebrisId)
            return (
              <DebrisInfoModal
                debris={debris}
                pinned={isPinned}
                onPin={() => handleDebrisPin(debris)}
                onClose={handleDebrisClose}
              />
            )
          })()}

          {/* Pinned tab bar — left-center of globe, only pinned objects.
              Clicking a tab opens the modal for that debris.
              Active tab (currently shown in modal) gets a white-outline modifier. */}
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

        <aside className="working-column">
          <div className="working-sticky">
            {/* ── Visualization ─────────────────────────────────── */}
            <div className="working-sticky-label">Visualization</div>
            <div style={{ display: 'flex', gap: 6 }}>
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
                  style={{ flex: 1 }}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* ── Tools ─────────────────────────────────────────── */}
            <div className="working-sticky-label" style={{ marginTop: 8 }}>Tools</div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                className="btn"
                style={{ flex: 1 }}
                disabled={pickingReplanBase || history.length === 0}
                title={history.length === 0 ? 'Generate a plan first.' : undefined}
                onClick={() => {
                  setExpandedIds(new Set())
                  setPickingReplanBase(true)
                  setReplanDraftBase(null)
                }}
              >
                Replan
              </button>
              <button
                className="btn"
                style={{ flex: 1 }}
                disabled={customSelecting}
                onClick={() => {
                  // Fresh entry: clear params (rule 1)
                  setCustomSelecting(true)
                  setCustomSelectedIds(new Set())
                  setCustomSelectionDone(false)
                  setCustomSelectionEditEntry(null)
                }}
              >
                Custom selection
              </button>
            </div>

            {/* Picking hint */}
            {pickingReplanBase && (
              <div className="replan-pick-hint">
                Select a plan to replan from
                <button className="btn" style={{ marginLeft: 8 }} onClick={cancelReplan}>Cancel</button>
              </div>
            )}
          </div>

          {/* Draft replan card — shown when a base entry has been selected */}
          {replanDraftBase && (
            <div className="panel reticle replan-draft-card">
              <div className="replan-draft-label">
                Based on: #{history.findIndex(e => e.id === replanDraftBase.id) + 1}{' '}
                <span className="history-kind">
                  {replanDraftBase.kind === 'plan' ? 'Plan'
                    : replanDraftBase.kind === 'mission_cost' ? 'Custom Selection'
                    : 'Modification'}
                </span>
                {replanDraftBase.kind === 'mission_cost' && (
                  <span className="mc-replan-hint"> — start position only</span>
                )}
              </div>
              <ReplanInput
                baseEntry={replanDraftBase}
                onReplan={(text) => {
                  if (replanDraftBase.kind === 'mission_cost') {
                    handleMissionCostReplan(replanDraftBase, text)
                  } else {
                    handleReplan(replanDraftBase.params, text)
                  }
                }}
                onCancel={cancelReplan}
                submitting={replanning}
              />
            </div>
          )}

          {[...history].reverse().map((entry) => {
            const entryNumber = history.findIndex(e => e.id === entry.id) + 1
            const isExpanded = expandedIds.has(entry.id)
            const isLatest = entry.id === latestEntry?.id
            const isPickable = pickingReplanBase
            // One-line summary always shown below the header, even when collapsed.
            let summary = null
            if (entry.status === 'done' && entry.kind === 'plan') {
              const pct = Math.round(((entry.result.fuel_used_fraction) ?? 0) * 100)
              summary = `${entry.result.visited_count} of ${entry.result.pool_size_used} targets · ${entry.result.total_fuel_cost_km_s}/${entry.result.fuel_budget_km_s} km/s (${pct}%)`
            } else if (entry.status === 'done' && entry.kind === 'replan') {
              const raw = entry.result.explanation ?? ''
              summary = raw.length > 90 ? raw.slice(0, 89) + '…' : raw
            } else if (entry.status === 'done' && entry.kind === 'mission_cost') {
              summary = `${entry.result.visited_count} targets · ${entry.result.total_fuel_cost_km_s} km/s required`
              if (entry.result.nets_carried_required > 1) {
                summary += ` · ${entry.result.nets_carried_required} nets`
              }
            } else if (entry.status === 'error') {
              // Show the full backend error — never truncate, it contains the fix hint.
              summary = entry.error ?? 'Unknown error'
            }
            return (
              <div
                key={entry.id}
                className={`panel reticle history-entry${isPickable ? ' history-entry--pickable' : ''}`}
              >
                <div
                  className="history-entry-header"
                  onClick={() => handleEntryHeaderClick(entry)}
                  role="button"
                  aria-expanded={isExpanded}
                >
                  <span className="history-entry-label">
                    <span className="history-chevron">{isExpanded ? '▾' : '▸'}</span>
                    <span className="history-entry-number">#{entryNumber}</span>
                    <span className="history-kind">
                      {entry.kind === 'plan' ? 'Plan'
                        : entry.kind === 'mission_cost' ? 'Custom Selection'
                        : 'Modification'}
                    </span>
                  </span>
                  <span className={`history-status${entry.status === 'error' ? ' history-status--error' : ''}`}>
                    {entry.status === 'running' ? 'Running…' : entry.status === 'done' ? 'Done' : 'Failed'}
                  </span>
                </div>
                {summary && <p className="history-summary">{summary}</p>}
                {isExpanded && (
                  <>
                    {/* Parameter summary — mission_cost entries show start position */}
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
                    {entry.kind !== 'mission_cost' && (
                      <dl className="history-params">
                        {summariseParams(entry.params).map(([label, value]) => (
                          <Fragment key={label}>
                            <dt>{label}</dt>
                            <dd>{value}</dd>
                          </Fragment>
                        ))}
                      </dl>
                    )}

                    {/* Latest done entry: live AI/Naive toggle instead of static result
                        (only for plan/replan kinds — mission_cost doesn't have a naive route) */}
                    {isLatest && entry.status === 'done' && entry.kind !== 'mission_cost' ? (
                      <div className="history-live-result">
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                          <span className="working-sticky-label">
                            {routeMode === 'naive' ? 'Nearest-neighbour route' : 'AI-optimised route'}
                          </span>
                          <button className="btn btn-toggle" onClick={handleToggleNaive} style={{ fontSize: 11, padding: '4px 10px' }}>
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
                        />
                      </div>
                    ) : (
                      <>
                        {entry.status === 'done' && entry.kind === 'plan' && (
                          <ReasoningPanel plan={entry.result} />
                        )}
                        {entry.status === 'done' && entry.kind === 'replan' && (
                          <div className="replan-result">
                            {/* Diff-level view: what changed between old and new plan */}
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
                            {/* Full new-plan breakdown: visited count, fuel, risk, steps, warnings. */}
                            <ReasoningPanel plan={entry.result.new_plan} />
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
                            <dl className="mc-stats">
                              <dt>Targets</dt>
                              <dd>{entry.result.visited_count}</dd>
                              <dt>Fuel required</dt>
                              <dd>{entry.result.total_fuel_cost_km_s} km/s</dd>
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
                                      {['Leg', 'From', 'To', 'Δv (km/s)', 'Arrival (days)'].map((h) => (
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
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </details>
                            )}
                            {/* Edit Selection action */}
                            <button
                              className="btn"
                              style={{ width: '100%', marginTop: 10, fontSize: 11 }}
                              onClick={() => handleEditSelection(entry)}
                            >
                              Edit Selection
                            </button>
                          </div>
                        )}
                        {entry.status === 'error' && (
                          <p className="history-error">{entry.error}</p>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            )
          })}
        </aside>
      </div>
    </div>
  )
}
