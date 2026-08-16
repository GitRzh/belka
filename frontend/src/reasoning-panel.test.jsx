/**
 * reasoning-panel.test.jsx
 *
 * Tests for ReasoningPanel.jsx (pure props — no API mocking needed)
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
    getDebrisById: vi.fn(),
    getRemovalMethods: vi.fn(),
    getLegExplanation: vi.fn(),
  },
}))

import ReasoningPanel from './components/ReasoningPanel.jsx'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const DEBRIS_A = { norad_id: 25544, name: 'ISS', longitude: 10, latitude: 5, altitude_km: 420 }
const DEBRIS_B = { norad_id: 12345, name: 'SL-8 R/B', longitude: 50, latitude: 20, altitude_km: 600 }

const BASE_PLAN = {
  visited_count: 2,
  pool_size_used: 5,
  total_fuel_cost_km_s: 0.8,
  fuel_budget_km_s: 2.5,
  fuel_used_fraction: 0.32,
  total_risk_collected: 0.5,
  explanation: 'Route optimised for risk.',
  warning: null,
  skipped_count: 0,
  skipped_names: null,
  step_breakdown: [],
}

const STEP = {
  from: 'Depot',
  to: 'ISS (25544)',
  delta_v_km_s: 0.5,
  arrival_time_days: 10,
  raan_drift_deg: 5,
  recommended_wait_days: 0,
  fuel_saved_km_s: 0,
  data_quality: 'fresh',
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('1) Renders null when plan prop is null', () => {
  it('renders nothing when plan is null', () => {
    const { container } = render(<ReasoningPanel plan={null} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('2) Warning / explanation / fallback rendering', () => {
  it('renders plan.warning in an alert role', () => {
    render(<ReasoningPanel plan={{ ...BASE_PLAN, warning: 'Fuel budget tight.' }} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Fuel budget tight.')
  })

  it('no alert element when warning is null', () => {
    render(<ReasoningPanel plan={BASE_PLAN} />)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('explanationOverride takes priority over plan.explanation when both are present', () => {
    render(
      <ReasoningPanel
        plan={BASE_PLAN}
        explanationOverride="Override text from replan."
      />
    )
    expect(screen.getByText('Override text from replan.')).toBeInTheDocument()
    expect(screen.queryByText(BASE_PLAN.explanation)).not.toBeInTheDocument()
  })

  it('renders plan.explanation when explanationOverride is not provided', () => {
    render(<ReasoningPanel plan={BASE_PLAN} />)
    expect(screen.getByText('Route optimised for risk.')).toBeInTheDocument()
  })

  it('renders italic fallback when neither explanation nor explanationOverride is present', () => {
    render(<ReasoningPanel plan={{ ...BASE_PLAN, explanation: null }} />)
    expect(screen.getByText(/Explanation unavailable/i)).toBeInTheDocument()
  })

  it('appends plan.explanation_error text in fallback when present', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, explanation: null, explanation_error: 'LLM timeout' }}
      />
    )
    expect(screen.getByText(/Explanation unavailable — LLM timeout/i)).toBeInTheDocument()
  })
})

describe('3) Skipped targets row', () => {
  it('does NOT render skipped targets row when skipped_count is 0', () => {
    render(<ReasoningPanel plan={{ ...BASE_PLAN, skipped_count: 0 }} />)
    expect(screen.queryByText('Skipped targets')).not.toBeInTheDocument()
  })

  it('renders skipped targets row when skipped_count > 0', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, skipped_count: 2, skipped_names: ['OBJ-A', 'OBJ-B'] }}
      />
    )
    expect(screen.getByText('Skipped targets')).toBeInTheDocument()
    expect(screen.getByText(/OBJ-A, OBJ-B/)).toBeInTheDocument()
  })

  it('falls back to empty array when skipped_names is null (not just undefined)', () => {
    // skipped_names explicitly null — must not throw
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, skipped_count: 1, skipped_names: null }}
      />
    )
    expect(screen.getByText('Skipped targets')).toBeInTheDocument()
    // With null skipped_names the join produces an empty string
    // Row reads: "1 ()" — just assert no crash and row present
    expect(screen.getByText(/1/)).toBeInTheDocument()
  })
})

describe('4) Manifest table leg-index button', () => {
  it('renders leg index as a button when toDebris resolves from debrisField', () => {
    const globeRef = { current: { flyToLeg: vi.fn(), flyTo: vi.fn() } }

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [STEP] }}
        globeRef={globeRef}
        debrisField={[DEBRIS_A]}
      />
    )
    // Open the details element
    fireEvent.click(screen.getByText(/Flight manifest/))
    const btn = screen.getByRole('button', { name: /01/i })
    expect(btn).toBeInTheDocument()
  })

  it('renders leg index as a button when onLegClick is provided and effectiveToId is non-null', () => {
    const onLegClick = vi.fn()

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [STEP] }}
        onLegClick={onLegClick}
        debrisField={[]}
      />
    )
    fireEvent.click(screen.getByText(/Flight manifest/))
    const btn = screen.getByRole('button', { name: /01/i })
    expect(btn).toBeInTheDocument()
  })

  it('renders leg index as plain text when neither toDebris resolves nor onLegClick provided', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [STEP] }}
        debrisField={[]}
      />
    )
    fireEvent.click(screen.getByText(/Flight manifest/))
    // No button with that label
    expect(screen.queryByRole('button', { name: /01/i })).toBeNull()
    expect(screen.getByText('01')).toBeInTheDocument()
  })

  it('clicking leg button calls globeRef.current.flyToLeg and onLegClick', () => {
    const flyToLeg = vi.fn()
    const globeRef = { current: { flyToLeg, flyTo: vi.fn() } }
    const onLegClick = vi.fn()

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [STEP] }}
        globeRef={globeRef}
        debrisField={[DEBRIS_A]}
        onLegClick={onLegClick}
      />
    )

    fireEvent.click(screen.getByText(/Flight manifest/))
    fireEvent.click(screen.getByRole('button', { name: /01/i }))

    expect(flyToLeg).toHaveBeenCalledTimes(1)
    expect(onLegClick).toHaveBeenCalledTimes(1)
    // onLegClick(step, effectiveFromId, effectiveToId, legNumber)
    // From: 'Depot' → fromNoradId = null → effectiveFromId = -1
    // To:   'ISS (25544)' → effectiveToId = 25544
    expect(onLegClick).toHaveBeenCalledWith(STEP, -1, 25544, 1)
  })
})

describe('5) Debris-name buttons in From/To manifest cells', () => {
  it('renders From cell as a button when debrisField resolves fromNoradId AND onDebrisSelect is provided', () => {
    const onDebrisSelect = vi.fn()
    const stepFromDebris = {
      ...STEP,
      from: 'ISS (25544)',
      to: 'SL-8 R/B (12345)',
    }

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [stepFromDebris] }}
        debrisField={[DEBRIS_A, DEBRIS_B]}
        onDebrisSelect={onDebrisSelect}
      />
    )

    fireEvent.click(screen.getByText(/Flight manifest/))
    // The "from" cell should be a button
    const fromBtn = screen.getByRole('button', { name: 'ISS (25544)' })
    expect(fromBtn).toBeInTheDocument()
  })

  it('clicking From name button calls globeRef.current.flyTo and onDebrisSelect', () => {
    const flyTo = vi.fn()
    const globeRef = { current: { flyToLeg: vi.fn(), flyTo } }
    const onDebrisSelect = vi.fn()
    const stepFromDebris = {
      ...STEP,
      from: 'ISS (25544)',
      to: 'SL-8 R/B (12345)',
    }

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [stepFromDebris] }}
        globeRef={globeRef}
        debrisField={[DEBRIS_A, DEBRIS_B]}
        onDebrisSelect={onDebrisSelect}
      />
    )

    fireEvent.click(screen.getByText(/Flight manifest/))
    fireEvent.click(screen.getByRole('button', { name: 'ISS (25544)' }))

    expect(flyTo).toHaveBeenCalledWith(DEBRIS_A.longitude, DEBRIS_A.latitude, DEBRIS_A.altitude_km)
    expect(onDebrisSelect).toHaveBeenCalledWith(DEBRIS_A)
  })

  it('renders From cell as plain text when onDebrisSelect is NOT provided', () => {
    const stepFromDebris = {
      ...STEP,
      from: 'ISS (25544)',
      to: 'SL-8 R/B (12345)',
    }

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [stepFromDebris] }}
        debrisField={[DEBRIS_A, DEBRIS_B]}
      />
    )

    fireEvent.click(screen.getByText(/Flight manifest/))
    expect(screen.queryByRole('button', { name: 'ISS (25544)' })).toBeNull()
    expect(screen.getByText('ISS (25544)')).toBeInTheDocument()
  })

  it('clicking To name button calls globeRef.current.flyTo and onDebrisSelect', () => {
    const flyTo = vi.fn()
    const globeRef = { current: { flyToLeg: vi.fn(), flyTo } }
    const onDebrisSelect = vi.fn()
    const stepFromDebris = {
      ...STEP,
      from: 'Depot',
      to: 'ISS (25544)',
    }

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, step_breakdown: [stepFromDebris] }}
        globeRef={globeRef}
        debrisField={[DEBRIS_A]}
        onDebrisSelect={onDebrisSelect}
      />
    )

    fireEvent.click(screen.getByText(/Flight manifest/))
    fireEvent.click(screen.getByRole('button', { name: 'ISS (25544)' }))

    expect(flyTo).toHaveBeenCalledWith(DEBRIS_A.longitude, DEBRIS_A.latitude, DEBRIS_A.altitude_km)
    expect(onDebrisSelect).toHaveBeenCalledWith(DEBRIS_A)
  })
})

describe('6) Proposal buttons', () => {
  const proposals = [
    { proposal: 'Increase fuel budget', reason: 'Current budget is too low.' },
    { proposal: 'Reduce pool size', reason: 'Too many targets for the orbit.' },
  ]

  it('does NOT render proposals when proposals array is empty', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 0 }}
        proposals={[]}
        onApplyProposal={vi.fn()}
      />
    )
    expect(screen.queryByText('Suggested fixes')).not.toBeInTheDocument()
  })

  it('renders proposal buttons when proposals is a non-empty array (showProposals only gates on proposals.length)', () => {
    // The component: const showProposals = Array.isArray(proposals) && proposals.length > 0
    // visited_count is NOT read in the render condition — proposals alone controls rendering.
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 0 }}
        proposals={proposals}
        onApplyProposal={vi.fn()}
      />
    )
    expect(screen.getByText('Increase fuel budget')).toBeInTheDocument()
    expect(screen.getByText('Reduce pool size')).toBeInTheDocument()
  })

  it('renders proposal buttons even when visited_count > 0 if proposals is non-empty', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 1 }}
        proposals={proposals}
        onApplyProposal={vi.fn()}
      />
    )
    expect(screen.getByText('Increase fuel budget')).toBeInTheDocument()
  })

  it('clicking a proposal button calls onApplyProposal with that proposal', () => {
    const onApplyProposal = vi.fn()

    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 0 }}
        proposals={proposals}
        onApplyProposal={onApplyProposal}
      />
    )

    fireEvent.click(screen.getByText('Increase fuel budget'))
    expect(onApplyProposal).toHaveBeenCalledTimes(1)
    expect(onApplyProposal).toHaveBeenCalledWith(proposals[0])
  })

  it('proposal buttons are disabled when submitting is true', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 0 }}
        proposals={proposals}
        onApplyProposal={vi.fn()}
        submitting={true}
      />
    )
    const buttons = screen.getAllByRole('button', { name: /Increase fuel budget|Reduce pool size/ })
    buttons.forEach(btn => expect(btn).toBeDisabled())
  })

  it('proposal buttons are enabled when submitting is false', () => {
    render(
      <ReasoningPanel
        plan={{ ...BASE_PLAN, visited_count: 0 }}
        proposals={proposals}
        onApplyProposal={vi.fn()}
        submitting={false}
      />
    )
    const buttons = screen.getAllByRole('button', { name: /Increase fuel budget|Reduce pool size/ })
    buttons.forEach(btn => expect(btn).not.toBeDisabled())
  })
})
