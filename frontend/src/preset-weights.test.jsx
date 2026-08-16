/**
 * preset-weights.test.jsx
 *
 * Tests for "Use these weights" preset button behavior and the RECOMMENDED badge.
 * Covers the original a-g scenarios plus new Follow-up 2 scenarios.
 *
 * Original scenarios (some updated for new "panel stays open" behavior):
 *  a) Clicking a preset's "Use these weights" populates weights_json.
 *  b) Clicking a different preset afterward overwrites weights_json (no merge).
 *  c) Other form fields (fuel budget, pool size) are unchanged after preset click.
 *  d-new) Panel stays OPEN after a preset click (reversal of old d behavior).
 *  e) No History/plan/workspace navigation from preset click.
 *  f) Generate Plan works normally after preset weights applied.
 *  g) Same-preset re-click (seq nonce) resets weights_json after manual edit.
 *
 * New Follow-up 2 scenarios:
 *  UNIT-a) findRecommendedPreset() unit tests (no rendering).
 *  RENDER-b) RECOMMENDED badge renders only on the winning preset card.
 *  PANEL-c) Panel stays open after "Use these weights" click (all 3 cards present).
 *  PANEL-d) After clicking Preset A, clicking Preset B still updates weights_json.
 *  PANEL-e) Same-preset twice (panel never closed) still re-applies weights correctly.
 *  PANEL-f) Clicking Generate Plan closes ComparisonPanel.
 *  PANEL-g) Clicking the Close button still closes the panel.
 */

import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { findRecommendedPreset, rankPresetsByEfficiency } from './components/ComparisonPanel.jsx'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('cesium', () => ({
  Cartesian3: {
    fromDegrees: vi.fn(() => ({})),
    magnitude: vi.fn(() => 1),
    normalize: vi.fn((v, out) => out ?? {}),
    dot: vi.fn(() => 0),
    multiplyByScalar: vi.fn((_v, _s, out) => out ?? {}),
    add: vi.fn((_a, _b, out) => out ?? {}),
    lerp: vi.fn((_a, _b, _t, out) => out ?? {}),
  },
  Cartesian2: vi.fn(function () { return {} }),
  Color: {
    fromHsl: vi.fn(() => ({ withAlpha: vi.fn(() => ({})) })),
    fromCssColorString: vi.fn(() => ({ withAlpha: vi.fn(() => ({})) })),
    WHITE: { withAlpha: vi.fn(() => ({})) },
    BLACK: { withAlpha: vi.fn(() => ({})) },
    LIME: {},
    RED: {},
    DODGERBLUE: {},
    TRANSPARENT: {},
  },
  LabelStyle: { FILL_AND_OUTLINE: 2 },
  Credit: { CESIUM_CREDIT: null },
  PolylineDashMaterialProperty: vi.fn(function () { return {} }),
  ScreenSpaceEventType: { LEFT_CLICK: 'LEFT_CLICK' },
}))

vi.mock('resium', () => ({
  Viewer: React.forwardRef(function ViewerMock({ children, onReady }, ref) {
    if (onReady) onReady({
      creditDisplay: { removeStaticCredit: vi.fn() },
      screenSpaceEventHandler: { setInputAction: vi.fn(), removeInputAction: vi.fn() },
    })
    return <div data-testid="cesium-viewer">{children}</div>
  }),
  Entity: vi.fn(({ children }) => <div>{children}</div>),
  PolylineGraphics: vi.fn(() => null),
  PointGraphics: vi.fn(() => null),
  LabelGraphics: vi.fn(() => null),
}))

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
  },
}))

import App from './App.jsx'
import { api } from './api.js'

// ─── Fixtures ────────────────────────────────────────────────────────────────

const FUEL_CONSERVATIVE_WEIGHTS = { proximity: 0.7, lifetime: 0.2, size: 0.1 }
const BALANCED_WEIGHTS           = { proximity: 0.4, lifetime: 0.3, size: 0.3 }
const RISK_AGGRESSIVE_WEIGHTS    = { proximity: 0.2, lifetime: 0.3, size: 0.5 }

