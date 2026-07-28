import { useEffect, useState } from 'react';
import LandingPage from './components/LandingPage.jsx';
import GlobeView from './components/GlobeView.jsx';
import ManifestPanel from './components/ManifestPanel.jsx';
import ReasoningPanel from './components/ReasoningPanel.jsx';
import ReplanBar from './components/ReplanBar.jsx';
import { fetchDebrisField, fetchPlan, fetchNaiveRoute, fetchReplan } from './api.js';

const LAUNCH_SITES = {
  kourou: { label: 'Kourou (5.2°N)', lat: 5.2, lon: -52.8, altitude_km: 400, inclination_deg: 5.2 },
  cape_canaveral: { label: 'Cape Canaveral', lat: 28.4, lon: -80.6, altitude_km: 400, inclination_deg: 28.4 },
  vandenberg: { label: 'Vandenberg', lat: 34.7, lon: -120.6, altitude_km: 400, inclination_deg: 97.5 },
};

export default function App() {
  const [view, setView] = useState('landing'); // 'landing' | 'console'
  const [debrisField, setDebrisField] = useState([]);
  const [siteKey, setSiteKey] = useState('kourou');
  const [plan, setPlan] = useState(null);
  const [naiveRoute, setNaiveRoute] = useState(null);
  const [showNaive, setShowNaive] = useState(false);
  const [selectedNoradId, setSelectedNoradId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [usingMock, setUsingMock] = useState(false);

  useEffect(() => {
    fetchDebrisField().then((field) => {
      setDebrisField(field);
      generatePlan(field);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function planParams(field = debrisField) {
    const site = LAUNCH_SITES[siteKey];
    return {
      start_altitude_km: site.altitude_km,
      start_inclination_deg: site.inclination_deg,
      fuel_budget_km_s: 3.0,
    };
  }

  async function generatePlan(field = debrisField) {
    setBusy(true);
    const [p, n] = await Promise.all([fetchPlan(planParams(field)), fetchNaiveRoute()]);
    setPlan(p);
    setNaiveRoute(n);
    setBusy(false);
  }

  async function handleReplan(text) {
    if (!plan) return;
    setBusy(true);
    const result = await fetchReplan(plan, text, planParams());
    setPlan(result.new_plan);
    setBusy(false);
  }

  const site = LAUNCH_SITES[siteKey];

  if (view === 'landing') {
    return <LandingPage onEnter={() => setView('console')} debrisField={debrisField} />;
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">
          <span className="app-title-mark">◈</span>
          <span>orbital-clean / mission console</span>
        </div>
        <select value={siteKey} onChange={(e) => { setSiteKey(e.target.value); }}>
          {Object.entries(LAUNCH_SITES).map(([key, s]) => (
            <option key={key} value={key}>launch site: {s.label}</option>
          ))}
        </select>
      </header>

      <div className="app-grid">
        <ManifestPanel
          debrisField={debrisField}
          plan={showNaive ? naiveRoute : plan}
          selectedNoradId={selectedNoradId}
          onSelectDebris={setSelectedNoradId}
        />

        <div className="globe-panel">
          <GlobeView
            debrisField={debrisField}
            plan={plan}
            naiveRoute={naiveRoute}
            showNaive={showNaive}
            selectedNoradId={selectedNoradId}
            onSelectDebris={setSelectedNoradId}
            launchSite={{ lat: site.lat, lon: site.lon }}
          />
          <div className="globe-legend">
            <span><i className="dot" style={{ background: '#4dd8e6' }} /> launch site</span>
            <span><i className="dot" style={{ background: showNaive ? '#8a94a6' : '#1D9E75' }} /> route ({showNaive ? 'naive' : 'optimized'})</span>
            <span><i className="dot" style={{ background: '#BA7517' }} /> high risk</span>
          </div>
        </div>

        <ReasoningPanel plan={showNaive ? naiveRoute : plan} loading={busy} showNaive={showNaive} />
      </div>

      <ReplanBar
        onReplan={handleReplan}
        onGeneratePlan={() => generatePlan()}
        onToggleNaive={() => setShowNaive((v) => !v)}
        showNaive={showNaive}
        busy={busy}
      />
    </div>
  );
}
