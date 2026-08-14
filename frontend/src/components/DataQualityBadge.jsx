// Shared badge for the data_quality field ("fresh" | "aging" | "stale" | "unknown").
// Grayscale only — brightness-encoded, no hue — matching the .warning bordered-box
// precedent in global.css. Used in DebrisInfoModal and ReasoningPanel.

const STYLES = {
  fresh: { borderColor: 'var(--c-ink)',   color: 'var(--c-ink)' },
  aging: { borderColor: 'var(--c-steel)', color: 'var(--c-steel)' },
  stale: { borderColor: 'var(--c-line)',  color: 'var(--c-steel)' },
}

const BASE_STYLE = {
  display: 'inline-block',
  fontFamily: 'var(--font-mono)',
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  border: '1px solid',
  borderRadius: 'var(--radius)',
  padding: '1px 5px',
  lineHeight: 1.4,
}

export default function DataQualityBadge({ value }) {
  if (value === null || value === undefined || value === '') return null
  const variant = STYLES[value] ?? { borderColor: 'var(--c-line)', color: 'var(--c-steel)' }
  return (
    <span style={{ ...BASE_STYLE, ...variant }}>
      {value}
    </span>
  )
}