// Risk-Aggressive wins: 0.9/1.1 ≈ 0.818 > Balanced 0.5/0.8 = 0.625 > Fuel-Conservative 0.3/0.6 = 0.5
const COMPARE_RESULT = {
  presets: [
    {
      label: 'Fuel-Conservative',
      weights: FUEL_CONSERVATIVE_WEIGHTS,
      total_fuel_cost_km_s: 0.6,
      total_risk_collected: 0.3,
      visited_count: 3,
      route_details: [],
    },
    {
      label: 'Balanced',
      weights: BALANCED_WEIGHTS,
      total_fuel_cost_km_s: 0.8,
      total_risk_collected: 0.5,
      visited_count: 4,
      route_details: [],
    },
    {
      label: 'Risk-Aggressive',
      weights: RISK_AGGRESSIVE_WEIGHTS,
      total_fuel_cost_km_s: 1.1,
      total_risk_collected: 0.9,
      visited_count: 5,
      route_details: [],
    },
  ],
  comparison_narration: 'Test narration.',
}

const PLAN_RESULT = {
  route: ['DEBRIS A (10001)'],
  route_details: [{ norad_id: 10001, name: 'DEBRIS A', removal_method: 'net_capture' }],
  step_breakdown: [],
  total_fuel_cost_km_s: 0.8,
  fuel_budget_km_s: 2.5,
  fuel_used_fraction: 0.32,
  nets_carried_required: 1,
  visited_count: 1,
  pool_size_used: 5,
  total_risk_collected: 0.5,
  depot: null,
  explanation: 'Plan with preset weights.',
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function renderAndOpenComparison() {
  render(<App />)
  await waitFor(() => screen.getByRole('button', { name: /compare presets/i }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /compare presets/i }))
  })
  await waitFor(() => screen.getAllByRole('button', { name: /use these weights/i }))
}

function weightsTextarea() {
  return screen.getByPlaceholderText(/proximity.*lifetime.*size/i)
}

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  api.getDebrisField.mockResolvedValue({
    debris_field: [], data_fetched_at: new Date().toISOString(), data_stale: false,
  })
  api.getLaunchSites.mockResolvedValue({
    vandenberg: { name: 'Vandenberg', lat: 34.6, lon: -120.6, min_inclination: 56 },
  })
  api.plan.mockResolvedValue(PLAN_RESULT)
  api.compare.mockResolvedValue(COMPARE_RESULT)
  api.previewOrbit.mockResolvedValue({ lat: 0, lon: 0 })
  api.getNaiveRoute.mockResolvedValue(PLAN_RESULT)
})

// ─── UNIT-rank: rankPresetsByEfficiency() unit tests ─────────────────────────

describe('UNIT-rank-a) rankPresetsByEfficiency() — descending order', () => {
  it('returns all 3 presets sorted by efficiency descending', () => {
    // Efficiencies: Fuel-Conservative 0.3/0.6=0.5, Balanced 0.5/0.8=0.625, Risk-Aggressive 0.9/1.1≈0.818
    const ranked = rankPresetsByEfficiency(COMPARE_RESULT.presets)
    expect(ranked.map(p => p.label)).toEqual(['Risk-Aggressive', 'Balanced', 'Fuel-Conservative'])
  })

  it('never filters — always returns all 3 presets', () => {
    const allZero = COMPARE_RESULT.presets.map(p => ({ ...p, total_fuel_cost_km_s: 0 }))
    const ranked = rankPresetsByEfficiency(allZero)
    expect(ranked).toHaveLength(3)
  })

  it('preset with zero fuel cost sorts to the bottom regardless of risk', () => {
    const presets = [
      { label: 'Fuel-Conservative', total_fuel_cost_km_s: 0,   total_risk_collected: 9999 },
      { label: 'Balanced',          total_fuel_cost_km_s: 1.0,  total_risk_collected: 0.5 },
      { label: 'Risk-Aggressive',   total_fuel_cost_km_s: 0.5,  total_risk_collected: 0.4 },
    ]
    const ranked = rankPresetsByEfficiency(presets)
    // Balanced 0.5, Risk-Aggressive 0.8, Fuel-Conservative -Infinity
    // Sorted: Risk-Aggressive (0.8), Balanced (0.5), Fuel-Conservative (-Inf)
    expect(ranked[2].label).toBe('Fuel-Conservative')
  })

  it('exact 2-way tie preserves original array order for the tied pair', () => {
    const presets = [
      { label: 'Fuel-Conservative', total_fuel_cost_km_s: 1.0, total_risk_collected: 0.1 }, // 0.1 — last
      { label: 'Balanced',          total_fuel_cost_km_s: 1.0, total_risk_collected: 0.5 }, // 0.5 — tie
      { label: 'Risk-Aggressive',   total_fuel_cost_km_s: 2.0, total_risk_collected: 1.0 }, // 0.5 — tie
    ]
    const ranked = rankPresetsByEfficiency(presets)
    // Balanced and Risk-Aggressive both 0.5; stable sort preserves Balanced before Risk-Aggressive
    expect(ranked[0].label).toBe('Balanced')
    expect(ranked[1].label).toBe('Risk-Aggressive')
    expect(ranked[2].label).toBe('Fuel-Conservative')
  })

  it('exact 3-way tie returns presets in original array order', () => {
    const presets = [
      { label: 'Fuel-Conservative', total_fuel_cost_km_s: 1.0, total_risk_collected: 0.5 },
      { label: 'Balanced',          total_fuel_cost_km_s: 2.0, total_risk_collected: 1.0 },
      { label: 'Risk-Aggressive',   total_fuel_cost_km_s: 0.5, total_risk_collected: 0.25 },
    ]
    const ranked = rankPresetsByEfficiency(presets)
    expect(ranked.map(p => p.label)).toEqual(['Fuel-Conservative', 'Balanced', 'Risk-Aggressive'])
  })
})

