import { useEffect, useState } from 'react'
import DebrisGlobe from './components/DebrisGlobe.jsx'
import PlanForm from './components/PlanForm.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import { api } from './api.js'

export default function App() {
  const [debrisField, setDebrisField] = useState([])
  const [debrisFieldError, setDebrisFieldError] = useState(null)
  const [cacheMetadata, setCacheMetadata] = useState(null) // { data_fetched_at, data_stale }

  const [lastPlanRequest, setLastPlanRequest] = useState(null) // needed for /replan body
  const [plan, setPlan] = useState(null) // current active plan shown on the globe
  const [naivePlan, setNaivePlan] = useState(null)
  const [routeMode, setRouteMode] = useState('ai') // 'ai' | 'naive'
  const [replanResult, setReplanResult] = useState(null) // diff + overrides from last /replan

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
    setReplanResult(null)
    setNaivePlan(null) // invalidate cached naive route when plan inputs change
    try {
      const result = await api.plan(payload)
      setPlan(result)
      setLastPlanRequest(payload)
      setRouteMode('ai')
    } catch (err) {
      // 404 (bad target_norad_id, or excluded-by-filter hint) and
      // 422 (bad removal_method_filter / monitor_only target) both land here —
      // err.body holds the detail message from the backend.
      setFormError(err.body?.detail || err.message)
    } finally {
      setPlanning(false)
    }
  }

  async function handleReplan(userRequestText) {
    if (!lastPlanRequest) return
    setReplanning(true)
    setFormError(null)
    try {
      const result = await api.replan({ ...lastPlanRequest, user_request_text: userRequestText })
      setPlan(result.new_plan)
      setRouteMode('ai')
      setReplanResult({ diff: result.diff, overrides_applied: result.overrides_applied, explanation: result.explanation })
    } catch (err) {
      setFormError(err.body?.detail || err.message)
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

  if (debrisFieldError) {
    return <p>Failed to load debris field: {debrisFieldError}</p>
  }

  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1 }}>
        <DebrisGlobe
          debrisField={debrisField}
          route={activePlan?.route}
          depot={activePlan?.depot}
          routeStyle={routeMode === 'ai' ? 'solid' : 'dashed'}
          cacheMetadata={cacheMetadata}
        />
      </div>

      <div style={{ width: 380, overflowY: 'auto', padding: 16 }}>
        <PlanForm onSubmit={handleGeneratePlan} submitting={planning} />

        {formError && <p role="alert">{formError}</p>}

        {plan && (
          <button onClick={handleToggleNaive}>
            {routeMode === 'ai' ? 'Show naive route' : 'Show AI route'}
          </button>
        )}

        <ReasoningPanel plan={activePlan} />

        <ReplanInput onReplan={handleReplan} submitting={replanning} disabled={!plan} replanResult={replanResult} />
      </div>
    </div>
  )
}
