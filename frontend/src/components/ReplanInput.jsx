import { useState } from 'react'

// Used as a draft replan card in App.jsx.
// Props:
//   baseEntry  — the history entry being branched from (passed for context, not rendered here)
//   onReplan(text) — called with the trimmed request text
//   onCancel       — called when the user cancels
//   submitting     — bool, disables inputs while API call is in flight
export default function ReplanInput({ onReplan, onCancel, submitting }) {
  const [text, setText] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    if (!text.trim()) return
    onReplan(text.trim())
  }

  return (
    <div className="replan">
      <form onSubmit={handleSubmit}>
        <label className="field">
          Describe your change
          <textarea
            placeholder='e.g. "prioritize risk over fuel"'
            value={text}
            rows={3}
            onChange={(e) => setText(e.target.value)}
            disabled={submitting}
          />
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="submit" className="btn btn-primary" disabled={submitting} style={{ flex: 1 }}>
            {submitting ? 'Applying…' : 'Apply changes'}
          </button>
          {onCancel && (
            <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
