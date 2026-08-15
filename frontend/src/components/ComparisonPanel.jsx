// ComparisonPanel — Feature 3: Trade-off Plan Comparator
//
// Shows the 3 fixed weight-preset results side by side:
//   - Stat cards (Fuel-Conservative / Balanced / Risk-Aggressive)
//   - Grouped bar chart (Recharts) with fuel cost + risk collected
//   - Comparison narration from the LLM
//   - "Use this plan" button on each card to commit to History
//
// Color rule: grayscale, brightness-only — matching DataQualityBadge.jsx.
//   Fuel-Conservative = dim/dark gray (#4a4a4e)
//   Balanced          = mid gray      (#8a8a8e)
//   Risk-Aggressive   = bright/white  (#f2f2f0)
// No hues introduced.

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

// Per-preset grayscale brightness values, consistent across card + chart bars.
const PRESET_STYLE = {
  'Fuel-Conservative': { color: '#4a4a4e', label: 'Fuel-Conservative' },
  'Balanced':          { color: '#8a8a8e', label: 'Balanced' },
  'Risk-Aggressive':   { color: '#f2f2f0', label: 'Risk-Aggressive' },
}

// Chart data transformer: one row per preset, two metrics per row.
function buildChartData(presets) {
  return presets.map((p) => ({
    name:            p.label,
    'Fuel (km/s)':   p.total_fuel_cost_km_s,
    'Risk collected': p.total_risk_collected,
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
  const chartData = buildChartData(presets)

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
          const style = PRESET_STYLE[preset.label] ?? { color: 'var(--c-steel)' }
          return (
            <div
              key={preset.label}
              style={{
                flex: 1,
                border: `1px solid ${style.color}`,
                borderRadius: 'var(--radius)',
                padding: '10px 10px 8px',
                background: 'var(--c-panel)',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
              }}
            >
              <div style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 600,
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: style.color,
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
                  borderColor: style.color,
                  color: style.color,
                }}
                onClick={() => onUsePlan?.(preset)}
              >
                Use this plan
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
            {/* Fuel bar: dark gray fill for each preset's bar,
                but we use two separate bars per metric so both are monochrome. */}
            <Bar dataKey="Fuel (km/s)"    fill="#4a4a4e" radius={[1, 1, 0, 0]} />
            <Bar dataKey="Risk collected" fill="#8a8a8e" radius={[1, 1, 0, 0]} />
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
