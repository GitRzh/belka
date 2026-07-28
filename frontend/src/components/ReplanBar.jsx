import { useState } from 'react';

export default function ReplanBar({ onReplan, onGeneratePlan, onToggleNaive, showNaive, busy }) {
  const [text, setText] = useState('');

  function submit() {
    if (!text.trim() || busy) return;
    onReplan(text.trim());
    setText('');
  }

  return (
    <div className="replan-bar">
      <button className="btn" onClick={onGeneratePlan} disabled={busy}>
        generate plan
      </button>
      <input
        type="text"
        placeholder="prioritize risk over fuel"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
        disabled={busy}
      />
      <button className="btn" onClick={submit} disabled={busy}>
        replan
      </button>
      <button className={`btn ${showNaive ? 'btn-active' : ''}`} onClick={onToggleNaive} disabled={busy}>
        naive vs AI
      </button>
    </div>
  );
}
