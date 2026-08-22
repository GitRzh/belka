// LaunchWindowPanel — Feature 4: Launch-Window Pareto Explorer
//
// Renders the result of POST /sweep-launch-window.
// Branches on sweep_mode:
//   "pareto_frontier" → ScatterChart (x = fuel cost, y = risk collected).
//     Frontier points visually distinguished from dominated ones.
//     lowest_fuel_date annotated, not highlighted as "the answer."
//   "single_axis"     → BarChart by date (fuel cost only).
//     No scatter, no implied two-axis trade-off.
//
// Clicking a point/date calls onSelectDate(launch_date_string) —
// populates PlanForm's launch_date field ONLY.  No auto-submit.
//
// Color rule: grayscale, brightness-only — same as ComparisonPanel.
//   Pareto-optimal points: #f2f2f0 (brightest)
//   Dominated / non-optimal points: #4a4a4e (dimmest)
//   lowest_fuel_date annotation: dashed border, no fill change

import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
  BarChart, Bar,
} from 'recharts'

function formatDisplayDate(launchDate) {
  if (!launchDate) return launchDate
  const isoMatch = launchDate.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):\d{2}Z$/)
  if (isoMatch) {
    const [, datePart, hh, mm] = isoMatch
    return `${datePart} ${hh}:${mm} UTC`
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(launchDate)) {
    return `${launchDate} 00:00 UTC`
  }
  return launchDate  // unexpected shape — fail safe, don't crash
}

const COLOR_OPTIMAL   = '#f2f2f0'
const COLOR_DOMINATED = '#4a4a4e'
const COLOR_BEST_FUEL = '#8a8a8e'  // mid — lowest_fuel annotation bar

// Exported for unit testing — mirrors rankPresetsByEfficiency shape.
// Returns the subset of window entries that are Pareto-optimal.
export function filterParetoOptimal(window) {
  return window.filter(r => r.is_pareto_optimal)
}

// Exported for unit testing.
// Returns true if every valid entry in the window has the same total_risk_collected.
// "Valid" means no error key and total_risk_collected is not null.
// Used purely for display: when true, the pareto_frontier scatter needs an
// explanatory note that the flat risk axis isn't a rendering bug.
// Does NOT affect sweep_mode, compute_pareto_frontier, or is_pareto_optimal.
export function hasConstantRisk(window) {
  const risks = window
    .filter(r => !r.error && r.total_risk_collected != null)
    .map(r => r.total_risk_collected)
  if (risks.length === 0) return false
  return risks.every(v => v === risks[0])
}

// Exported for unit testing.
// Returns the entry with lowest total_fuel_cost_km_s (tie → lower day_offset).
export function findLowestFuelEntry(window) {
  const valid = window.filter(r => r.total_fuel_cost_km_s != null)
  if (!valid.length) return null
  return valid.reduce((best, r) => {
    if (r.total_fuel_cost_km_s < best.total_fuel_cost_km_s) return r
    if (r.total_fuel_cost_km_s === best.total_fuel_cost_km_s && r.day_offset < best.day_offset) return r
    return best
  })
}

function GrayTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload ?? {}
  return (
    <div style={{
      background: 'var(--c-panel)',
      border: '1px solid var(--c-line)',
      padding: '6px 10px',
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
    }}>
      <div style={{ marginBottom: 3, color: 'var(--c-ink)', fontWeight: 600 }}>
        {d.launch_date ? formatDisplayDate(d.launch_date) : `Day +${d.day_offset}`}
      </div>
      {d.total_fuel_cost_km_s != null && (
        <div style={{ color: 'var(--c-steel)' }}>
          Fuel: <span style={{ color: 'var(--c-ink)' }}>{Number(d.total_fuel_cost_km_s).toFixed(4)} km/s</span>
        </div>
      )}
      {d.total_risk_collected != null && (
        <div style={{ color: 'var(--c-steel)' }}>
          Risk: <span style={{ color: 'var(--c-ink)' }}>{Number(d.total_risk_collected).toFixed(4)}</span>
        </div>
      )}
      {d.visited_count != null && (
        <div style={{ color: 'var(--c-steel)' }}>
          Visited: <span style={{ color: 'var(--c-ink)' }}>{d.visited_count}</span>
        </div>
      )}
      {d.is_pareto_optimal && (
        <div style={{ marginTop: 3, color: COLOR_OPTIMAL, fontWeight: 600 }}>Pareto-optimal</div>
      )}
      {d.data_quality && (
        <div style={{ color: 'var(--c-steel)' }}>
          Data: <span style={{ color: 'var(--c-ink)' }}>{d.data_quality}</span>
        </div>
      )}
    </div>
  )
}

