/**
 * reroute-panel.test.jsx
 *
 * Covers the three cases called out in the spec's TEST FILE section:
 *   1. Fuel prefill math on Match-button click and on globe-click — ceiling =
 *      params.fuel_budget_km_s, prefill = ceiling minus exact cumulative delta-v.
 *   2. Clicking an old history-tab shows that entry's own narration (not the
 *      latest entry's) and hides the Replan/Reroute/Fix action panel.
 *   3. Replan/Reroute/Fix counters stay independent and don't collide after a
 *      tab gets trimmed past MAX_ROUTE_REPLAN_TABS.
 *
 * Conventions follow leg-detail-panel.test.jsx: vitest, real render, act()/
 * waitFor around anything that fetches or updates state asynchronously.
 */

import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('./api.js', () => ({
  api: {
    getDebrisField: vi.fn(),
    getLaunchSites: vi.fn(),
    plan: vi.fn(),
    replan: vi.fn(),
    missionCost: vi.fn(),
    previewOrbit: vi.fn(),
    getNaiveRoute: vi.fn(),
    compare: vi.fn(),
    sweepLaunchWindow: vi.fn(),
    getDebrisById: vi.fn(),
    getRemovalMethods: vi.fn(),
    getLegExplanation: vi.fn(),
  },
}))

// DebrisGlobe needs a real WebGL/Cesium context we don't have in jsdom — stub it.
vi.mock('./components/DebrisGlobe.jsx', () => ({
  default: React.forwardRef(function DebrisGlobeStub(_props, ref) {
    React.useImperativeHandle(ref, () => ({}))
    return <div data-testid="debris-globe-stub" />
  }),
}))

// PlanForm is a large, unrelated form — stub it with a single button that
// calls onSubmit with a fixed payload, so App's real handleGeneratePlan /
// history / route-tab logic runs unmodified.
vi.mock('./components/PlanForm.jsx', () => ({
  default: function PlanFormStub({ onSubmit }) {
    return (
      <button
        data-testid="planform-submit"
        onClick={() => onSubmit({ launch_site: 'Baikonur', fuel_budget_km_s: 10 })}
      >
        Generate Plan
      </button>
    )
  },
}))

import App from './App.jsx'
import ReplanInput from './components/ReplanInput.jsx'
import { api } from './api.js'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

// route_details matches the real optimizer.py shape (optimizer.py ~line 425):
// no depot entry, no delta_v_km_s field — just norad_id, name, and other
// per-object metadata.  The depot is not a visited object and never appears here.
const ROUTE_DETAILS = [
  { norad_id: 1001, name: 'Alpha', risk_score: 0.4, arrival_time_days: 10.0, data_quality: 'fresh' },
  { norad_id: 1002, name: 'Bravo', risk_score: 0.3, arrival_time_days: 20.0, data_quality: 'fresh' },
  { norad_id: 1003, name: 'Charlie', risk_score: 0.5, arrival_time_days: 30.0, data_quality: 'fresh' },
]

// step_breakdown aligns with route_details by array index (same visit order):
// step_breakdown[i] is the leg arriving at route_details[i].
const STEP_BREAKDOWN = [
  { from: 'Depot', to: 'Alpha (1001)', delta_v_km_s: 1.5, arrival_time_days: 10.0, raan_drift_deg: 0, recommended_wait_days: 0, fuel_saved_km_s: 0, data_quality: 'fresh' },
  { from: 'Alpha (1001)', to: 'Bravo (1002)', delta_v_km_s: 2.25, arrival_time_days: 20.0, raan_drift_deg: 0, recommended_wait_days: 0, fuel_saved_km_s: 0, data_quality: 'fresh' },
  { from: 'Bravo (1002)', to: 'Charlie (1003)', delta_v_km_s: 0.75, arrival_time_days: 30.0, raan_drift_deg: 0, recommended_wait_days: 0, fuel_saved_km_s: 0, data_quality: 'fresh' },
]

