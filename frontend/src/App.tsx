import {
  Activity,
  CalendarClock,
  CheckCircle2,
  Eye,
  KeyRound,
  Play,
  RadioTower,
  Server,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import { useMemo, useState } from "react";

type Host = {
  name: string;
  address: string;
  user: string;
  ready: boolean;
  schedule: string;
};

type WarpKey = {
  id: string;
  masked: string;
  value: string;
  status: "valid" | "invalid";
  source: string;
  attempts: number;
};

const hosts: Host[] = [
  { name: "edge-mow-01", address: "203.0.113.18", user: "root", ready: true, schedule: "04:00 daily" },
  { name: "edge-ams-02", address: "198.51.100.42", user: "ubuntu", ready: false, schedule: "Every 6h" },
  { name: "edge-fra-03", address: "192.0.2.77", user: "debian", ready: true, schedule: "2026-06-01 10:30" },
];

const warpKeys: WarpKey[] = [
  {
    id: "wk_01",
    masked: "•••• •••• •••• 9abc",
    value: "12345678-1234-1234-1234-123456789abc",
    status: "valid",
    source: "@owned_warp_pool",
    attempts: 3,
  },
  {
    id: "wk_02",
    masked: "•••• •••• •••• 77fa",
    value: "de305d54-75b4-431b-adb2-eb6b9e54677fa",
    status: "valid",
    source: "@infra_licenses",
    attempts: 1,
  },
  {
    id: "wk_03",
    masked: "•••• •••• •••• 0bad",
    value: "00000000-0000-0000-0000-000000000bad",
    status: "invalid",
    source: "@owned_warp_pool",
    attempts: 1,
  },
];

const jobs = [
  { host: "edge-mow-01", state: "license_applied", detail: "WARP+ active, key retained in pool", tone: "good" },
  { host: "edge-ams-02", state: "license_failed", detail: "Key invalidated after license step", tone: "bad" },
  { host: "edge-fra-03", state: "ssh_failed", detail: "Pool untouched, connection retry needed", tone: "warn" },
];

export function App() {
  const [active, setActive] = useState("overview");
  const [revealedKeys, setRevealedKeys] = useState<Set<string>>(new Set());

  const stats = useMemo(
    () => ({
      readyHosts: hosts.filter((host) => host.ready).length,
      validKeys: warpKeys.filter((key) => key.status === "valid").length,
      invalidKeys: warpKeys.filter((key) => key.status === "invalid").length,
    }),
    [],
  );

  function revealKey(id: string) {
    setRevealedKeys((current) => new Set(current).add(id));
  }

  return (
    <main className="shell">
      <aside className="rail" aria-label="Primary navigation">
        <div className="brandMark">
          <ShieldCheck size={24} />
        </div>
        {[
          ["overview", Activity],
          ["hosts", Server],
          ["telegram", RadioTower],
          ["keys", KeyRound],
          ["jobs", TerminalSquare],
        ].map(([id, Icon]) => (
          <button
            className={active === id ? "railButton active" : "railButton"}
            key={id as string}
            onClick={() => setActive(id as string)}
            title={id as string}
            aria-label={id as string}
          >
            <Icon size={20} />
          </button>
        ))}
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">authorized telegram to ssh rotation</p>
            <h1>WARP+ Orchestrator</h1>
          </div>
          <button className="primaryAction">
            <Play size={17} />
            Run host update
          </button>
        </header>

        <section className="metricGrid" aria-label="System metrics">
          <Metric icon={<Server />} label="Hosts ready" value={`${stats.readyHosts}/${hosts.length}`} accent="green" />
          <Metric icon={<KeyRound />} label="Valid keys" value={stats.validKeys.toString()} accent="yellow" />
          <Metric icon={<Activity />} label="Invalidated" value={stats.invalidKeys.toString()} accent="red" />
          <Metric icon={<CalendarClock />} label="Next run" value="04:00 MSK" accent="blue" />
        </section>

        <section className="layout">
          <div className="panel large">
            <div className="panelHeader">
              <h2>Hosts</h2>
              <button className="ghostButton">Add host</button>
            </div>
            <div className="hostTable">
              {hosts.map((host) => (
                <div className="hostRow" key={host.name}>
                  <div className="statusDot" data-ready={host.ready} />
                  <div>
                    <strong>{host.name}</strong>
                    <span>
                      {host.user}@{host.address}
                    </span>
                  </div>
                  <span className="schedule">{host.schedule}</span>
                  <span className={host.ready ? "pill good" : "pill warn"}>{host.ready ? "ready" : "pending"}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panelHeader">
              <h2>Telegram sources</h2>
              <button className="iconButton" title="Sync sources" aria-label="Sync sources">
                <RadioTower size={18} />
              </button>
            </div>
            <SourceRow name="@owned_warp_pool" mode="Bot API" regex="uuid + custom" count="18 keys" />
            <SourceRow name="@infra_licenses" mode="User session" regex="default regex" count="7 keys" />
          </div>

          <div className="panel large">
            <div className="panelHeader">
              <h2>WARP keys</h2>
              <button className="ghostButton">Import key</button>
            </div>
            <div className="keyList">
              {warpKeys.map((key) => (
                <div className="keyRow" key={key.id}>
                  <KeyRound size={18} />
                  <code>{revealedKeys.has(key.id) ? key.value : key.masked}</code>
                  <span className={key.status === "valid" ? "pill good" : "pill bad"}>{key.status}</span>
                  <span className="muted">{key.source}</span>
                  <button className="iconButton" onClick={() => revealKey(key.id)} aria-label={`Reveal WARP key ${key.value.slice(-4)}`}>
                    <Eye size={17} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panelHeader">
              <h2>Job stream</h2>
              <span className="live">live</span>
            </div>
            <div className="jobList">
              {jobs.map((job) => (
                <div className="jobRow" key={`${job.host}-${job.state}`}>
                  <span className={`jobLight ${job.tone}`} />
                  <div>
                    <strong>{job.host}</strong>
                    <span>{job.detail}</span>
                  </div>
                  <code>{job.state}</code>
                </div>
              ))}
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: string; accent: string }) {
  return (
    <div className="metric" data-accent={accent}>
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SourceRow({ name, mode, regex, count }: { name: string; mode: string; regex: string; count: string }) {
  return (
    <div className="sourceRow">
      <div>
        <strong>{name}</strong>
        <span>{mode}</span>
      </div>
      <code>{regex}</code>
      <span className="muted">{count}</span>
    </div>
  );
}