// Scatter chart for pareto_frontier mode.
function ParetoScatter({ window, lowestFuelDate, onSelectDate }) {
  const validPoints = window.filter(r => r.total_fuel_cost_km_s != null && r.total_risk_collected != null)
  const flatRisk = hasConstantRisk(window)

  return (
    <div>
      <div style={{
        fontFamily: 'var(--font-display)',
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: 'var(--c-steel)',
        marginBottom: 6,
      }}>
        Fuel cost vs Risk collected — Pareto frontier
      </div>
      <div style={{ fontSize: 10, color: 'var(--c-steel)', marginBottom: 8, fontFamily: 'var(--font-mono)' }}>
        <span style={{ color: COLOR_OPTIMAL }}>●</span> Pareto-optimal&nbsp;&nbsp;
        <span style={{ color: COLOR_DOMINATED }}>●</span> Dominated&nbsp;&nbsp;
        Click any point to set launch date
      </div>
      {flatRisk && (
        <div
          data-testid="flat-risk-note"
          style={{
            fontSize: 11,
            color: 'var(--c-steel)',
            background: 'var(--c-panel)',
            border: '1px solid var(--c-line)',
            borderRadius: 'var(--radius)',
            padding: '6px 10px',
            marginBottom: 8,
            fontFamily: 'var(--font-mono)',
          }}
        >
          Risk collected didn't vary across these dates — the same debris set was reachable
          regardless of launch date, so fuel cost alone determines the best choice here.
        </div>
      )}
      <ResponsiveContainer width="100%" height={200}>
        <ScatterChart margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--c-line)" />
          <XAxis
            dataKey="total_fuel_cost_km_s"
            name="Fuel (km/s)"
            type="number"
            domain={['auto', 'auto']}
            tick={{ fill: 'var(--c-steel)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
            axisLine={{ stroke: 'var(--c-line)' }}
            tickLine={false}
            label={{ value: 'Fuel (km/s)', position: 'insideBottom', offset: -2, fill: 'var(--c-steel)', fontSize: 10 }}
          />
          <YAxis
            dataKey="total_risk_collected"
            name="Risk collected"
            type="number"
            domain={['auto', 'auto']}
            tick={{ fill: 'var(--c-steel)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            width={44}
            label={{ value: 'Risk', angle: -90, position: 'insideLeft', fill: 'var(--c-steel)', fontSize: 10 }}
          />
          <Tooltip content={<GrayTooltip />} />
          <Scatter
            data={validPoints}
            onClick={(point) => onSelectDate?.(point.launch_date)}
            cursor="pointer"
          >
            {validPoints.map((entry, idx) => {
              const isLowest = lowestFuelDate && entry.launch_date === lowestFuelDate.launch_date
              return (
                <Cell
                  key={idx}
                  fill={entry.is_pareto_optimal ? COLOR_OPTIMAL : COLOR_DOMINATED}
                  fillOpacity={entry.is_pareto_optimal ? 1.0 : 0.5}
                  stroke={isLowest ? 'var(--c-ink)' : 'none'}
                  strokeWidth={isLowest ? 1.5 : 0}
                  strokeDasharray={isLowest ? '3 2' : undefined}
                  r={entry.is_pareto_optimal ? 5 : 4}
                />
              )
            })}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
      {lowestFuelDate && (
        <div style={{ fontSize: 10, color: 'var(--c-steel)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
          <span style={{ border: '1px dashed var(--c-ink)', padding: '0 3px', marginRight: 4 }}>dashed ring</span>
          = lowest-fuel date ({formatDisplayDate(lowestFuelDate.launch_date)}) — one reference point, not the only valid choice
        </div>
      )}
    </div>
  )
}

// Bar chart for single_axis mode.
function SingleAxisBar({ window, lowestFuelDate, onSelectDate }) {
  const validPoints = window
    .filter(r => r.total_fuel_cost_km_s != null)
    .map(r => ({
      ...r,
      label: r.launch_date?.slice(0, 10) ?? `+${r.day_offset}d`,
    }))

  return (
    <div>
      <div style={{
        fontFamily: 'var(--font-display)',
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: 'var(--c-steel)',
        marginBottom: 6,
      }}>
        Fuel cost by launch date — Fixed target list
      </div>
      <div style={{ fontSize: 10, color: 'var(--c-steel)', marginBottom: 8, fontFamily: 'var(--font-mono)' }}>
        Target list fixed — risk is constant. Click a bar to set launch date.
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={validPoints} margin={{ top: 4, right: 8, left: 0, bottom: 24 }} onClick={(e) => {
          if (e?.activePayload?.[0]?.payload?.launch_date) {
            onSelectDate?.(e.activePayload[0].payload.launch_date)
          }
        }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--c-line)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: 'var(--c-steel)', fontSize: 9, fontFamily: 'var(--font-mono)' }}
            axisLine={{ stroke: 'var(--c-line)' }}
            tickLine={false}
            angle={-35}
            textAnchor="end"
            interval={0}
          />
          <YAxis
            tick={{ fill: 'var(--c-steel)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            width={44}
          />
          <Tooltip content={<GrayTooltip />} />
          <Bar dataKey="total_fuel_cost_km_s" radius={[1, 1, 0, 0]} cursor="pointer">
            {validPoints.map((entry, idx) => {
              const isLowest = lowestFuelDate && entry.launch_date === lowestFuelDate.launch_date
              return (
                <Cell
                  key={idx}
                  fill={isLowest ? COLOR_OPTIMAL : COLOR_DOMINATED}
                  fillOpacity={isLowest ? 1.0 : 0.6}
                />
              )
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {lowestFuelDate && (
        <div style={{ fontSize: 10, color: 'var(--c-steel)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
          <span style={{ color: COLOR_OPTIMAL }}>■</span> = lowest-fuel date ({formatDisplayDate(lowestFuelDate.launch_date)})
        </div>
      )}
    </div>
  )
}

export default function LaunchWindowPanel({ result, onSelectDate, onClose }) {
  if (!result) return null

  const { sweep_mode, window: windowData, lowest_fuel_date, narration } = result
  const validWindow = (windowData ?? []).filter(r => !r.error)

  return (
    <div className="reasoning" style={{ paddingBottom: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>Launch Window Explorer</h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            color: 'var(--c-steel)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}>
            {sweep_mode === 'single_axis' ? 'single-axis' : 'pareto frontier'}
          </span>
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
      </div>

      {/* Chart */}
      <div style={{ marginBottom: 16 }}>
        {sweep_mode === 'pareto_frontier' ? (
          <ParetoScatter
            window={validWindow}
            lowestFuelDate={lowest_fuel_date}
            onSelectDate={onSelectDate}
          />
        ) : (
          <SingleAxisBar
            window={validWindow}
            lowestFuelDate={lowest_fuel_date}
            onSelectDate={onSelectDate}
          />
        )}
      </div>

      {/* Narration */}
      {narration ? (
        <p className="explanation" style={{ margin: 0 }}>{narration}</p>
      ) : (
        <p className="explanation" style={{ margin: 0, fontStyle: 'italic' }}>
          Narration unavailable.
        </p>
      )}
    </div>
  )
}