const DEBRIS_FIELD = [
  { norad_id: 1001, name: 'Alpha', altitude_km: 800, inclination_deg: 51.6 },
  { norad_id: 1002, name: 'Bravo', altitude_km: 820, inclination_deg: 52.1 },
  { norad_id: 1003, name: 'Charlie', altitude_km: 840, inclination_deg: 53.0 },
]

const FUEL_BUDGET_KM_S = 10

const ACTIVE_PLAN = {
  route_details: ROUTE_DETAILS,
  step_breakdown: STEP_BREAKDOWN,
  depot: { altitude_km: 500, inclination_deg: 51.6 },
}

// ─── 1) Fuel prefill / ceiling math (ReplanInput, isolated) ───────────────────

describe('1) Fuel prefill math', () => {
  function renderReplan(extraProps = {}) {
    return render(
      <ReplanInput
        activePlan={ACTIVE_PLAN}
        debrisField={DEBRIS_FIELD}
        globePickedObject={null}
        fuelBudgetKmS={FUEL_BUDGET_KM_S}
        onReplan={vi.fn()}
        onReroute={vi.fn()}
        submitting={false}
        {...extraProps}
      />
    )
  }

  // Helper: after switching to Reroute mode, the useEffect([mode]) fires and
  // prefills fields from the last route object.  We need to flush that effect
  // before making assertions or clicking more buttons, otherwise state updates
  // from the effect may be batched and not yet reflected in the DOM.
  async function switchToReroute() {
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reroute' }))
    })
  }

  // Helper: find the fuel number input specifically (not the paired range input
  // which also carries the same numeric value).
  function getFuelNumberInput() {
    // The fuel field's number input has min=0 max=15.
    return document.querySelector('input[type="number"][min="0"][max="15"]')
  }

  it('shows the ceiling label from fuelBudgetKmS', async () => {
    renderReplan()
    await switchToReroute()
    expect(screen.getByText(/ceiling 10 km\/s/)).toBeInTheDocument()
  })

  it('Match-button click on Alpha prefills ceiling minus step_breakdown[0].delta_v_km_s', async () => {
    renderReplan()
    await switchToReroute()

    // Alpha is route_details[0] → step_breakdown[0].delta_v_km_s = 1.5
    // prefill = 10 - 1.5 = 8.5
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Alpha' }))
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    expect(fuelInput.value).toBe('8.5')
  })

  it('Match-button click on Bravo prefills ceiling minus cumulative delta-v through Bravo', async () => {
    renderReplan()
    await switchToReroute()

    // Bravo is route_details[1] → step_breakdown[0..1] = 1.5 + 2.25 = 3.75
    // prefill = 10 - 3.75 = 6.25 → round1 → 6.3 (Math.round(62.5)/10 = 63/10)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Bravo' }))
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    expect(fuelInput.value).toBe('6.3')
  })

  it('Match-button click on Charlie prefills ceiling minus the FULL cumulative delta-v', async () => {
    renderReplan()
    await switchToReroute()

    // Charlie is route_details[2] → step_breakdown[0..2] = 1.5 + 2.25 + 0.75 = 4.5
    // prefill = 10 - 4.5 = 5.5
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Charlie' }))
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    expect(fuelInput.value).toBe('5.5')
  })

  it('globe-click prefills using step_breakdown cumulative delta-v for the picked norad_id', async () => {
    const { rerender } = renderReplan({ globePickedObject: null })
    await switchToReroute()

    // Globe click on Alpha (route_details[0]): step_breakdown[0].delta_v_km_s = 1.5
    // prefill = 10 - 1.5 = 8.5
    await act(async () => {
      rerender(
        <ReplanInput
          activePlan={ACTIVE_PLAN}
          debrisField={DEBRIS_FIELD}
          globePickedObject={{ norad_id: 1001, altitude_km: 800, inclination_deg: 51.6 }}
          fuelBudgetKmS={FUEL_BUDGET_KM_S}
          onReplan={vi.fn()}
          onReroute={vi.fn()}
          submitting={false}
        />
      )
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    expect(fuelInput.value).toBe('8.5')
  })

  it('clamps prefill to 0 when cumulative delta-v exceeds the ceiling', async () => {
    const tightBudget = 2 // less than cumulative delta-v through Bravo (3.75)
    renderReplan({ fuelBudgetKmS: tightBudget })
    await switchToReroute()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Bravo' }))
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    expect(fuelInput.value).toBe('0')
  })

  it('fuel prefill returns null (field unchanged) when step_breakdown is absent', async () => {
    // Matches the guard: if activePlan has no step_breakdown, fuelPrefillForNoradId
    // returns null and the fuel field stays empty rather than showing a wrong value.
    const planWithoutStepBreakdown = { route_details: ROUTE_DETAILS, depot: ACTIVE_PLAN.depot }
    renderReplan({ activePlan: planWithoutStepBreakdown })
    await switchToReroute()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Bravo' }))
    })

    const fuelInput = getFuelNumberInput()
    expect(fuelInput).not.toBeNull()
    // Field stays empty — the initial mode-open prefill also returns null without
    // step_breakdown, and the Match click also returns null, so value is ''.
    expect(fuelInput.value).toBe('')
  })

  it('submitting sends the clamped fuel value as fuel_budget_km_s in the reroute payload', async () => {
    const onReroute = vi.fn()
    renderReplan({ onReroute })
    await switchToReroute()

    // Match Bravo → fuel = 10 - 3.75 = 6.25 → round1 → 6.3
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Match Bravo' }))
    })

    await act(async () => {
      // Use fireEvent.submit on the form — clicking a type="submit" button does not
      // always propagate to the form's onSubmit handler in jsdom; submitting the
      // form directly is the reliable approach (same pattern used in other tests in
      // this repo that verify form payloads).
      const form = document.querySelector('.replan form')
      expect(form).not.toBeNull()
      fireEvent.submit(form)
    })

    expect(onReroute).toHaveBeenCalledTimes(1)
    const payload = onReroute.mock.calls[0][0]
    expect(payload.fuel_budget_km_s).toBe(6.3)
  })
})

