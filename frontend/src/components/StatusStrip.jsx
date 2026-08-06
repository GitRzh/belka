// StatusStrip — one-line mission state summary below the app header.
// Updates reactively whenever routeMode, focusMode, activePlan, or
// cacheMetadata change in App.jsx.

const FOCUS_LABELS = {
  all:   'all debris visible',
  dim:   'route highlighted',
  focus: 'route only',
}

const ROUTE_LABELS = {
  ai:    'AI-optimised',
  naive: 'nearest-neighbour',
}

export default function StatusStrip({ routeMode, focusMode, activePlan, cacheMetadata }) {
  // L3: show actual cache age in minutes instead of a binary "current / refreshing soon"
  // label that says "data current" right up until the last 10 minutes of a 2-hour window.
  let dataLabel = 'no data'
  if (cacheMetadata) {
    if (cacheMetadata.data_fetched_at) {
      const ageMin = Math.floor((Date.now() - new Date(cacheMetadata.data_fetched_at).getTime()) / 60000)
      const ageSuffix = cacheMetadata.data_stale ? ' — refreshing soon' : ''
      dataLabel = `data ${ageMin} min old${ageSuffix}`
    } else {
      dataLabel = cacheMetadata.data_stale ? 'data refreshing soon' : 'data current'
    }
  }

  // Build the strip segments conditionally so absent state doesn't leave
  // dangling separators.
  const segments = []

  if (activePlan) {
    segments.push(`${ROUTE_LABELS[routeMode] ?? routeMode} route`)
    segments.push(`${FOCUS_LABELS[focusMode] ?? focusMode}`)
    segments.push(`${activePlan.visited_count} of ${activePlan.pool_size_used} targets visited`)
  } else {
    segments.push('no active route')
  }

  segments.push(dataLabel)

  return (
    <div className={`status-strip${cacheMetadata?.data_stale ? ' status-strip--stale' : ''}`}>
      {segments.map((seg, i) => (
        <span key={i}>
          {i > 0 && <span className="status-strip-sep" aria-hidden="true">·</span>}
          {seg}
        </span>
      ))}
    </div>
  )
}
