// CustomSelectionSummary — shows selected debris after finishing custom selection.
// "Compute Mission Cost" is intentionally a dead end (no backend call yet).
export default function CustomSelectionSummary({ debrisField, selectedIds, onClose }) {
  const selected = debrisField.filter((d) => selectedIds.has(d.norad_id))

  return (
    <div className="custom-selection-summary panel reticle">
      <div className="custom-selection-summary-header">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          Custom selection — {selected.length} object{selected.length !== 1 ? 's' : ''}
        </span>
        <button className="btn debris-modal-close" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {selected.length === 0 ? (
        <p className="history-summary" style={{ marginTop: 8 }}>No objects selected.</p>
      ) : (
        <div className="custom-selection-list-scroll">
          <ul className="custom-selection-list">
            {selected.map((d) => (
              <li key={d.norad_id} className="custom-selection-item">
                <span className="custom-selection-name">{d.name}</span>
                <span className="custom-selection-norad">{d.norad_id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        className="btn btn-primary"
        disabled
        title="Not wired yet."
        style={{ width: '100%', marginTop: 10 }}
      >
        Compute Mission Cost
      </button>
    </div>
  )
}
