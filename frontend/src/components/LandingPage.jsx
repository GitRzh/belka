import HeroGlobe from './HeroGlobe.jsx';

export default function LandingPage({ onEnter, debrisField }) {
  return (
    <div className="landing">
      <div className="landing-globe-bg">
        <HeroGlobe debrisField={debrisField} />
      </div>

      <div className="landing-scrim" aria-hidden="true" />
      <div className="landing-grain" aria-hidden="true" />

      <div className="landing-mark">
        <span className="landing-mark-icon">◈</span>
        <span>Orbital-Clean</span>
      </div>

      <div className="landing-content">
        <p className="landing-kicker">mission planning / space debris</p>
        <h1 className="landing-title">
          <span>ORBITAL</span>
          <span className="landing-title-accent">CLEAN</span>
        </h1>
        <p className="landing-tagline">
          An AI co-pilot that ranks orbital debris by risk, plans a fuel-optimal
          removal route, and explains its reasoning in plain language — then
          replans the moment your constraints change.
        </p>

        <div className="landing-features">
          <div className="landing-feature">
            <p className="landing-feature-label">risk-ranked targeting</p>
            <p className="landing-feature-body">Real Celestrak TLE data scored by proximity and orbital lifetime.</p>
          </div>
          <div className="landing-feature">
            <p className="landing-feature-label">optimized routing</p>
            <p className="landing-feature-body">Multi-target removal order under a real fuel budget, not a guess.</p>
          </div>
          <div className="landing-feature">
            <p className="landing-feature-label">live replanning</p>
            <p className="landing-feature-body">Type a new constraint. The route and reasoning update on the spot.</p>
          </div>
        </div>

        <button className="landing-cta" onClick={onEnter}>
          enter mission console →
        </button>
      </div>
    </div>
  );
}