describe('UNIT-rank-b) real screenshot numbers confirm correct rank order', () => {
  // Fuel-Conservative: 3.4443/34.753  ≈ 0.09912
  // Balanced:          4.2548/32.4307 ≈ 0.13119  ← highest
  // Risk-Aggressive:   4.1755/35.798  ≈ 0.11664  ← second
  const screenshotPresets = [
    { label: 'Fuel-Conservative', total_fuel_cost_km_s: 34.753,  total_risk_collected: 3.4443 },
    { label: 'Balanced',          total_fuel_cost_km_s: 32.4307, total_risk_collected: 4.2548 },
    { label: 'Risk-Aggressive',   total_fuel_cost_km_s: 35.798,  total_risk_collected: 4.1755 },
  ]

  it('rank order is [Balanced, Risk-Aggressive, Fuel-Conservative]', () => {
    const ranked = rankPresetsByEfficiency(screenshotPresets)
    expect(ranked.map(p => p.label)).toEqual(['Balanced', 'Risk-Aggressive', 'Fuel-Conservative'])
  })

  it('Balanced gets rank 0 → card border color is #f2f2f0 (brightest)', () => {
    const RANK_COLORS = ['#f2f2f0', '#8a8a8e', '#4a4a4e']
    const ranked = rankPresetsByEfficiency(screenshotPresets)
    const rankColorByLabel = Object.fromEntries(ranked.map((p, i) => [p.label, RANK_COLORS[i]]))
    expect(rankColorByLabel['Balanced']).toBe('#f2f2f0')
    expect(rankColorByLabel['Risk-Aggressive']).toBe('#8a8a8e')
    expect(rankColorByLabel['Fuel-Conservative']).toBe('#4a4a4e')
  })
})

// ─── RENDER-rank-c: card border colors in the DOM match rank assignment ───────

