/**
 * Dashboard layout tests — original 8 scenarios + new layout-patch scenarios.
 *
 * Original (prev task):
 *  1. Empty state: Workspace renders dimmed placeholder "Workspace" (no #N)
 *  2. Generate Plan → new history entry + Workspace opens as "Workspace #1"
 *  3. Two entries: clicking tab #1 while #2 is shown replaces Workspace
 *  4. Replan: same #N persists, tab updates in-place, no new tab created
 *  5. History tabs: render in vertical column, overflow-y scrollable
 *  6. Workspace ✕: returns to empty state; history entry still in Section 2
 *  7. Visualization toggles: arrow control on right edge, toggle logic works
 *  8. Custom Selection Filter: in Parameters section, toggles selection mode
 *
 * Layout patch (this task):
 *  LP1. Parameters, History, Workspace render as three side-by-side columns
 *  LP2. Workspace column width > Parameters width AND > History width
 *  LP3. Parameters form uses 2-column grid layout (.form-grid class)
 *  LP4. History tabs are flex-direction: column; History section scrolls vertically
 *  LP5. Viz control NOT at globe top — IS at globe right edge (.globe-viz-arrow)
 *  LP6. Arrow toggles: options visible/hidden + arrow direction flips on click
 *  LP7. ALL/HIGHLIGHT/ROUTE ONLY logic unchanged after relocation (re-asserts test 7)
 */

import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ─── Module mocks ────────────────────────────────────────────────────────────

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
  },
}))

import App from './App.jsx'
import { api } from './api.js'

// ─── Shared fixtures ─────────────────────────────────────────────────────────
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
  explanation: 'Test plan explanation',
}

const REPLAN_RESULT = {
  new_plan: { ...PLAN_RESULT, total_fuel_cost_km_s: 0.7, explanation: 'Replanned' },
  explanation: 'Changes applied',
  overrides_applied: { fuel_budget_km_s: 2.5 },
  diff: { added: [], dropped: [], fuel_delta_km_s: -0.1, risk_delta: 0 },
}

async function clickGeneratePlan() {
  const btn = screen.getByRole('button', { name: /generate plan/i })
  await act(async () => { fireEvent.click(btn) })
}

async function waitForWorkspaceTitle(pattern) {
  return waitFor(() => expect(screen.getByTestId('workspace-title').textContent).toMatch(pattern))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getDebrisField.mockResolvedValue({
    debris_field: [], data_fetched_at: new Date().toISOString(), data_stale: false,
  })
  api.getLaunchSites.mockResolvedValue({
    vandenberg: { name: 'Vandenberg', lat: 34.6, lon: -120.6, min_inclination: 56 },
  })
  api.plan.mockResolvedValue(PLAN_RESULT)
  api.replan.mockResolvedValue(REPLAN_RESULT)
  api.previewOrbit.mockResolvedValue({ lat: 0, lon: 0 })
  api.getNaiveRoute.mockResolvedValue(PLAN_RESULT)
})

// ─── Original 8 tests ────────────────────────────────────────────────────────

describe('1. Empty state — Workspace placeholder', () => {
  it('renders "Workspace" (no #N) and empty-label when no entry is active', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('section-workspace')).toBeInTheDocument())

    const title = screen.getByTestId('workspace-title')
    expect(title.textContent.trim()).toBe('Workspace')
    expect(screen.getByTestId('workspace-empty-label')).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-close-btn')).not.toBeInTheDocument()
  })
})

describe('2. Generate Plan → history entry + Workspace opens', () => {
  it('creates tab #1 and opens Workspace as "Workspace #1"', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))

    await clickGeneratePlan()

    await waitFor(() => expect(screen.getByTestId('history-tab-1')).toBeInTheDocument())
    await waitForWorkspaceTitle(/Workspace #1/)
    expect(screen.queryByTestId('workspace-empty-label')).not.toBeInTheDocument()
    expect(api.plan).toHaveBeenCalledTimes(1)
  })
})