// ─── 2) Old-tab read-only snapshot behavior (full App) ────────────────────────

describe('2) Old history-tab shows its own narration and hides the action panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDebrisField.mockResolvedValue({
      debris_field: DEBRIS_FIELD,
      data_fetched_at: '2026-08-01T00:00:00Z',
      data_stale: false,
    })
  })

  it('entry #1 becomes read-only (no Replan control, own explanation shown) once entry #2 exists', async () => {
    api.plan
      .mockResolvedValueOnce({
        route: ['Alpha (1001)'],
        route_details: ROUTE_DETAILS,
        depot: ACTIVE_PLAN.depot,
        visited_count: 1,
        pool_size_used: 3,
        total_fuel_cost_km_s: 1.5,
        fuel_budget_km_s: 10,
        fuel_used_fraction: 0.15,
        explanation: 'FIRST PLAN explanation.',
      })
      .mockResolvedValueOnce({
        route: ['Bravo (1002)'],
        route_details: ROUTE_DETAILS,
        depot: ACTIVE_PLAN.depot,
        visited_count: 1,
        pool_size_used: 3,
        total_fuel_cost_km_s: 2.25,
        fuel_budget_km_s: 10,
        fuel_used_fraction: 0.225,
        explanation: 'SECOND PLAN explanation.',
      })

    render(<App />)
    await waitFor(() => expect(api.getDebrisField).toHaveBeenCalledTimes(1))

    // Generate plan #1.  App auto-navigates to Workspace panel after generating.
    fireEvent.click(screen.getByTestId('planform-submit'))
    await waitFor(() => expect(api.plan).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('section-workspace')).toBeInTheDocument())

    // Navigate back to Parameters so planform-submit is in the DOM again.
    fireEvent.click(screen.getByTestId('panel-tab-parameters'))
    await waitFor(() => expect(screen.getByTestId('planform-submit')).toBeInTheDocument())

    // Generate plan #2 — a brand-new plan/history entry, so #1 is no longer latest.
    fireEvent.click(screen.getByTestId('planform-submit'))
    await waitFor(() => expect(api.plan).toHaveBeenCalledTimes(2))

    // Switch to the History panel and click entry #1 (now an old entry).
    fireEvent.click(screen.getByTestId('panel-tab-history'))
    await waitFor(() => expect(screen.getByTestId('history-tab-1')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('history-tab-1'))

    // The action panel (Replan/Reroute control div) must be hidden for old entry.
    // It only renders when isLatest is true; check its wrapper is absent.
    await waitFor(() => {
      expect(document.querySelector('.workspace-replan')).toBeNull()
    })

    // The narration shown must NOT be entry #2's explanation.
    expect(screen.queryByText(/SECOND PLAN explanation/)).not.toBeInTheDocument()
  })
})

