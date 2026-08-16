// ComparisonPanel — Feature 3: Trade-off Plan Comparator
//
// Shows the 3 fixed weight-preset results side by side:
//   - Stat cards (Fuel-Conservative / Balanced / Risk-Aggressive)
//   - Grouped bar chart (Recharts) with fuel cost + risk collected
//   - Comparison narration from the LLM
//   - "Use these weights" button on each card to populate PlanForm weights
//   - "RECOMMENDED" badge on the preset with the highest risk/fuel efficiency
//
// Color rule: grayscale, brightness-only — matching DataQualityBadge.jsx.
//   Brightness is rank-based (best efficiency = brightest), not fixed to label.
//   rank 0 (best)   → #f2f2f0 (brightest)
//   rank 1 (middle) → #8a8a8e (mid)
//   rank 2 (worst)  → #4a4a4e (dimmest)
// No hues introduced.

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell } from 'recharts'

// Rank-based brightness palette (index = rank, 0 = best).
const RANK_COLORS = ['#f2f2f0', '#8a8a8e', '#4a4a4e']

// Returns the preset with the highest risk/fuel efficiency
// (total_risk_collected / total_fuel_cost_km_s), or null if:
//   - all presets have total_fuel_cost_km_s <= 0, or
//   - two or more presets tie exactly on efficiency.
// Exported for direct unit testing.
export function findRecommendedPreset(presets) {
  const eligible = presets.filter(p => p.total_fuel_cost_km_s > 0)
  if (eligible.length === 0) return null

  const withEff = eligible.map(p => ({
    preset: p,
    efficiency: p.total_risk_collected / p.total_fuel_cost_km_s,
  }))

  const maxEff = Math.max(...withEff.map(e => e.efficiency))
  const winners = withEff.filter(e => e.efficiency === maxEff)

  // Tie → no recommendation
  if (winners.length !== 1) return null
  return winners[0].preset
}

// Ranks all presets by descending efficiency (risk/fuel).
// Presets with total_fuel_cost_km_s <= 0 sort to the bottom (-Infinity).
// Always returns all presets; ties preserve original array order (stable sort).
// Exported for direct unit testing.
export function rankPresetsByEfficiency(presets) {
  const withEff = presets.map(p => ({
    preset: p,
    efficiency: p.total_fuel_cost_km_s > 0
      ? p.total_risk_collected / p.total_fuel_cost_km_s
      : -Infinity,
  }))
  withEff.sort((a, b) => b.efficiency - a.efficiency)
  return withEff.map(e => e.preset)
}

// Chart data transformer: one row per preset, two metrics per row + rank color.
function buildChartData(presets, rankColorByLabel) {
  return presets.map((p) => ({
    name:             p.label,
    'Fuel (km/s)':    p.total_fuel_cost_km_s,
    'Risk collected': p.total_risk_collected,
    color:            rankColorByLabel[p.label] ?? 'var(--c-steel)',
  }))
}

// Custom dot-style tooltip so we stay in the grayscale theme.
function GrayTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: 'var(--c-panel)',
      border: '1px solid var(--c-line)',
      padding: '6px 10px',
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
    }}>
      <div style={{ marginBottom: 4, color: 'var(--c-ink)', fontWeight: 600 }}>{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} style={{ color: 'var(--c-steel)' }}>
          {entry.name}: <span style={{ color: 'var(--c-ink)' }}>{Number(entry.value).toFixed(4)}</span>
        </div>
      ))}
    </div>
  )
}