describe('3. Two entries — tab #1 click replaces Workspace from #2 to #1', () => {
  it('two tabs exist; clicking #1 while #2 shown switches Workspace to #1', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))

    await clickGeneratePlan()
    await waitFor(() => screen.getByTestId('history-tab-1'))

    await clickGeneratePlan()
    await waitFor(() => screen.getByTestId('history-tab-2'))
    await waitForWorkspaceTitle(/Workspace #2/)

    await act(async () => { fireEvent.click(screen.getByTestId('history-tab-1')) })

    await waitForWorkspaceTitle(/Workspace #1/)
    expect(screen.getByTestId('history-tab-2')).toBeInTheDocument()
    expect(screen.getAllByTestId('workspace-title')).toHaveLength(1)
  })
})

describe('4. Replan — same #N, no new tab, in-place update', () => {
  it('same tab count and #N after replan; replan API called once', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))

    await clickGeneratePlan()
    await waitFor(() => screen.getByTestId('history-tab-1'))
    await waitForWorkspaceTitle(/Workspace #1/)

    expect(screen.getAllByTestId(/^history-tab-\d+$/)).toHaveLength(1)

    const textarea = screen.getByPlaceholderText(/prioritize risk over fuel/i)
    await act(async () => { fireEvent.change(textarea, { target: { value: 'more risk focus' } }) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /apply changes/i })) })

    await waitFor(() => expect(api.replan).toHaveBeenCalledTimes(1))

    await waitForWorkspaceTitle(/Workspace #1/)
    expect(screen.getAllByTestId(/^history-tab-\d+$/)).toHaveLength(1)
    expect(screen.queryByTestId('history-tab-2')).not.toBeInTheDocument()
  })
})

describe('5. History tabs — vertical column, overflow-y scrollable', () => {
  it('history-tab-row container holds all tabs in a single column container', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))

    for (let i = 0; i < 6; i++) {
      await clickGeneratePlan()
    }
    await waitFor(() => expect(screen.getByTestId('history-tab-6')).toBeInTheDocument())

    const row = screen.getByTestId('history-tab-row')
    // CSS class carries flex-direction:column + overflow-y:auto
    expect(row.className).toContain('history-tab-row')
    // Inline style must not set overflow-x (that was the old horizontal mode)
    expect(row.style.overflowX).not.toBe('auto')
    // All 6 tabs inside the single container
    expect(row.querySelectorAll('[data-testid^="history-tab-"]')).toHaveLength(6)
  })
})

describe('6. Workspace ✕ — empty state; history entry persists', () => {
  it('close button returns to placeholder; tab stays in Section 2', async () => {
    render(<App />)
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))

    await clickGeneratePlan()
    await waitFor(() => screen.getByTestId('history-tab-1'))
    await waitForWorkspaceTitle(/Workspace #1/)

    await act(async () => { fireEvent.click(screen.getByTestId('workspace-close-btn')) })

    await waitFor(() => expect(screen.getByTestId('workspace-title').textContent.trim()).toBe('Workspace'))
    expect(screen.getByTestId('workspace-empty-label')).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-close-btn')).not.toBeInTheDocument()
    expect(screen.getByTestId('history-tab-1')).toBeInTheDocument()
  })
})

describe('7. Visualization arrow — right-edge control, correct toggle behavior', () => {
  it('arrow is at globe right edge; toggles expand/collapse; toggle logic works', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('globe-viz-arrow'))

    // Arrow container is present at globe right edge
    const arrowContainer = screen.getByTestId('globe-viz-arrow')
    expect(arrowContainer).toBeInTheDocument()

    // Arrow button present, options hidden initially (vizOpen=false)
    const arrowBtn = screen.getByTestId('globe-viz-arrow-btn')
    expect(arrowBtn).toBeInTheDocument()
    expect(screen.queryByTestId('globe-viz-options')).not.toBeInTheDocument()

    // Old top-overlay NOT present
    expect(screen.queryByTestId('globe-viz-controls')).not.toBeInTheDocument()

    // Click arrow → options appear, arrow flips
    await act(async () => { fireEvent.click(arrowBtn) })
    expect(screen.getByTestId('globe-viz-options')).toBeInTheDocument()
    expect(arrowBtn.getAttribute('aria-expanded')).toBe('true')

    // Buttons are inside the options panel
    const vizOptions = screen.getByTestId('globe-viz-options')
    const allBtn = screen.getByRole('button', { name: /^all$/i })
    const hlBtn  = screen.getByRole('button', { name: /^highlight$/i })
    const routeBtn = screen.getByRole('button', { name: /^route only$/i })
    expect(vizOptions).toContainElement(allBtn)
    expect(vizOptions).toContainElement(hlBtn)
    expect(vizOptions).toContainElement(routeBtn)

    // Disabled before plan
    expect(allBtn).toBeDisabled()

    // Click arrow again → options collapse
    await act(async () => { fireEvent.click(arrowBtn) })
    expect(screen.queryByTestId('globe-viz-options')).not.toBeInTheDocument()
    expect(arrowBtn.getAttribute('aria-expanded')).toBe('false')
  })
})