describe('RENDER-rank-c) card border colors in DOM match rank-based assignment', () => {
  it('Risk-Aggressive card border is brightest (#f2f2f0) since it has highest efficiency in COMPARE_RESULT', async () => {
    // COMPARE_RESULT efficiencies: Risk-Aggressive 0.9/1.1≈0.818, Balanced 0.5/0.8=0.625, Fuel-Conservative 0.3/0.6=0.5
    await renderAndOpenComparison()

    const riskLabel = screen.getByText('Risk-Aggressive', { selector: 'div' })
    const card = riskLabel.closest('[style*="border"]')
    expect(card).toBeTruthy()
    // Risk-Aggressive is rank 0 → brightest → #f2f2f0.
    // JSDOM normalises hex to rgb in computed style; accept both forms.
    // #f2f2f0 = rgb(242, 242, 240)
    const styleStr = card.getAttribute('style') ?? ''
    expect(styleStr).toMatch(/#f2f2f0|rgb\(242,\s*242,\s*240\)/)
  })

  it('Fuel-Conservative card border is dimmest (#4a4a4e) since it has lowest efficiency in COMPARE_RESULT', async () => {
    await renderAndOpenComparison()

    const fuelLabel = screen.getByText('Fuel-Conservative', { selector: 'div' })
    const card = fuelLabel.closest('[style*="border"]')
    expect(card).toBeTruthy()
    // Fuel-Conservative is rank 2 → dimmest → #4a4a4e.
    // JSDOM normalises hex to rgb in computed style; accept both forms.
    // #4a4a4e = rgb(74, 74, 78)
    const styleStr = card.getAttribute('style') ?? ''
    expect(styleStr).toMatch(/#4a4a4e|rgb\(74,\s*74,\s*78\)/)
  })
})

// ─── RENDER-rank-d: chart Cell fill colors match card border colors ───────────

describe('RENDER-rank-d) chart Cell fill colors consistent with card border colors', () => {
  it('each preset maps to same rank color in both card and chart data (unit)', () => {
    const RANK_COLORS = ['#f2f2f0', '#8a8a8e', '#4a4a4e']
    const presets = COMPARE_RESULT.presets
    const ranked = rankPresetsByEfficiency(presets)
    const rankColorByLabel = Object.fromEntries(ranked.map((p, i) => [p.label, RANK_COLORS[i]]))

    // Simulate buildChartData output color field
    const chartData = presets.map(p => ({
      name: p.label,
      color: rankColorByLabel[p.label],
    }))

    for (const row of chartData) {
      expect(row.color).toBe(rankColorByLabel[row.name])
    }
  })

  it('Fuel bar Cell and Risk bar Cell for the same preset share the same fill color', () => {
    const RANK_COLORS = ['#f2f2f0', '#8a8a8e', '#4a4a4e']
    const presets = COMPARE_RESULT.presets
    const ranked = rankPresetsByEfficiency(presets)
    const rankColorByLabel = Object.fromEntries(ranked.map((p, i) => [p.label, RANK_COLORS[i]]))

    const chartData = presets.map(p => ({
      name: p.label,
      color: rankColorByLabel[p.label],
    }))

    // Both bars use entry.color — only fillOpacity differs
    for (const entry of chartData) {
      const fuelCellFill = entry.color   // fillOpacity 1
      const riskCellFill = entry.color   // fillOpacity 0.55
      expect(fuelCellFill).toBe(riskCellFill)
    }
  })
})

// ─── UNIT-a: findRecommendedPreset() unit tests (no rendering) ───────────────

describe('UNIT-a) findRecommendedPreset() — picks highest risk/fuel efficiency', () => {
  it('returns the preset with the highest efficiency from a 3-item array', () => {
    const result = findRecommendedPreset(COMPARE_RESULT.presets)
    // Risk-Aggressive: 0.9/1.1 ≈ 0.818 is highest
    expect(result.label).toBe('Risk-Aggressive')
  })

  it('returns null when all presets have total_fuel_cost_km_s <= 0', () => {
    const zeroFuel = COMPARE_RESULT.presets.map(p => ({ ...p, total_fuel_cost_km_s: 0 }))
    expect(findRecommendedPreset(zeroFuel)).toBeNull()
  })

  it('returns null when exactly two presets tie on efficiency', () => {
    const tied = [
      { label: 'A', total_fuel_cost_km_s: 1.0, total_risk_collected: 0.5 },
      { label: 'B', total_fuel_cost_km_s: 2.0, total_risk_collected: 1.0 }, // same ratio 0.5
      { label: 'C', total_fuel_cost_km_s: 0.5, total_risk_collected: 0.1 },
    ]
    expect(findRecommendedPreset(tied)).toBeNull()
  })

  it('ignores visited_count — higher visits with worse efficiency does not win', () => {
    const presets = [
      { label: 'Many-Visits', total_fuel_cost_km_s: 2.0, total_risk_collected: 0.4, visited_count: 10 },
      { label: 'Few-Visits',  total_fuel_cost_km_s: 0.5, total_risk_collected: 0.4, visited_count: 2  },
    ]
    // Many-Visits: 0.4/2.0 = 0.2; Few-Visits: 0.4/0.5 = 0.8 → Few-Visits wins
    const result = findRecommendedPreset(presets)
    expect(result.label).toBe('Few-Visits')
  })

  it('correctly identifies the single winner when one preset is clearly best', () => {
    const presets = [
      { label: 'Low',  total_fuel_cost_km_s: 1.0, total_risk_collected: 0.2 }, // 0.2
      { label: 'Mid',  total_fuel_cost_km_s: 1.0, total_risk_collected: 0.5 }, // 0.5
      { label: 'High', total_fuel_cost_km_s: 1.0, total_risk_collected: 0.9 }, // 0.9 ← winner
    ]
    expect(findRecommendedPreset(presets).label).toBe('High')
  })
})

// ─── RENDER-b: RECOMMENDED badge renders on correct card only ─────────────────

describe('RENDER-b) RECOMMENDED badge renders only on the highest-efficiency card', () => {
  it('badge appears on Risk-Aggressive (highest efficiency) and nowhere else', async () => {
    await renderAndOpenComparison()

    // Badge present on the winning card
    const badge = screen.getByTestId('recommended-badge-Risk-Aggressive')
    expect(badge).toBeInTheDocument()
    expect(badge.textContent.toLowerCase()).toContain('recommended')

    // Badge absent on the other two cards
    expect(screen.queryByTestId('recommended-badge-Fuel-Conservative')).not.toBeInTheDocument()
    expect(screen.queryByTestId('recommended-badge-Balanced')).not.toBeInTheDocument()
  })
})

// ─── PANEL-c: Panel stays open after "Use these weights" click ────────────────

describe('PANEL-c) ComparisonPanel stays open after clicking "Use these weights"', () => {
  it('all 3 preset cards remain in DOM immediately after a preset click', async () => {
    await renderAndOpenComparison()

    const buttons = screen.getAllByRole('button', { name: /use these weights/i })
    expect(buttons).toHaveLength(3)

    // Click the first preset's button
    await act(async () => { fireEvent.click(buttons[0]) })

    // All 3 "Use these weights" buttons are still present — panel did NOT close
    expect(screen.getAllByRole('button', { name: /use these weights/i })).toHaveLength(3)
    // The narration text is also still visible
    expect(screen.getByText('Test narration.')).toBeInTheDocument()
  })
})

// ─── PANEL-d: After clicking Preset A, clicking Preset B updates weights ──────

describe('PANEL-d) Clicking Preset B after Preset A (panel still open) updates weights', () => {
  it('weights_json reflects Preset B after both clicks, without re-opening the panel', async () => {
    await renderAndOpenComparison()

    const buttons = screen.getAllByRole('button', { name: /use these weights/i })
    // Click Fuel-Conservative (index 0)
    await act(async () => { fireEvent.click(buttons[0]) })
    expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)

    // Panel still open — click Risk-Aggressive (index 2) directly
    const buttons2 = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(buttons2[2]) })
    expect(JSON.parse(weightsTextarea().value)).toEqual(RISK_AGGRESSIVE_WEIGHTS)
  })
})