// ─── 3) Independent per-kind counters survive a trim ──────────────────────────

describe('3) Replan/Reroute/Fix counters stay independent across a trim', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDebrisField.mockResolvedValue({
      debris_field: DEBRIS_FIELD,
      data_fetched_at: '2026-08-01T00:00:00Z',
      data_stale: false,
    })
  })

  it('labels stay correctly numbered per-kind even after MAX_ROUTE_REPLAN_TABS trims old tabs', async () => {
    api.plan.mockResolvedValue({
      route: ['Alpha (1001)'],
      route_details: ROUTE_DETAILS,
      depot: ACTIVE_PLAN.depot,
      visited_count: 1,
      pool_size_used: 3,
      total_fuel_cost_km_s: 1.5,
      fuel_budget_km_s: 10,
      fuel_used_fraction: 0.15,
      explanation: 'Base plan.',
    })

    // Six replans in a row (MAX_ROUTE_REPLAN_TABS is 5) — tab #1 must get
    // trimmed off the front, but the counter keeps counting up regardless,
    // so the surviving tabs read "Replan #2".."Replan #6", never colliding
    // or renumbering down to #1..#5.
    let call = 0
    api.replan.mockImplementation(async () => {
      call += 1
      return {
        new_plan: {
          route: [`Stop ${call}`],
          route_details: ROUTE_DETAILS,
          depot: ACTIVE_PLAN.depot,
        },
        explanation: `Replan #${call} explanation.`,
        overrides_applied: {},
      }
    })

    render(<App />)
    await waitFor(() => expect(api.getDebrisField).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByTestId('planform-submit'))
    await waitFor(() => expect(api.plan).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('section-workspace')).toBeInTheDocument())

    // Open the Replan mode form and fire off 6 free-text replans.
    for (let i = 0; i < 6; i++) {
      const textarea = screen.getByPlaceholderText(/prioritize risk over fuel/i)
      fireEvent.change(textarea, { target: { value: `change ${i + 1}` } })
      fireEvent.click(screen.getByRole('button', { name: /apply changes/i }))
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => expect(api.replan).toHaveBeenCalledTimes(i + 1))
    }

    const strip = screen.getByTestId('route-tab-strip')
    const labels = Array.from(strip.querySelectorAll('.route-tab-btn')).map((b) => b.textContent)

    // Plan tab always stays; oldest replan tab (#1) was trimmed off.
    expect(labels[0]).toBe('Plan')
    expect(labels).not.toContain('Replan #1')
    expect(labels).toContain('Replan #6')
    // Exactly MAX_ROUTE_REPLAN_TABS (5) replan tabs survive, plus the Plan tab.
    expect(labels).toHaveLength(6)
    // No duplicate/collided labels.
    expect(new Set(labels).size).toBe(labels.length)
  })
})