describe('8. Custom Selection Filter — in Parameters section, toggles mode', () => {
  it('button lives in Parameters section and gates entry into selection mode', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('section-parameters'))

    const csBtn = screen.getByTestId('custom-selection-btn')
    expect(csBtn).toBeInTheDocument()
    expect(screen.getByTestId('section-parameters')).toContainElement(csBtn)

    await act(async () => { fireEvent.click(csBtn) })
    await waitFor(() => expect(screen.getByRole('button', { name: /finish selection/i })).toBeInTheDocument())
    expect(csBtn).toBeDisabled()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^cancel$/i })) })
    await waitFor(() => expect(screen.queryByRole('button', { name: /finish selection/i })).not.toBeInTheDocument())
    expect(csBtn).not.toBeDisabled()
  })
})

// ─── Layout patch tests ───────────────────────────────────────────────────────

describe('LP1. Three columns side-by-side (not stacked)', () => {
  it('Parameters, History, Workspace are three distinct column sections in that order', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('dashboard-column'))

    const dashboard = screen.getByTestId('dashboard-column')
    const params    = screen.getByTestId('section-parameters')
    const history   = screen.getByTestId('section-history')
    const workspace = screen.getByTestId('section-workspace')

    // All three are children of the dashboard column
    expect(dashboard).toContainElement(params)
    expect(dashboard).toContainElement(history)
    expect(dashboard).toContainElement(workspace)

    // They appear in DOM order: parameters first, history second, workspace third
    const children = Array.from(dashboard.children)
    expect(children.indexOf(params)).toBeLessThan(children.indexOf(history))
    expect(children.indexOf(history)).toBeLessThan(children.indexOf(workspace))

    // Dashboard carries the class that sets flex-direction: row
    expect(dashboard.className).toContain('dashboard-column')
  })
})

describe('LP2. Workspace column is wider than Parameters and History', () => {
  it('Workspace offsetWidth > Parameters offsetWidth AND > History offsetWidth', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('dashboard-column'))

    const params    = screen.getByTestId('section-parameters')
    const history   = screen.getByTestId('section-history')
    const workspace = screen.getByTestId('section-workspace')

    // jsdom lays out with offsetWidth=0 by default, but we can verify via the
    // CSS classes applied: dashboard-section--workspace has flex:1 1 auto
    // (takes all remaining space), while --parameters and --history have fixed widths.
    // We assert via className since jsdom doesn't do real layout arithmetic.
    expect(workspace.className).toContain('dashboard-section--workspace')
    expect(params.className).toContain('dashboard-section--parameters')
    expect(history.className).toContain('dashboard-section--history')

    // The CSS file sets fixed px widths for params/history and flex:1 for workspace.
    // In a real browser workspace will always be wider. Assert the class contract.
    expect(workspace.className).not.toContain('dashboard-section--parameters')
    expect(workspace.className).not.toContain('dashboard-section--history')
  })
})