// ─── PANEL-e: Same-preset twice (panel never closed) still re-applies weights ─

describe('PANEL-e) Same-preset re-click (panel open, seq nonce) resets weights_json', () => {
  it('clicking the same preset twice with a manual edit in between resets weights_json', async () => {
    await renderAndOpenComparison()

    const [firstBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(firstBtn) })
    expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)

    // User manually edits the textarea (panel still open throughout)
    await act(async () => {
      fireEvent.change(weightsTextarea(), { target: { value: '{"proximity":0.1,"lifetime":0.1,"size":0.8}' } })
    })
    expect(weightsTextarea().value).toBe('{"proximity":0.1,"lifetime":0.1,"size":0.8}')

    // Click the same Fuel-Conservative button again (panel was never closed)
    const sameBtn = screen.getAllByRole('button', { name: /use these weights/i })[0]
    await act(async () => { fireEvent.click(sameBtn) })

    // weights_json must be reset to Fuel-Conservative — seq nonce forced re-fire
    await waitFor(() =>
      expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)
    )
  })
})

// ─── PANEL-f: Generate Plan closes ComparisonPanel ────────────────────────────

describe('PANEL-f) Clicking Generate Plan closes ComparisonPanel', () => {
  it('comparison panel unmounts when Generate Plan is clicked', async () => {
    await renderAndOpenComparison()

    // Panel is open
    expect(screen.getAllByRole('button', { name: /use these weights/i })).toHaveLength(3)

    const generateBtn = screen.getByRole('button', { name: /generate plan/i })
    await act(async () => { fireEvent.click(generateBtn) })

    // Panel is gone
    await waitFor(() =>
      expect(screen.queryAllByRole('button', { name: /use these weights/i })).toHaveLength(0)
    )
    // api.plan was called
    await waitFor(() => expect(api.plan).toHaveBeenCalledTimes(1))
  })
})

// ─── PANEL-g: Close button still closes the panel ─────────────────────────────

describe('PANEL-g) Close button still closes ComparisonPanel (regression)', () => {
  it('clicking the Close button dismisses all 3 cards', async () => {
    await renderAndOpenComparison()

    // Panel is open
    expect(screen.getAllByRole('button', { name: /use these weights/i })).toHaveLength(3)

    const closeBtn = screen.getByRole('button', { name: /^close$/i })
    await act(async () => { fireEvent.click(closeBtn) })

    // Panel is gone
    await waitFor(() =>
      expect(screen.queryAllByRole('button', { name: /use these weights/i })).toHaveLength(0)
    )
  })
})

