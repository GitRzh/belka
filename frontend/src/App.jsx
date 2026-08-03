import { Fragment, useEffect, useState } from 'react'
import DebrisGlobe from './components/DebrisGlobe.jsx'
import PlanForm from './components/PlanForm.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import MissionClock from './components/MissionClock.jsx'
import { api } from './api.js'

export default function App() {
  const [debrisField, setDebrisField] = useState([])
  const [debrisFieldError, setDebrisFieldError] = useState(null)
  const [cacheMetadata, setCacheMetadata] = useState(null) // { data_fetched_at, data_stale }

  const [lastPlanRequest, setLastPlanRequest] = useState(null) // needed for /replan body
  const [plan, setPlan] = useState(null) // current active plan shown on the globe
  const [naivePlan, setNaivePlan] = useState(null)
  const [routeMode, setRouteMode] = useState('ai') // 'ai' | 'naive'
  const [focusMode, setFocusMode] = useState('dim') // 'all' | 'dim' | 'focus'
  const [history, setHistory] = useState([]) // chronological plan/replan attempts, newest last
  const [expandedIds, setExpandedIds] = useState(new Set()) // ids of currently expanded cards

  const [planning, setPlanning] = useState(false)
  const [replanning, setReplanning] = useState(false)
  const [formError, setFormError] = useState(null)

  useEffect(() => {
    api
      .getDebrisField()
      .then((res) => {
        setDebrisField(res.debris_field)
        setCacheMetadata({ data_fetched_at: res.data_fetched_at, data_stale: res.data_stale })
      })
      .catch((err) => setDebrisFieldError(err.message))
  }, [])

  async function handleGeneratePlan(payload) {
    setPlanning(true)
    setFormError(null)
    setNaivePlan(null) // invalidate cached naive route when plan inputs change
    const id = crypto.randomUUID()
    setHistory(h => [...h, { id, kind: 'plan', status: 'running', params: payload, result: null, error: null }])
    setExpandedIds(s => new Set([...s, id]))
    try {
      const result = await api.plan(payload)
      setPlan(result)
      setLastPlanRequest(payload)
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

  async function handleReplan(userRequestText) {
    if (!lastPlanRequest) return
    setReplanning(true)
    setFormError(null)
    const replanParams = { ...lastPlanRequest, user_request_text: userRequestText }
    const id = crypto.randomUUID()
    setHistory(h => [...h, { id, kind: 'replan', status: 'running', params: replanParams, result: null, error: null }])
    setExpandedIds(s => new Set([...s, id]))
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
        try {
          const result = await api.getNaiveRoute(lastPlanRequest)
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

  // Disable All/Dim/Focus when there's nothing meaningful to toggle:
  // no active plan, or plan exists but visited nothing (empty route / 0 visits).
  const focusButtonsDisabled = !activePlan || !(activePlan.visited_count > 0)

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
            <PlanForm onSubmit={handleGeneratePlan} submitting={planning} />
          </section>

          {formError && <div className="panel error-panel" role="alert">{formError}</div>}

          <section className="panel reticle">
            <ReplanInput onReplan={handleReplan} submitting={replanning} disabled={!plan} />
          </section>
        </aside>

        <div className="globe-pane reticle">
          <DebrisGlobe
            debrisField={debrisField}
            route={activePlan?.route}
            depot={activePlan?.depot}
            routeStyle={routeMode === 'ai' ? 'solid' : 'dashed'}
            cacheMetadata={cacheMetadata}
            focusMode={focusMode}
          />
        </div>

        <aside className="working-column">
          {plan && (
            <div className="working-sticky">
              <button className="btn btn-toggle" onClick={handleToggleNaive} style={{ width: '100%' }}>
                {routeMode === 'ai' ? 'Show naive route' : 'Show AI route'}
              </button>
              <div style={{ display: 'flex', gap: 6 }}>
                {['all', 'dim', 'focus'].map((mode) => (
                  <button
                    key={mode}
                    className={`btn btn-toggle${focusMode === mode ? ' btn-primary' : ''}`}
                    onClick={() => setFocusMode(mode)}
                    disabled={focusButtonsDisabled}
                    title={focusButtonsDisabled ? 'Generate a plan with visited stops first' : undefined}
                    style={{ flex: 1 }}
                  >
                    {mode === 'all' ? 'All dots' : mode === 'dim' ? 'Dim others' : 'Focus route'}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Active-plan stats panel: always reflects the currently displayed
              route (AI or naive). Separate from history cards so toggling the
              route mode visibly updates the numbers without re-expanding anything. */}
          {activePlan && (
            <div className="panel reticle">
              <h2 className="panel-title" style={{ marginBottom: 4 }}>
                {routeMode === 'naive' ? 'Naive route (active)' : 'AI route (active)'}
              </h2>
              <ReasoningPanel
                plan={activePlan}
                explanationOverride={
                  routeMode === 'naive'
                    ? 'Nearest-neighbor baseline (no AI optimization)'
                    : undefined
                }
              />
            </div>
          )}

          {[...history].reverse().map((entry) => {
            const isExpanded = expandedIds.has(entry.id)
            // One-line summary always shown below the header, even when collapsed.
            let summary = null
            if (entry.status === 'done' && entry.kind === 'plan') {
              summary = `${entry.result.visited_count}/${entry.result.pool_size_used} visited · ${entry.result.total_fuel_cost_km_s}/${entry.result.fuel_budget_km_s} km/s`
            } else if (entry.status === 'done' && entry.kind === 'replan') {
              const raw = entry.result.explanation ?? ''
              summary = raw.length > 80 ? raw.slice(0, 79) + '…' : raw
            } else if (entry.status === 'error') {
              const raw = entry.error ?? ''
              summary = raw.length > 80 ? raw.slice(0, 79) + '…' : raw
            }
            return (
              <div key={entry.id} className="panel reticle history-entry">
                <div
                  className="history-entry-header"
                  onClick={() => toggleExpanded(entry.id)}
                  role="button"
                  aria-expanded={isExpanded}
                >
                  <span className="history-entry-label">
                    <span className="history-chevron">{isExpanded ? '▾' : '▸'}</span>
                    <span className="history-kind">{entry.kind === 'plan' ? 'Plan' : 'Replan'}</span>
                  </span>
                  <span className="history-status">
                    {entry.status === 'running' ? 'Running…' : entry.status === 'done' ? 'Done' : 'Error'}
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
              </div>
            )
          })}
        </aside>
      </div>
    </div>
  )
}