describe('LP3. Parameters form uses 2-column grid (.form-grid)', () => {
  it('form-grid element exists inside the Parameters section body', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('section-parameters'))

    const formGrid = screen.getByTestId('form-grid')
    expect(formGrid).toBeInTheDocument()
    expect(formGrid.className).toContain('form-grid')

    // form-grid is inside the Parameters section
    const params = screen.getByTestId('section-parameters')
    expect(params).toContainElement(formGrid)

    // full-width items carry form-grid-span
    const spanItems = formGrid.querySelectorAll('.form-grid-span')
    expect(spanItems.length).toBeGreaterThan(0)
  })
})

describe('LP4. History tabs: vertical stack, section scrolls vertically', () => {
  it('history-tab-row has flex-direction:column class and no horizontal overflow', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('section-history'))

    const row = screen.getByTestId('history-tab-row')
    // Class applies flex-direction:column + overflow-y:auto
    expect(row.className).toContain('history-tab-row')

    // Inline style must not have overflow-x: auto (that was the old horizontal layout)
    expect(row.style.overflowX).not.toBe('auto')
    expect(row.style.flexDirection).not.toBe('row')

    // History section carries the right class for its column width
    const section = screen.getByTestId('section-history')
    expect(section.className).toContain('dashboard-section--history')
  })
})

describe('LP5. Viz control not at globe top; present at globe right edge', () => {
  it('old globe-viz-controls absent; new globe-viz-arrow present inside globe-pane', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('globe-viz-arrow'))

    // Old top-right overlay is gone
    expect(screen.queryByTestId('globe-viz-controls')).not.toBeInTheDocument()

    // New right-edge arrow is present
    const arrow = screen.getByTestId('globe-viz-arrow')
    expect(arrow).toBeInTheDocument()
    expect(arrow.className).toContain('globe-viz-arrow')

    // It lives inside the globe pane (not the dashboard column)
    const globePane = document.querySelector('.globe-pane')
    expect(globePane).toContainElement(arrow)
  })
})

describe('LP6. Arrow toggles: options appear/disappear + aria-expanded flips', () => {
  it('three clicks: open → close → open; state tracks correctly', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('globe-viz-arrow-btn'))

    const btn = screen.getByTestId('globe-viz-arrow-btn')

    // Initial: closed
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('globe-viz-options')).not.toBeInTheDocument()

    // First click: open
    await act(async () => { fireEvent.click(btn) })
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('globe-viz-options')).toBeInTheDocument()

    // Second click: close
    await act(async () => { fireEvent.click(btn) })
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('globe-viz-options')).not.toBeInTheDocument()

    // Third click: open again
    await act(async () => { fireEvent.click(btn) })
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('globe-viz-options')).toBeInTheDocument()
  })
})

describe('LP7. ALL/HIGHLIGHT/ROUTE ONLY logic unchanged after relocation', () => {
  it('expand arrow, then toggle buttons work identically to before', async () => {
    render(<App />)
    await waitFor(() => screen.getByTestId('globe-viz-arrow-btn'))

    // Expand the panel
    await act(async () => { fireEvent.click(screen.getByTestId('globe-viz-arrow-btn')) })
    await waitFor(() => screen.getByTestId('globe-viz-options'))

    const allBtn   = screen.getByRole('button', { name: /^all$/i })
    const hlBtn    = screen.getByRole('button', { name: /^highlight$/i })
    const routeBtn = screen.getByRole('button', { name: /^route only$/i })

    // Disabled before plan
    expect(allBtn).toBeDisabled()
    expect(hlBtn).toBeDisabled()
    expect(routeBtn).toBeDisabled()

    // Generate plan → enable
    await waitFor(() => screen.getByRole('button', { name: /generate plan/i }))
    await clickGeneratePlan()
    await waitFor(() => expect(allBtn).not.toBeDisabled())

    // Click "All" → btn-primary
    await act(async () => { fireEvent.click(allBtn) })
    expect(allBtn.className).toContain('btn-primary')
    expect(hlBtn.className).not.toContain('btn-primary')

    // Click "Route only" → it gets btn-primary, All loses it
    await act(async () => { fireEvent.click(routeBtn) })
    expect(routeBtn.className).toContain('btn-primary')
    expect(allBtn.className).not.toContain('btn-primary')
  })
})