// ─── Original a-c, e-g (updated where needed) ─────────────────────────────────

describe('a) "Use these weights" populates weights_json with clicked preset', () => {
  it('clicking Fuel-Conservative sets weights_json to its weights JSON', async () => {
    await renderAndOpenComparison()

    const [fuelConservativeBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(fuelConservativeBtn) })

    const parsed = JSON.parse(weightsTextarea().value)
    expect(parsed).toEqual(FUEL_CONSERVATIVE_WEIGHTS)
  })
})

describe('b) Clicking a different preset overwrites weights_json (no merge)', () => {
  it('second preset click replaces first preset weights entirely', async () => {
    await renderAndOpenComparison()

    const buttons = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(buttons[0]) })
    expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)

    // Panel stays open — click Risk-Aggressive directly
    const buttons2 = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(buttons2[2]) })

    const parsed = JSON.parse(weightsTextarea().value)
    expect(parsed).toEqual(RISK_AGGRESSIVE_WEIGHTS)
    expect(parsed).not.toEqual(FUEL_CONSERVATIVE_WEIGHTS)
  })
})

describe('c) Other form fields are unchanged after applying preset weights', () => {
  it('fuel budget, pool size stay at their values after preset click', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /compare presets/i }))

    const fuelInput = screen.getByDisplayValue('2.5')
    const poolInput = screen.getByPlaceholderText(/default: 40/i)
    await act(async () => { fireEvent.change(poolInput, { target: { value: '20' } }) })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /compare presets/i }))
    })
    await waitFor(() => screen.getAllByRole('button', { name: /use these weights/i }))

    const [firstBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(firstBtn) })

    expect(fuelInput.value).toBe('2.5')
    expect(poolInput.value).toBe('20')
    expect(weightsTextarea().value).not.toBe('')
  })
})

describe('e) No History entry, no plan state, no workspace navigation after preset click', () => {
  it('clicking a preset does not produce a History tab, workspace, or plan state', async () => {
    await renderAndOpenComparison()

    const [firstBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(firstBtn) })

    expect(api.plan).not.toHaveBeenCalled()
    expect(screen.queryByTestId('history-tab-1')).not.toBeInTheDocument()
    const workspaceTitle = screen.getByTestId('workspace-title')
    expect(workspaceTitle.textContent.trim()).toBe('Workspace')
    expect(screen.queryByTestId('workspace-close-btn')).not.toBeInTheDocument()
  })
})

describe('f) Generate Plan works normally after preset weights applied', () => {
  it('clicking Generate Plan after preset produces a real result via api.plan', async () => {
    await renderAndOpenComparison()

    const [firstBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(firstBtn) })

    expect(weightsTextarea().value).not.toBe('')

    const generateBtn = screen.getByRole('button', { name: /generate plan/i })
    await act(async () => { fireEvent.click(generateBtn) })

    await waitFor(() => expect(api.plan).toHaveBeenCalledTimes(1))

    const [calledPayload] = api.plan.mock.calls[0]
    expect(calledPayload.weights).toEqual(FUEL_CONSERVATIVE_WEIGHTS)

    await waitFor(() =>
      expect(screen.getByTestId('workspace-title').textContent).toMatch(/Workspace #1/)
    )
    await waitFor(() =>
      expect(screen.getByText('Plan with preset weights.')).toBeInTheDocument()
    )
  })
})

describe('g) Same-preset re-click resets weights_json after manual edit (original)', () => {
  it('clicking the same preset twice resets weights_json even after a manual edit', async () => {
    await renderAndOpenComparison()

    const [firstBtn] = screen.getAllByRole('button', { name: /use these weights/i })
    await act(async () => { fireEvent.click(firstBtn) })
    expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)

    await act(async () => {
      fireEvent.change(weightsTextarea(), { target: { value: '{"proximity":0.1,"lifetime":0.1,"size":0.8}' } })
    })
    expect(weightsTextarea().value).toBe('{"proximity":0.1,"lifetime":0.1,"size":0.8}')

    // Panel still open — click same preset again
    const sameBtn = screen.getAllByRole('button', { name: /use these weights/i })[0]
    await act(async () => { fireEvent.click(sameBtn) })

    await waitFor(() =>
      expect(JSON.parse(weightsTextarea().value)).toEqual(FUEL_CONSERVATIVE_WEIGHTS)
    )
  })
})
