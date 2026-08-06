import { useState } from 'react'

export default function ReplanInput({ onReplan, submitting, disabled }) {
  const [text, setText] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    if (!text.trim()) return
    onReplan(text.trim())
  }

  return (
    <div className="replan">
      <h3>Modify plan</h3>
      <form onSubmit={handleSubmit}>
        <label className="field">
          Describe your change
          <input
            type="text"
            placeholder='e.g. "prioritize risk over fuel"'
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={disabled}
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={disabled || submitting}>
          {submitting ? 'Applying…' : 'Apply changes'}
        </button>
      </form>
    </div>
  )
}
