/**
 * debris-info-modal.test.jsx
 *
 * Tests for DebrisInfoModal.jsx
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

import DebrisInfoModal from './components/DebrisInfoModal.jsx'
import { api } from './api.js'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const DEBRIS = { norad_id: 25544, name: 'ISS' }
const DEBRIS_2 = { norad_id: 12345, name: 'SL-8 R/B' }

const DETAIL = {
  name: 'ISS',
  norad_id: 25544,
  object_type: 'intact',
  data_quality: 'fresh',
  epoch_age_days: 1,
  altitude_km: 420,
  latitude: 0,
  longitude: 0,
  inclination_deg: 51.6,
  raan_deg: 100,
  bstar: 1.23e-5,
  rcs_m2: 10,
  risk_score: 0.5,
  proximity_score: 0.1,
  lifetime_score: 0.2,
  size_score: 0.3,
  size_score_available: true,
  removal_method: 'net_capture',
  possible_methods: ['net_capture'],
  method_maturity: { net_capture: 'flight_demonstrated' },
  removal_method_explanation: 'Standard net capture.',
  removal_method_explanation_source: 'ESA',
}

const REASONING = {
  removal_method: 'net_capture',
  reasoning: 'This object is large and in LEO.',
  reasoning_unavailable: false,
  alternatives: [
    { name: 'robotic_arm_or_net_capture', why: 'Also viable due to size.' },
  ],
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  api.getDebrisById.mockResolvedValue(DETAIL)
  api.getRemovalMethods.mockResolvedValue(REASONING)
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('1) Renders null when debris prop is null', () => {
  it('renders nothing when debris is null', () => {
    const { container } = render(
      <DebrisInfoModal debris={null} pinned={false} onPin={vi.fn()} onClose={vi.fn()} />
    )
    expect(container.firstChild).toBeNull()
  })
})

describe('2) Info tab: loading/fetched/error', () => {
  it('shows loading state initially', async () => {
    let resolve
    api.getDebrisById.mockReturnValue(new Promise(r => { resolve = r }))

    render(
      <DebrisInfoModal debris={DEBRIS} pinned={false} onPin={vi.fn()} onClose={vi.fn()} />
    )

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    await act(async () => resolve(DETAIL))
  })

  it('renders detail rows after api.getDebrisById resolves', async () => {
    render(
      <DebrisInfoModal debris={DEBRIS} pinned={false} onPin={vi.fn()} onClose={vi.fn()} />
    )

    await waitFor(() => expect(screen.getByText('ISS')).toBeInTheDocument())
    expect(screen.getByText('420 km')).toBeInTheDocument()
    expect(screen.getByText('51.6°')).toBeInTheDocument()
  })

  it('renders error message when api.getDebrisById rejects', async () => {
    api.getDebrisById.mockRejectedValue(new Error('Not found'))

    render(
      <DebrisInfoModal debris={DEBRIS} pinned={false} onPin={vi.fn()} onClose={vi.fn()} />
    )

    await waitFor(() => expect(screen.getByText('Not found')).toBeInTheDocument())
  })
})

describe('3) Tab bar renders only when debris is in activeRouteNoradIds', () => {
  it('does NOT show tab bar when activeRouteNoradIds is null', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={null}
      />
    )
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('does NOT show tab bar when debris.norad_id not in activeRouteNoradIds', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([99999])}
      />
    )
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('shows tab bar when debris.norad_id is in activeRouteNoradIds', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('tablist')).toBeInTheDocument()
    // default tab is 'reason'
    const reasonTab = screen.getByRole('tab', { name: 'Reason' })
    expect(reasonTab).toHaveAttribute('aria-selected', 'true')
  })

  it('default active tab is "info" when not in activeRouteNoradIds', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([99999])}
      />
    )
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    // No tablist means info is the only (implicit) view; just verify no tab
    expect(screen.queryByRole('tab', { name: 'Reason' })).toBeNull()
  })
})

describe('4) Changing debris prop resets activeTab and clears reasoning', () => {
  it('resets to reason tab for new norad_id in route, clears prior reasoning', async () => {
    const { rerender } = render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )
    await waitFor(() => expect(api.getRemovalMethods).toHaveBeenCalledWith(DEBRIS.norad_id))

    api.getRemovalMethods.mockResolvedValue({ ...REASONING, removal_method: 'monitor_only' })

    rerender(
      <DebrisInfoModal
        debris={DEBRIS_2}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS_2.norad_id])}
      />
    )

    await waitFor(() => expect(api.getRemovalMethods).toHaveBeenCalledWith(DEBRIS_2.norad_id))
    // Should now show reason tab selected for DEBRIS_2
    const reasonTab = screen.getByRole('tab', { name: 'Reason' })
    expect(reasonTab).toHaveAttribute('aria-selected', 'true')
  })

  it('resets to info tab when new debris is not a route target', async () => {
    const { rerender } = render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    rerender(
      <DebrisInfoModal
        debris={DEBRIS_2}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )
    // DEBRIS_2 is not in route; no tablist
    await waitFor(() => expect(screen.queryByRole('tablist')).toBeNull())
  })
})

describe('5) Reason tab caches api.getRemovalMethods calls', () => {
  it('only calls api.getRemovalMethods once for the same norad_id (client-side cache)', async () => {
    api.getRemovalMethods.mockResolvedValue(REASONING)
    const { rerender } = render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    // Wait for first fetch
    await waitFor(() => expect(api.getRemovalMethods).toHaveBeenCalledTimes(1))

    // Switch away to info tab
    fireEvent.click(screen.getByRole('tab', { name: 'Info' }))

    // Switch back to reason tab
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: 'Reason' }))
    })

    // Should still be exactly 1 call
    expect(api.getRemovalMethods).toHaveBeenCalledTimes(1)
  })

  it('does NOT call api.getRemovalMethods when activeTab is "info" and object is a route target', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )
    // Default tab is reason — call will be made. Switch to info.
    await waitFor(() => expect(api.getRemovalMethods).toHaveBeenCalledTimes(1))

    // No more calls after switching to info
    expect(api.getRemovalMethods).toHaveBeenCalledTimes(1)
  })
})

describe('6) reasoning.reasoning_unavailable renders Groq-fallback copy', () => {
  it('renders Groq fallback when reasoning_unavailable is true', async () => {
    api.getRemovalMethods.mockResolvedValue({
      ...REASONING,
      reasoning_unavailable: true,
    })

    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    await waitFor(() =>
      expect(screen.getByText(/Reasoning generation failed/i)).toBeInTheDocument()
    )
    expect(screen.queryByText(REASONING.reasoning)).not.toBeInTheDocument()
  })
})

describe('7) Alternatives list renders only when present and reasoning_unavailable is falsy', () => {
  it('renders alternatives when reasoning_unavailable is false and alternatives has entries', async () => {
    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    await waitFor(() => expect(screen.getByText('Alternatives')).toBeInTheDocument())
    expect(screen.getByText('Also viable due to size.')).toBeInTheDocument()
  })

  it('does NOT render alternatives section when reasoning_unavailable is true', async () => {
    api.getRemovalMethods.mockResolvedValue({
      ...REASONING,
      reasoning_unavailable: true,
    })

    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    await waitFor(() =>
      expect(screen.getByText(/Reasoning generation failed/i)).toBeInTheDocument()
    )
    expect(screen.queryByText('Alternatives')).not.toBeInTheDocument()
  })

  it('does NOT render alternatives section when alternatives is empty', async () => {
    api.getRemovalMethods.mockResolvedValue({ ...REASONING, alternatives: [] })

    render(
      <DebrisInfoModal
        debris={DEBRIS}
        pinned={false}
        onPin={vi.fn()}
        onClose={vi.fn()}
        activeRouteNoradIds={new Set([DEBRIS.norad_id])}
      />
    )

    await waitFor(() =>
      expect(screen.getByText(REASONING.reasoning)).toBeInTheDocument()
    )
    expect(screen.queryByText('Alternatives')).not.toBeInTheDocument()
  })
})

describe('8) Pin and Close buttons', () => {
  it('calls onPin when Pin button is clicked', async () => {
    const onPin = vi.fn()
    render(
      <DebrisInfoModal debris={DEBRIS} pinned={false} onPin={onPin} onClose={vi.fn()} />
    )
    // Let the on-mount getDebrisById fetch resolve and flush before
    // asserting, so the state update it triggers doesn't land after the
    // test has already finished (unflushed act() warning).
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: /pin/i }))
    expect(onPin).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when Close button is clicked', async () => {
    const onClose = vi.fn()
    render(
      <DebrisInfoModal debris={DEBRIS} pinned={false} onPin={vi.fn()} onClose={onClose} />
    )
    await waitFor(() => expect(api.getDebrisById).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: /close debris panel/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
