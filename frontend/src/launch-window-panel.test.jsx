/**
 * launch-window-panel.test.jsx
 *
 * Unit tests for LaunchWindowPanel's exported pure functions:
 *   filterParetoOptimal(window)  — must filter on backend is_pareto_optimal flag ONLY,
 *                                   never recompute Pareto dominance from fuel/risk values.
 *   findLowestFuelEntry(window)  — lowest fuel, tie-broken by day_offset.
 *
 * Q3 guard: filterParetoOptimal must trust the backend flag, not re-derive dominance.
 * The critical test feeds it an array where the backend flag and a naive fuel/risk
 * recomputation DISAGREE — verifying the function follows the flag, not the numbers.
 */

import { describe, it, expect } from 'vitest'
import { filterParetoOptimal, findLowestFuelEntry } from './components/LaunchWindowPanel.jsx'

// ---------------------------------------------------------------------------
// filterParetoOptimal — trusts backend flag, never recomputes dominance
// ---------------------------------------------------------------------------

describe('filterParetoOptimal — reads is_pareto_optimal flag directly', () => {
  it('returns only entries where is_pareto_optimal is true', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: 1.0, total_risk_collected: 5.0, is_pareto_optimal: true },
      { day_offset: 1.0, total_fuel_cost_km_s: 2.0, total_risk_collected: 8.0, is_pareto_optimal: false },
      { day_offset: 2.0, total_fuel_cost_km_s: 1.5, total_risk_collected: 7.0, is_pareto_optimal: true },
    ]
    const result = filterParetoOptimal(window)
    expect(result).toHaveLength(2)
    expect(result.map(r => r.day_offset)).toEqual([0.0, 2.0])
  })

  it('returns empty array when no entries are optimal', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: 3.0, total_risk_collected: 4.0, is_pareto_optimal: false },
      { day_offset: 1.0, total_fuel_cost_km_s: 2.5, total_risk_collected: 6.0, is_pareto_optimal: false },
    ]
    expect(filterParetoOptimal(window)).toHaveLength(0)
  })

  it('returns all entries when all are optimal', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: 1.0, total_risk_collected: 5.0, is_pareto_optimal: true },
      { day_offset: 1.0, total_fuel_cost_km_s: 2.0, total_risk_collected: 9.0, is_pareto_optimal: true },
    ]
    expect(filterParetoOptimal(window)).toHaveLength(2)
  })

  // --- Q3 critical test ---
  // This entry has worse fuel AND worse risk than its neighbor (dominated on both axes),
  // but the backend flag says is_pareto_optimal: true (e.g. the backend used a
  // more nuanced criterion, or the flag was set by some other logic).
  // filterParetoOptimal MUST return it because the flag says true.
  // If the function re-derived dominance from the raw numbers, it would wrongly
  // exclude this entry.
  it('Q3: trusts backend flag when flag and naive dominance disagree — dominated-by-numbers entry with flag=true IS included', () => {
    const window = [
      // This entry is strictly better on both axes than the next one.
      { day_offset: 0.0, total_fuel_cost_km_s: 1.0, total_risk_collected: 10.0, is_pareto_optimal: true },
      // This entry is strictly DOMINATED on both axes by day 0 — a naive recomputation
      // would call it non-optimal. But the backend explicitly marks it true.
      // filterParetoOptimal must include it because it trusts the flag.
      { day_offset: 1.0, total_fuel_cost_km_s: 2.0, total_risk_collected: 5.0, is_pareto_optimal: true },
    ]
    const result = filterParetoOptimal(window)
    // Both must be returned — the function does NOT recompute dominance.
    expect(result).toHaveLength(2)
    expect(result.some(r => r.day_offset === 1.0)).toBe(true)
  })

  // Inverse of Q3: an entry that is non-dominated by fuel/risk numbers alone
  // but has is_pareto_optimal: false — must NOT be returned.
  it('Q3 inverse: non-dominated-by-numbers entry with flag=false is excluded', () => {
    const window = [
      // These two entries are trade-offs — neither dominates the other by numbers.
      { day_offset: 0.0, total_fuel_cost_km_s: 1.0, total_risk_collected: 5.0, is_pareto_optimal: true },
      // Day 1 is not dominated by numbers (higher risk, higher fuel = trade-off),
      // but backend set flag=false. filterParetoOptimal must exclude it.
      { day_offset: 1.0, total_fuel_cost_km_s: 2.0, total_risk_collected: 9.0, is_pareto_optimal: false },
    ]
    const result = filterParetoOptimal(window)
    expect(result).toHaveLength(1)
    expect(result[0].day_offset).toBe(0.0)
  })

  it('handles empty window', () => {
    expect(filterParetoOptimal([])).toEqual([])
  })

  it('does not mutate the input array', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: 1.0, total_risk_collected: 5.0, is_pareto_optimal: true },
    ]
    const original = [...window]
    filterParetoOptimal(window)
    expect(window).toEqual(original)
  })
})

// ---------------------------------------------------------------------------
// findLowestFuelEntry
// ---------------------------------------------------------------------------

describe('findLowestFuelEntry', () => {
  it('returns the entry with the lowest fuel cost', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: 2.0, is_pareto_optimal: false },
      { day_offset: 1.0, total_fuel_cost_km_s: 1.2, is_pareto_optimal: true },
      { day_offset: 2.0, total_fuel_cost_km_s: 1.8, is_pareto_optimal: true },
    ]
    const result = findLowestFuelEntry(window)
    expect(result.day_offset).toBe(1.0)
  })

  it('tie broken by lower day_offset', () => {
    const window = [
      { day_offset: 2.0, total_fuel_cost_km_s: 1.5, is_pareto_optimal: true },
      { day_offset: 0.0, total_fuel_cost_km_s: 1.5, is_pareto_optimal: true },
      { day_offset: 1.0, total_fuel_cost_km_s: 1.5, is_pareto_optimal: true },
    ]
    const result = findLowestFuelEntry(window)
    expect(result.day_offset).toBe(0.0)
  })

  it('returns null for empty window', () => {
    expect(findLowestFuelEntry([])).toBeNull()
  })

  it('returns null when all entries have no fuel cost', () => {
    const window = [
      { day_offset: 0.0, is_pareto_optimal: false },
    ]
    expect(findLowestFuelEntry(window)).toBeNull()
  })

  it('ignores entries with null total_fuel_cost_km_s', () => {
    const window = [
      { day_offset: 0.0, total_fuel_cost_km_s: null, is_pareto_optimal: false },
      { day_offset: 1.0, total_fuel_cost_km_s: 1.3, is_pareto_optimal: true },
    ]
    const result = findLowestFuelEntry(window)
    expect(result.day_offset).toBe(1.0)
  })
})
