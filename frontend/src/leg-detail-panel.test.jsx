/**
 * leg-detail-panel.test.jsx
 *
 * Tests for LegDetailPanel.jsx
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

import LegDetailPanel from './components/LegDetailPanel.jsx'
import { api } from './api.js'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

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

const STEP_WITH_WAIT = {
  ...STEP,
  recommended_wait_days: 3,
  fuel_saved_km_s: 0.2,
}

const LEG_DATA = {
  explanation: 'This transfer requires a Hohmann maneuver.',
  explanation_unavailable: false,
  from_obj: { name: 'Depot', norad_id: -1, is_depot: true },
  to_obj: { name: 'ISS', norad_id: 25544, data_quality: 'fresh', risk_score: 0.5, epoch_age_days: 2 },
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  api.getLegExplanation.mockResolvedValue(LEG_DATA)
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('1) Renders null when step prop is null', () => {
  it('returns null when step is null', () => {
    const { container } = render(
      <LegDetailPanel step={null} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )
    expect(container.firstChild).toBeNull()
  })
})

describe('2) FROM card renders Depot when fromNoradId === -1', () => {
  it('shows Depot for fromNoradId -1 before data loads', async () => {
    let resolve
    api.getLegExplanation.mockReturnValue(new Promise(r => { resolve = r }))

    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    // From the static endpointCard rendered before fetch resolves
    expect(screen.getByText('Depot')).toBeInTheDocument()
    expect(screen.getByText('spacecraft start')).toBeInTheDocument()

    await act(async () => resolve(LEG_DATA))
  })

  it('does not need a separate api call just for the depot label', async () => {
    let resolve
    api.getLegExplanation.mockReturnValue(new Promise(r => { resolve = r }))

    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    // "Depot" label comes from the fromNoradId === -1 fallback, no api needed
    expect(screen.getByText('Depot')).toBeInTheDocument()

    await act(async () => resolve(LEG_DATA))
  })
})

describe('3) Loading state and explanation rendering', () => {
  it('shows loading state while api.getLegExplanation is pending', async () => {
    let resolve
    api.getLegExplanation.mockReturnValue(new Promise(r => { resolve = r }))

    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    expect(screen.getByText('Generating explanation…')).toBeInTheDocument()
    await act(async () => resolve(LEG_DATA))
  })

  it('renders data.explanation after api.getLegExplanation resolves', async () => {
    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() =>
      expect(screen.getByText('This transfer requires a Hohmann maneuver.')).toBeInTheDocument()
    )
  })

  it('renders explanation_unavailable fallback when data.explanation_unavailable is true', async () => {
    api.getLegExplanation.mockResolvedValue({ ...LEG_DATA, explanation_unavailable: true })

    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() =>
      expect(screen.getByText(/Explanation unavailable/i)).toBeInTheDocument()
    )
    expect(screen.queryByText(LEG_DATA.explanation)).not.toBeInTheDocument()
  })
})

describe('4) Cache keyed by fromNoradId:toNoradId', () => {
  it('does NOT call api.getLegExplanation a second time for the same pair', async () => {
    const { rerender } = render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() => expect(api.getLegExplanation).toHaveBeenCalledTimes(1))

    // Re-render with same fromNoradId/toNoradId pair
    rerender(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    // Allow any async updates
    await act(async () => {})

    expect(api.getLegExplanation).toHaveBeenCalledTimes(1)
  })

  it('calls api.getLegExplanation again for a different toNoradId', async () => {
    api.getLegExplanation.mockResolvedValue({ ...LEG_DATA, explanation: 'Different leg.' })

    const { rerender } = render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() => expect(api.getLegExplanation).toHaveBeenCalledTimes(1))

    rerender(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={99999} legIndex={2} onClose={vi.fn()} />
    )

    await waitFor(() => expect(api.getLegExplanation).toHaveBeenCalledTimes(2))
  })
})

describe('5) J2 nodal drift wait section', () => {
  it('renders when recommended_wait_days > 0 AND fuel_saved_km_s > 0', async () => {
    render(
      <LegDetailPanel step={STEP_WITH_WAIT} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() =>
      expect(screen.getByText('J2 nodal drift wait')).toBeInTheDocument()
    )
    expect(screen.getByText('3 day(s)')).toBeInTheDocument()
    expect(screen.getByText('0.2 km/s')).toBeInTheDocument()
  })

  it('does NOT render when recommended_wait_days is 0', async () => {
    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={vi.fn()} />
    )

    await waitFor(() =>
      expect(screen.getByText('This transfer requires a Hohmann maneuver.')).toBeInTheDocument()
    )
    expect(screen.queryByText('J2 nodal drift wait')).not.toBeInTheDocument()
  })

  it('does NOT render when fuel_saved_km_s is 0 even if recommended_wait_days > 0', async () => {
    render(
      <LegDetailPanel
        step={{ ...STEP, recommended_wait_days: 3, fuel_saved_km_s: 0 }}
        fromNoradId={-1}
        toNoradId={25544}
        legIndex={1}
        onClose={vi.fn()}
      />
    )

    await waitFor(() =>
      expect(screen.getByText('This transfer requires a Hohmann maneuver.')).toBeInTheDocument()
    )
    expect(screen.queryByText('J2 nodal drift wait')).not.toBeInTheDocument()
  })
})

describe('6) Close button calls onClose', () => {
  it('calls onClose when clicked', () => {
    const onClose = vi.fn()
    render(
      <LegDetailPanel step={STEP} fromNoradId={-1} toNoradId={25544} legIndex={1} onClose={onClose} />
    )
    fireEvent.click(screen.getByRole('button', { name: /close leg detail panel/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
