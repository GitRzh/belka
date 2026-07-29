import { useEffect, useState } from 'react'

// Real UTC clock, not a decorative animation — mirrors the mission-console
// framing without inventing fake telemetry.
export default function MissionClock() {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const time = now.toISOString().slice(11, 19) // HH:MM:SS

  return <span className="mission-clock">{time} UTC</span>
}