export default function ComparisonPanel({ result, onUsePlan, onClose }) {
  if (!result) return null

  const { presets, comparison_narration } = result
  const recommendedPreset = findRecommendedPreset(presets)

  // Build rank-color lookup: { [presetLabel]: colorHex }
  const ranked = rankPresetsByEfficiency(presets)
  const rankColorByLabel = Object.fromEntries(
    ranked.map((p, i) => [p.label, RANK_COLORS[i] ?? 'var(--c-steel)'])
  )

  const chartData = buildChartData(presets, rankColorByLabel)

  return (
    <div className="reasoning" style={{ paddingBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>Preset Comparison</h3>
        {onClose && (
          <button
            className="btn"
            style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={onClose}
          >
            Close
          </button>
        )}
      </div>

      {/* ── Stat cards ──────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {presets.map((preset) => {
          const color = rankColorByLabel[preset.label] ?? 'var(--c-steel)'
          return (
            <div
              key={preset.label}
              style={{
                flex: 1,
                border: `1px solid ${color}`,
                borderRadius: 'var(--radius)',
                padding: '10px 10px 8px',
                background: 'var(--c-panel)',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                position: 'relative',
              }}
            >
              {recommendedPreset?.label === preset.label && (
                <span
                  data-testid={`recommended-badge-${preset.label}`}
                  style={{
                    position: 'absolute',
                    top: 6,
                    right: 6,
                    fontFamily: 'var(--font-display)',
                    fontSize: 9,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    background: 'var(--c-ink)',
                    color: 'var(--c-panel)',
                    padding: '1px 4px',
                    borderRadius: 2,
                  }}
                >
                  Recommended
                </span>
              )}
              <div style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 600,
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: color,
                marginBottom: 4,
              }}>
                {preset.label}
              </div>

              <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 8px' }}>
                <dt style={{ color: 'var(--c-steel)', fontSize: 11, whiteSpace: 'nowrap' }}>Fuel (km/s)</dt>
                <dd style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--c-ink)' }}>
                  {preset.total_fuel_cost_km_s}
                </dd>
                <dt style={{ color: 'var(--c-steel)', fontSize: 11, whiteSpace: 'nowrap' }}>Risk</dt>
                <dd style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--c-ink)' }}>
                  {preset.total_risk_collected}
                </dd>
                <dt style={{ color: 'var(--c-steel)', fontSize: 11, whiteSpace: 'nowrap' }}>Visited</dt>
                <dd style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--c-ink)' }}>
                  {preset.visited_count}
                </dd>
              </dl>

              <button
                className="btn"
                style={{
                  marginTop: 6,
                  fontSize: 11,
                  padding: '4px 8px',
                  width: '100%',
                  borderColor: color,
                  color: color,
                }}
                onClick={() => onUsePlan?.(preset)}
              >
                Use these weights
              </button>
            </div>
          )
        })}
      </div>

      {/* ── Grouped bar chart ────────────────────────────────────── */}
      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontFamily: 'var(--font-display)',
          fontSize: 11,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--c-steel)',
          marginBottom: 6,
        }}>
          Fuel vs Risk by preset
        </div>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--c-line)" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fill: 'var(--c-steel)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
              axisLine={{ stroke: 'var(--c-line)' }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: 'var(--c-steel)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip content={<GrayTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--c-steel)' }}
            />
            {/* Per-preset rank color: Fuel bar solid, Risk bar at 0.55 opacity.
                Bar-level fill provides the Legend swatch; Cell overrides per-point color. */}
            <Bar dataKey="Fuel (km/s)" fill={RANK_COLORS[0]} radius={[1, 1, 0, 0]}>
              {chartData.map((entry) => (
                <Cell key={entry.name} fill={entry.color} fillOpacity={1} />
              ))}
            </Bar>
            <Bar dataKey="Risk collected" fill={RANK_COLORS[0]} radius={[1, 1, 0, 0]}>
              {chartData.map((entry) => (
                <Cell key={entry.name} fill={entry.color} fillOpacity={0.55} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* ── Comparison narration ─────────────────────────────────── */}
      {comparison_narration ? (
        <p className="explanation" style={{ margin: 0 }}>{comparison_narration}</p>
      ) : (
        <p className="explanation" style={{ margin: 0, fontStyle: 'italic' }}>
          Comparison narration unavailable.
        </p>
      )}
    </div>
  )
}
