import { useState } from 'react'

export default function ReplanInput({ onReplan, submitting, disabled, replanResult }) {
  const [text, setText] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    if (!text.trim()) return
    onReplan(text.trim())
  }

  const diff = replanResult?.diff
  const overrides = replanResult?.overrides_applied
  const explanation = replanResult?.explanation

  return (
    <div>
      <form onSubmit={handleSubmit}>
        <label>
          Adjust the plan
          <input
            type="text"
            placeholder='e.g. "prioritize risk over fuel"'
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={disabled}
          />
        </label>
        <button type="submit" disabled={disabled || submitting}>
          {submitting ? 'Replanning…' : 'Replan'}
        </button>
      </form>

      {replanResult && (
        <div style={{ marginTop: 12, fontSize: 13 }}>
          {explanation && <p style={{ marginBottom: 6 }}>{explanation}</p>}

          {overrides && Object.keys(overrides).length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <strong>Overrides applied:</strong>{' '}
              {Object.entries(overrides)
                .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
                .join(', ')}
            </div>
          )}

          {diff && (
            <dl style={{ margin: 0 }}>
              {diff.added?.length > 0 && (
                <><dt>Added stops</dt><dd>{diff.added.join(', ')}</dd></>
              )}
              {diff.dropped?.length > 0 && (
                <><dt>Dropped stops</dt><dd>{diff.dropped.join(', ')}</dd></>
              )}
              <dt>Fuel Δ</dt>
              <dd>{diff.fuel_delta_km_s > 0 ? '+' : ''}{diff.fuel_delta_km_s} km/s</dd>
              <dt>Risk Δ</dt>
              <dd>{diff.risk_delta > 0 ? '+' : ''}{diff.risk_delta}</dd>
            </dl>
          )}
        </div>
      )}
    </div>
  )
}
