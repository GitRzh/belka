import { Fragment, useEffect, useState } from 'react'
import DebrisGlobe from './components/DebrisGlobe.jsx'
import PlanForm from './components/PlanForm.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import MissionClock from './components/MissionClock.jsx'
import DebrisInfoModal from './components/DebrisInfoModal.jsx'
import CustomSelectionSummary from './components/CustomSelectionSummary.jsx'
import { api } from './api.js'

export default function App() {
  const [debrisField, setDebrisField] = useState([])
  const [debrisFieldError, setDebrisFieldError] = useState(null)
  const [cacheMetadata, setCacheMetadata] = useState(null) // { data_fetched_at, data_stale }

  const [selectedDebris, setSelectedDebris] = useState(null) // debris object clicked on the globe
  const [isDebrisModalPinned, setIsDebrisModalPinned] = useState(false)

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

  function handleDebrisSelect(debris) {
    setSelectedDebris(debris)
  }

  function handleDebrisModalClose() {
    setSelectedDebris(null)
    setIsDebrisModalPinned(false)
  }

  function handleDebrisModalTogglePin() {
    setIsDebrisModalPinned((prev) => !prev)
  }

  // Toggle membership of a debris object in the custom selection set
  function handleDebrisToggleSelect(debris) {
    setCustomSelectedIds(prev => {
      const next = new Set(prev)
      next.has(debris.norad_id) ? next.delete(debris.norad_id) : next.add(debris.norad_id)
      return next
    })
  }

  // Produces a compact [label, value] list for a plan/replan params object.
  // Skips defaults that add noise; always shows orbit origin and fuel budget.
  const DEFAULT_RISK_PENALTY_SCALE = 3000 // mirrors optimizer.py RISK_PENALTY_SCALE
  const DEFAULT_POOL_SIZE = 40            // mirrors cost_matrix.py DEFAULT_POOL_SIZE
  function summariseParams(params) {
    const pairs = []
    if (params.launch_site) {
      pairs.push(['site', params.launch_site])
      if (params.inclination_deg != null) pairs.push(['incl', `${params.inclination_deg}°`])
    } else {
      if (params.start_altitude_km != null)     pairs.push(['alt',  `${params.start_altitude_km} km`])
      if (params.start_inclination_deg != null)  pairs.push(['incl', `${params.start_inclination_deg}°`])
    }
    pairs.push(['budget', `${params.fuel_budget_km_s} km/s`])
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
            selectedDebrisId={selectedDebris?.norad_id ?? null}
            isModalPinned={isDebrisModalPinned}
            customSelecting={customSelecting}
            customSelectedIds={customSelectedIds}
            onDebrisSelect={handleDebrisSelect}
            onDebrisToggleSelect={handleDebrisToggleSelect}
            onBackgroundClick={() => {
              if (!isDebrisModalPinned) handleDebrisModalClose()
            }}
          />
          {/* Custom selection banner */}
          {customSelecting && (
            <div className="custom-selection-banner">
              <span className="custom-selection-count">{customSelectedIds.size} selected</span>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setCustomSelecting(false)
                  setCustomSelectionDone(true)
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
                }}
              >
                Cancel
              </button>
            </div>
          )}
          {customSelectionDone && !customSelecting && (
            <CustomSelectionSummary
              debrisField={debrisField}
              selectedIds={customSelectedIds}
              onClose={() => {
                setCustomSelectionDone(false)
                setCustomSelectedIds(new Set())
              }}
            />
          )}
          <DebrisInfoModal
            selectedDebris={selectedDebris}
            isModalPinned={isDebrisModalPinned}
            onClose={handleDebrisModalClose}
            onTogglePin={handleDebrisModalTogglePin}
          />
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
                  setCustomSelecting(true)
                  setCustomSelectedIds(new Set())
                  setCustomSelectionDone(false)
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
                <span className="history-kind">{replanDraftBase.kind === 'plan' ? 'Plan' : 'Modification'}</span>
              </div>
              <ReplanInput
                baseEntry={replanDraftBase}
                onReplan={(text) => handleReplan(replanDraftBase.params, text)}
                onCancel={cancelReplan}
                submitting={replanning}
              />
            </div>
          )}

          {[...history].reverse().map((entry) => {
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
                    <span className="history-kind">{entry.kind === 'plan' ? 'Plan' : 'Modification'}</span>
                  </span>
                  <span className={`history-status${entry.status === 'error' ? ' history-status--error' : ''}`}>
                    {entry.status === 'running' ? 'Running…' : entry.status === 'done' ? 'Done' : 'Failed'}
                  </span>
                </div>
                {summary && <p className="history-summary">{summary}</p>}
                {isExpanded && (
                  <>
                    <dl className="history-params">
                      {summariseParams(entry.params).map(([label, value]) => (
                        <Fragment key={label}>
                          <dt>{label}</dt>
                          <dd>{value}</dd>
                        </Fragment>
                      ))}
                    </dl>
                    {/* Latest done entry: live AI/Naive toggle instead of static result */}
                    {isLatest && entry.status === 'done' ? (
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
                            {/* Full new-plan breakdown: visited count, fuel, risk, steps, warnings.
                                new_plan has the same shape as a /plan result, so ReasoningPanel
                                renders it identically — including surfacing any warning when
                                visited_count == 0 (e.g. the constraint tightened too hard). */}
                            <ReasoningPanel plan={entry.result.new_plan} />
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
