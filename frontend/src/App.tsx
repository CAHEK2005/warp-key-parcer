import {
  Activity,
  CalendarClock,
  CheckCircle2,
  Eye,
  KeyRound,
  LogOut,
  Play,
  Plus,
  RadioTower,
  RefreshCcw,
  Server,
  ShieldCheck,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";

import { apiFetch } from "./api";

type View = "overview" | "hosts" | "ssh" | "telegram" | "keys" | "schedules" | "jobs";

type SshKey = {
  id: number;
  name: string;
  fingerprint: string;
  tail: string;
  private_key: string | null;
};

type Host = {
  id: number;
  name: string;
  address: string;
  ssh_username: string;
  ssh_port: number;
  ssh_key_id: number | null;
  ready: boolean;
  last_ready_at: string | null;
};

type TelegramSource = {
  id: number;
  name: string;
  access_mode: "bot" | "user_session";
  channel_ref: string;
  regex: string;
  enabled: boolean;
  last_sync_at: string | null;
};

type WarpKey = {
  id: number;
  fingerprint: string;
  tail: string;
  status: "valid" | "invalid" | "suspended";
  source_id: number | null;
  created_at: string;
  value: string | null;
};

type Schedule = {
  id: number;
  host_id: number;
  kind: "one_time" | "cron" | "interval";
  run_at: string | null;
  cron: string | null;
  interval_seconds: number | null;
  timezone: string;
  enabled: boolean;
};

type JobRun = {
  id: number;
  host_id: number;
  schedule_id: number | null;
  trigger: string;
  status: "pending" | "running" | "succeeded" | "failed" | "exhausted";
  summary: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type DataState = {
  hosts: Host[];
  sshKeys: SshKey[];
  telegramSources: TelegramSource[];
  warpKeys: WarpKey[];
  schedules: Schedule[];
  jobs: JobRun[];
};

const emptyData: DataState = {
  hosts: [],
  sshKeys: [],
  telegramSources: [],
  warpKeys: [],
  schedules: [],
  jobs: [],
};

const navItems: Array<[View, string, typeof Activity]> = [
  ["overview", "Overview", Activity],
  ["hosts", "Hosts", Server],
  ["ssh", "SSH keys", ShieldCheck],
  ["telegram", "Telegram", RadioTower],
  ["keys", "WARP keys", KeyRound],
  ["schedules", "Schedules", CalendarClock],
  ["jobs", "Jobs", TerminalSquare],
];

const DEFAULT_WARP_KEY_REGEX = String.raw`\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b`;

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem("warp.token") || "");
  const [admin, setAdmin] = useState("");
  const [active, setActive] = useState<View>("overview");
  const [data, setData] = useState<DataState>(emptyData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [modal, setModal] = useState<"" | "host" | "ssh" | "telegram" | "warp" | "schedule">("");
  const [revealed, setRevealed] = useState<Record<number, string>>({});

  const stats = useMemo(
    () => ({
      readyHosts: data.hosts.filter((host) => host.ready).length,
      validKeys: data.warpKeys.filter((key) => key.status === "valid").length,
      invalidKeys: data.warpKeys.filter((key) => key.status === "invalid").length,
      nextRun: describeNextRun(data.schedules),
    }),
    [data],
  );

  async function api<T>(path: string, init: RequestInit = {}) {
    return apiFetch<T>(path, { ...init, token });
  }

  async function loadAll(activeToken = token) {
    if (!activeToken) return;
    setLoading(true);
    setError("");
    try {
      const [me, hosts, sshKeys, telegramSources, warpKeys, schedules, jobs] = await Promise.all([
        apiFetch<{ username: string }>("/auth/me", { token: activeToken }),
        apiFetch<Host[]>("/hosts", { token: activeToken }),
        apiFetch<SshKey[]>("/ssh-keys", { token: activeToken }),
        apiFetch<TelegramSource[]>("/telegram-sources", { token: activeToken }),
        apiFetch<WarpKey[]>("/warp-keys", { token: activeToken }),
        apiFetch<Schedule[]>("/schedules", { token: activeToken }),
        apiFetch<JobRun[]>("/jobs", { token: activeToken }),
      ]);
      setAdmin(me.username);
      setData({ hosts, sshKeys, telegramSources, warpKeys, schedules, jobs });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      if (String(err).includes("401")) logout();
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (token) void loadAll(token);
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const hasActiveJob = data.jobs.some((job) => job.status === "pending" || job.status === "running");
    if (active !== "jobs" && !hasActiveJob) return;
    const interval = window.setInterval(() => {
      void loadAll(token);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [active, data.jobs, token]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      const response = await apiFetch<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
      });
      localStorage.setItem("warp.token", response.access_token);
      setToken(response.access_token);
      await loadAll(response.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }

  function logout() {
    localStorage.removeItem("warp.token");
    setToken("");
    setAdmin("");
    setData(emptyData);
  }

  async function submit(path: string, payload: unknown) {
    await runAction(async () => {
      await api(path, { method: "POST", body: JSON.stringify(payload) });
      setModal("");
      await loadAll();
    });
  }

  async function remove(path: string) {
    await runAction(async () => {
      await api(path, { method: "DELETE" });
      await loadAll();
    });
  }

  async function revealWarpKey(key: WarpKey) {
    await runAction(async () => {
      const response = await api<WarpKey>(`/warp-keys/${key.id}/reveal`);
      if (response.value) {
        setRevealed((current) => ({ ...current, [key.id]: response.value || "" }));
      }
    });
  }

  async function triggerHost(host: Host) {
    await runAction(async () => {
      await api<JobRun>(`/jobs/hosts/${host.id}/run`, { method: "POST" });
      await loadAll();
      setActive("jobs");
    });
  }

  async function syncSource(source: TelegramSource) {
    await runAction(async () => {
      await api(`/telegram-sources/${source.id}/sync`, { method: "POST" });
      await loadAll();
    });
  }

  async function runAction(action: () => Promise<void>) {
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      if (String(err).includes("401")) logout();
    }
  }

  if (!token) {
    return (
      <main className="loginShell">
        <section className="loginPanel">
          <div className="brandMark">
            <ShieldCheck size={24} />
          </div>
          <p className="eyebrow">operator access</p>
          <h1>WARP+ Orchestrator</h1>
          <form className="formStack" onSubmit={login}>
            <label>
              Username
              <input name="username" autoComplete="username" defaultValue="admin" />
            </label>
            <label>
              Password
              <input name="password" type="password" autoComplete="current-password" />
            </label>
            <button className="primaryAction" type="submit">Sign in</button>
          </form>
          {error ? <p className="errorText">{error}</p> : null}
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <aside className="rail" aria-label="Primary navigation">
        <div className="brandMark">
          <ShieldCheck size={24} />
        </div>
        {navItems.map(([id, label, Icon]) => (
          <button
            className={active === id ? "railButton active" : "railButton"}
            key={id}
            onClick={() => setActive(id)}
            title={label}
            aria-label={label}
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
          <div className="actionCluster">
            <button className="ghostButton" onClick={() => loadAll()} disabled={loading}>
              <RefreshCcw size={17} />
              Refresh
            </button>
            <span className="adminBadge">{admin}</span>
            <button className="iconButton" onClick={logout} aria-label="Logout">
              <LogOut size={17} />
            </button>
          </div>
        </header>
        {error ? <p className="errorBanner">{error}</p> : null}

        <section className="metricGrid" aria-label="System metrics">
          <Metric icon={<Server />} label="Hosts ready" value={`${stats.readyHosts}/${data.hosts.length}`} accent="green" />
          <Metric icon={<KeyRound />} label="Valid keys" value={stats.validKeys.toString()} accent="yellow" />
          <Metric icon={<Activity />} label="Invalidated" value={stats.invalidKeys.toString()} accent="red" />
          <Metric icon={<CalendarClock />} label="Next run" value={stats.nextRun} accent="blue" />
        </section>

        {active === "overview" ? (
          <Overview data={data} revealed={revealed} onReveal={revealWarpKey} onRun={triggerHost} onSync={syncSource} setModal={setModal} />
        ) : null}
        {active === "hosts" ? (
          <HostsView hosts={data.hosts} sshKeys={data.sshKeys} onRun={triggerHost} onDelete={(host) => remove(`/hosts/${host.id}`)} onAdd={() => setModal("host")} />
        ) : null}
        {active === "ssh" ? <SshView sshKeys={data.sshKeys} onAdd={() => setModal("ssh")} onDelete={(key) => remove(`/ssh-keys/${key.id}`)} /> : null}
        {active === "telegram" ? (
          <TelegramView sources={data.telegramSources} onAdd={() => setModal("telegram")} onSync={syncSource} onDelete={(source) => remove(`/telegram-sources/${source.id}`)} />
        ) : null}
        {active === "keys" ? (
          <KeysView
            keys={data.warpKeys}
            sources={data.telegramSources}
            revealed={revealed}
            onAdd={() => setModal("warp")}
            onReveal={revealWarpKey}
            onInvalidate={(key) => runAction(async () => {
              await api(`/warp-keys/${key.id}/invalidate`, { method: "POST" });
              await loadAll();
            })}
            onReactivate={(key) => runAction(async () => {
              await api(`/warp-keys/${key.id}/reactivate`, { method: "POST" });
              await loadAll();
            })}
            onDelete={(key) => remove(`/warp-keys/${key.id}`)}
          />
        ) : null}
        {active === "schedules" ? (
          <SchedulesView schedules={data.schedules} hosts={data.hosts} onAdd={() => setModal("schedule")} onDelete={(schedule) => remove(`/schedules/${schedule.id}`)} />
        ) : null}
        {active === "jobs" ? <JobsView jobs={data.jobs} hosts={data.hosts} /> : null}
      </section>

      {modal === "host" ? <HostModal sshKeys={data.sshKeys} onClose={() => setModal("")} onSubmit={(payload) => submit("/hosts", payload)} /> : null}
      {modal === "ssh" ? <SshModal onClose={() => setModal("")} onSubmit={(payload) => submit("/ssh-keys", payload)} /> : null}
      {modal === "telegram" ? <TelegramModal onClose={() => setModal("")} onSubmit={(payload) => submit("/telegram-sources", payload)} /> : null}
      {modal === "warp" ? <WarpKeyModal sources={data.telegramSources} onClose={() => setModal("")} onSubmit={(payload) => submit("/warp-keys", payload)} /> : null}
      {modal === "schedule" ? <ScheduleModal hosts={data.hosts} onClose={() => setModal("")} onSubmit={(payload) => submit("/schedules", payload)} /> : null}
    </main>
  );
}

function Overview({
  data,
  revealed,
  onReveal,
  onRun,
  onSync,
  setModal,
}: {
  data: DataState;
  revealed: Record<number, string>;
  onReveal: (key: WarpKey) => void;
  onRun: (host: Host) => void;
  onSync: (source: TelegramSource) => void;
  setModal: (modal: "" | "host" | "ssh" | "telegram" | "warp" | "schedule") => void;
}) {
  return (
    <section className="layout">
      <HostsPanel hosts={data.hosts.slice(0, 5)} onAdd={() => setModal("host")} onRun={onRun} />
      <TelegramPanel sources={data.telegramSources.slice(0, 5)} onAdd={() => setModal("telegram")} onSync={onSync} />
      <KeysPanel keys={data.warpKeys.slice(0, 5)} sources={data.telegramSources} revealed={revealed} onAdd={() => setModal("warp")} onReveal={onReveal} />
      <JobsPanel jobs={data.jobs.slice(0, 6)} hosts={data.hosts} />
    </section>
  );
}

function HostsView({
  hosts,
  sshKeys,
  onRun,
  onDelete,
  onAdd,
}: {
  hosts: Host[];
  sshKeys: SshKey[];
  onRun: (host: Host) => void;
  onDelete: (host: Host) => void;
  onAdd: () => void;
}) {
  return (
    <section className="panel full">
      <PanelTitle title="Hosts" action="Add host" onAction={onAdd} />
      <div className="dataTable">
        {hosts.map((host) => (
          <div className="dataRow" key={host.id}>
            <span className="statusDot" data-ready={host.ready} />
            <strong>{host.name}</strong>
            <span>{host.ssh_username}@{host.address}:{host.ssh_port}</span>
            <span>{sshKeys.find((key) => key.id === host.ssh_key_id)?.name || "no ssh key"}</span>
            <span className={host.ready ? "pill good" : "pill warn"}>{host.ready ? "ready" : "pending"}</span>
            <button className="iconButton" onClick={() => onRun(host)} aria-label={`Run ${host.name}`}><Play size={16} /></button>
            <button className="iconButton danger" onClick={() => onDelete(host)} aria-label={`Delete ${host.name}`}><Trash2 size={16} /></button>
          </div>
        ))}
        {hosts.length === 0 ? <EmptyState text="No hosts yet. Add a VM to start applying WARP+ keys." /> : null}
      </div>
    </section>
  );
}

function SshView({ sshKeys, onAdd, onDelete }: { sshKeys: SshKey[]; onAdd: () => void; onDelete: (key: SshKey) => void }) {
  return (
    <section className="panel full">
      <PanelTitle title="SSH keys" action="Add SSH key" onAction={onAdd} />
      <div className="dataTable">
        {sshKeys.map((key) => (
          <div className="dataRow" key={key.id}>
            <ShieldCheck size={18} />
            <strong>{key.name}</strong>
            <code>{key.fingerprint}</code>
            <span>tail {key.tail}</span>
            <button className="iconButton danger" onClick={() => onDelete(key)} aria-label={`Delete SSH key ${key.name}`}><Trash2 size={16} /></button>
          </div>
        ))}
        {sshKeys.length === 0 ? <EmptyState text="No SSH keys. Add a private key before creating SSH-backed hosts." /> : null}
      </div>
    </section>
  );
}

function TelegramView({
  sources,
  onAdd,
  onSync,
  onDelete,
}: {
  sources: TelegramSource[];
  onAdd: () => void;
  onSync: (source: TelegramSource) => void;
  onDelete: (source: TelegramSource) => void;
}) {
  return (
    <section className="panel full">
      <PanelTitle title="Telegram sources" action="Add source" onAction={onAdd} />
      <div className="dataTable">
        {sources.map((source) => (
          <div className="dataRow" key={source.id}>
            <RadioTower size={18} />
            <strong>{source.name}</strong>
            <span>{source.channel_ref}</span>
            <code>{source.regex}</code>
            <span className={source.enabled ? "pill good" : "pill warn"}>{source.access_mode}</span>
            <button className="iconButton" onClick={() => onSync(source)} aria-label={`Sync ${source.name}`}><RefreshCcw size={16} /></button>
            <button className="iconButton danger" onClick={() => onDelete(source)} aria-label={`Delete source ${source.name}`}><Trash2 size={16} /></button>
          </div>
        ))}
        {sources.length === 0 ? <EmptyState text="No Telegram sources. Add an authorized channel or bot source." /> : null}
      </div>
    </section>
  );
}

function KeysView({
  keys,
  sources,
  revealed,
  onAdd,
  onReveal,
  onInvalidate,
  onReactivate,
  onDelete,
}: {
  keys: WarpKey[];
  sources: TelegramSource[];
  revealed: Record<number, string>;
  onAdd: () => void;
  onReveal: (key: WarpKey) => void;
  onInvalidate: (key: WarpKey) => void;
  onReactivate: (key: WarpKey) => void;
  onDelete: (key: WarpKey) => void;
}) {
  return (
    <section className="panel full">
      <PanelTitle title="WARP keys" action="Import key" onAction={onAdd} />
      <div className="dataTable">
        {keys.map((key) => (
          <div className="dataRow" key={key.id}>
            <KeyRound size={18} />
            <code>{revealed[key.id] || maskSecret(key.tail)}</code>
            <span className={key.status === "valid" ? "pill good" : "pill bad"}>{key.status}</span>
            <span>{sources.find((source) => source.id === key.source_id)?.name || "manual"}</span>
            <button className="iconButton" onClick={() => onReveal(key)} aria-label={`Reveal WARP key ${key.tail}`}><Eye size={16} /></button>
            {key.status === "valid" ? (
              <button className="ghostButton compact" onClick={() => onInvalidate(key)}>Invalidate</button>
            ) : (
              <button className="ghostButton compact" onClick={() => onReactivate(key)}>Reactivate</button>
            )}
            <button className="iconButton danger" onClick={() => onDelete(key)} aria-label={`Delete WARP key ${key.tail}`}><Trash2 size={16} /></button>
          </div>
        ))}
        {keys.length === 0 ? <EmptyState text="No WARP keys. Import a key or sync an authorized Telegram source." /> : null}
      </div>
    </section>
  );
}

function SchedulesView({
  schedules,
  hosts,
  onAdd,
  onDelete,
}: {
  schedules: Schedule[];
  hosts: Host[];
  onAdd: () => void;
  onDelete: (schedule: Schedule) => void;
}) {
  return (
    <section className="panel full">
      <PanelTitle title="Schedules" action="Add schedule" onAction={onAdd} />
      <div className="dataTable">
        {schedules.map((schedule) => (
          <div className="dataRow" key={schedule.id}>
            <CalendarClock size={18} />
            <strong>{hosts.find((host) => host.id === schedule.host_id)?.name || `host #${schedule.host_id}`}</strong>
            <span>{describeSchedule(schedule)}</span>
            <span>{schedule.timezone}</span>
            <span className={schedule.enabled ? "pill good" : "pill warn"}>{schedule.enabled ? "enabled" : "disabled"}</span>
            <button className="iconButton danger" onClick={() => onDelete(schedule)} aria-label={`Delete schedule ${schedule.id}`}><Trash2 size={16} /></button>
          </div>
        ))}
        {schedules.length === 0 ? <EmptyState text="No schedules. Add one-time, cron, or interval rotations per host." /> : null}
      </div>
    </section>
  );
}

function JobsView({ jobs, hosts }: { jobs: JobRun[]; hosts: Host[] }) {
  return (
    <section className="panel full">
      <PanelTitle title="Jobs" />
      <div className="dataTable">
        {jobs.map((job) => (
          <div className="dataRow" key={job.id}>
            <span className={`jobLight ${jobTone(job.status)}`} />
            <strong>{hosts.find((host) => host.id === job.host_id)?.name || `host #${job.host_id}`}</strong>
            <code>{job.status}</code>
            <span>{job.trigger}</span>
            <span>{job.summary || "waiting for worker"}</span>
            <span>{formatDate(job.created_at)}</span>
          </div>
        ))}
        {jobs.length === 0 ? <EmptyState text="No jobs yet. Run a host update or wait for a schedule." /> : null}
      </div>
    </section>
  );
}

function HostsPanel({ hosts, onAdd, onRun }: { hosts: Host[]; onAdd: () => void; onRun: (host: Host) => void }) {
  return (
    <div className="panel large">
      <PanelTitle title="Hosts" action="Add host" onAction={onAdd} />
      <div className="hostTable">
        {hosts.map((host) => (
          <div className="hostRow" key={host.id}>
            <span className="statusDot" data-ready={host.ready} />
            <div>
              <strong>{host.name}</strong>
              <span>{host.ssh_username}@{host.address}</span>
            </div>
            <span className={host.ready ? "pill good" : "pill warn"}>{host.ready ? "ready" : "pending"}</span>
            <button className="iconButton" onClick={() => onRun(host)} aria-label={`Run ${host.name}`}><Play size={16} /></button>
          </div>
        ))}
        {hosts.length === 0 ? <EmptyState text="No hosts yet." /> : null}
      </div>
    </div>
  );
}

function TelegramPanel({ sources, onAdd, onSync }: { sources: TelegramSource[]; onAdd: () => void; onSync: (source: TelegramSource) => void }) {
  return (
    <div className="panel">
      <PanelTitle title="Telegram sources" action="Add source" onAction={onAdd} icon={<RadioTower size={18} />} />
      {sources.map((source) => (
        <div className="sourceRow" key={source.id}>
          <div>
            <strong>{source.name}</strong>
            <span>{source.access_mode}</span>
          </div>
          <code>{source.regex}</code>
          <button className="iconButton" onClick={() => onSync(source)} aria-label={`Sync ${source.name}`}><RefreshCcw size={16} /></button>
        </div>
      ))}
      {sources.length === 0 ? <EmptyState text="No sources yet." /> : null}
    </div>
  );
}

function KeysPanel({
  keys,
  sources,
  revealed,
  onAdd,
  onReveal,
}: {
  keys: WarpKey[];
  sources: TelegramSource[];
  revealed: Record<number, string>;
  onAdd: () => void;
  onReveal: (key: WarpKey) => void;
}) {
  return (
    <div className="panel large">
      <PanelTitle title="WARP keys" action="Import key" onAction={onAdd} />
      <div className="keyList">
        {keys.map((key) => (
          <div className="keyRow" key={key.id}>
            <KeyRound size={18} />
            <code>{revealed[key.id] || maskSecret(key.tail)}</code>
            <span className={key.status === "valid" ? "pill good" : "pill bad"}>{key.status}</span>
            <span className="muted">{sources.find((source) => source.id === key.source_id)?.name || "manual"}</span>
            <button className="iconButton" onClick={() => onReveal(key)} aria-label={`Reveal WARP key ${key.tail}`}><Eye size={17} /></button>
          </div>
        ))}
        {keys.length === 0 ? <EmptyState text="No keys yet." /> : null}
      </div>
    </div>
  );
}

function JobsPanel({ jobs, hosts }: { jobs: JobRun[]; hosts: Host[] }) {
  return (
    <div className="panel">
      <PanelTitle title="Job stream" />
      <div className="jobList">
        {jobs.map((job) => (
          <div className="jobRow" key={job.id}>
            <span className={`jobLight ${jobTone(job.status)}`} />
            <div>
              <strong>{hosts.find((host) => host.id === job.host_id)?.name || `host #${job.host_id}`}</strong>
              <span>{job.summary || job.trigger}</span>
            </div>
            <code>{job.status}</code>
          </div>
        ))}
        {jobs.length === 0 ? <EmptyState text="No job runs yet." /> : null}
      </div>
    </div>
  );
}

function HostModal({ sshKeys, onClose, onSubmit }: { sshKeys: SshKey[]; onClose: () => void; onSubmit: (payload: unknown) => void }) {
  return (
    <Modal title="Add host" onClose={onClose}>
      <form className="formGrid" onSubmit={(event) => handleSubmit(event, onSubmit, hostPayload)}>
        <Field label="Host name" name="name" required />
        <Field label="Address" name="address" required />
        <Field label="SSH username" name="ssh_username" defaultValue="root" required />
        <Field label="SSH port" name="ssh_port" defaultValue="22" type="number" required />
        <label>
          SSH key
          <select name="ssh_key_id" defaultValue={sshKeys[0]?.id || ""}>
            <option value="">None</option>
            {sshKeys.map((key) => <option key={key.id} value={key.id}>{key.name}</option>)}
          </select>
        </label>
        <SubmitRow onClose={onClose} submitLabel="Save host" />
      </form>
    </Modal>
  );
}

function SshModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (payload: unknown) => void }) {
  return (
    <Modal title="Add SSH key" onClose={onClose}>
      <form className="formGrid" onSubmit={(event) => handleSubmit(event, onSubmit, sshPayload)}>
        <Field label="Name" name="name" required />
        <label className="wide">
          Private key
          <textarea name="private_key" rows={8} required />
        </label>
        <Field label="Passphrase" name="passphrase" type="password" />
        <SubmitRow onClose={onClose} submitLabel="Save SSH key" />
      </form>
    </Modal>
  );
}

function TelegramModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (payload: unknown) => void }) {
  return (
    <Modal title="Add Telegram source" onClose={onClose}>
      <form className="formGrid" onSubmit={(event) => handleSubmit(event, onSubmit, telegramPayload)}>
        <Field label="Name" name="name" required />
        <label>
          Access mode
          <select name="access_mode" defaultValue="bot">
            <option value="bot">Bot API</option>
            <option value="user_session">User session</option>
          </select>
        </label>
        <Field label="Channel ref" name="channel_ref" placeholder="@channel" required />
        <Field label="Regex" name="regex" defaultValue={DEFAULT_WARP_KEY_REGEX} required />
        <label className="wide">
          Secret
          <textarea name="secret" rows={5} placeholder="Bot token or user-session JSON" />
        </label>
        <SubmitRow onClose={onClose} submitLabel="Save source" />
      </form>
    </Modal>
  );
}

function WarpKeyModal({ sources, onClose, onSubmit }: { sources: TelegramSource[]; onClose: () => void; onSubmit: (payload: unknown) => void }) {
  return (
    <Modal title="Import WARP key" onClose={onClose}>
      <form className="formGrid" onSubmit={(event) => handleSubmit(event, onSubmit, warpPayload)}>
        <Field label="WARP key" name="value" required />
        <label>
          Source
          <select name="source_id" defaultValue="">
            <option value="">Manual</option>
            {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
          </select>
        </label>
        <SubmitRow onClose={onClose} submitLabel="Import key" />
      </form>
    </Modal>
  );
}

function ScheduleModal({ hosts, onClose, onSubmit }: { hosts: Host[]; onClose: () => void; onSubmit: (payload: unknown) => void }) {
  return (
    <Modal title="Add schedule" onClose={onClose}>
      <form className="formGrid" onSubmit={(event) => handleSubmit(event, onSubmit, schedulePayload)}>
        <label>
          Host
          <select name="host_id" defaultValue={hosts[0]?.id || ""} required>
            {hosts.map((host) => <option key={host.id} value={host.id}>{host.name}</option>)}
          </select>
        </label>
        <label>
          Kind
          <select name="kind" defaultValue="interval">
            <option value="interval">Interval</option>
            <option value="cron">Cron</option>
            <option value="one_time">One time</option>
          </select>
        </label>
        <Field label="Run at" name="run_at" type="datetime-local" />
        <Field label="Cron" name="cron" placeholder="0 4 * * *" />
        <Field label="Interval seconds" name="interval_seconds" type="number" defaultValue="3600" />
        <Field label="Timezone" name="timezone" defaultValue="Europe/Moscow" required />
        <SubmitRow onClose={onClose} submitLabel="Save schedule" />
      </form>
    </Modal>
  );
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="modalBackdrop" role="presentation">
      <section className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="panelHeader">
          <h2>{title}</h2>
          <button className="iconButton" onClick={onClose} aria-label="Close dialog">x</button>
        </div>
        {children}
      </section>
    </div>
  );
}

function Field({ label, name, ...props }: { label: string; name: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label>
      {label}
      <input name={name} {...props} />
    </label>
  );
}

function SubmitRow({ submitLabel, onClose }: { submitLabel: string; onClose: () => void }) {
  return (
    <div className="formActions wide">
      <button className="ghostButton" type="button" onClick={onClose}>Cancel</button>
      <button className="primaryAction" type="submit">{submitLabel}</button>
    </div>
  );
}

function handleSubmit(event: FormEvent<HTMLFormElement>, onSubmit: (payload: unknown) => void, mapper: (form: FormData) => unknown) {
  event.preventDefault();
  onSubmit(mapper(new FormData(event.currentTarget)));
}

function hostPayload(form: FormData) {
  return {
    name: stringValue(form, "name"),
    address: stringValue(form, "address"),
    ssh_username: stringValue(form, "ssh_username"),
    ssh_port: numberValue(form, "ssh_port") || 22,
    ssh_key_id: nullableNumber(form, "ssh_key_id"),
  };
}

function sshPayload(form: FormData) {
  return {
    name: stringValue(form, "name"),
    private_key: stringValue(form, "private_key"),
    passphrase: nullableString(form, "passphrase"),
  };
}

function telegramPayload(form: FormData) {
  return {
    name: stringValue(form, "name"),
    access_mode: stringValue(form, "access_mode"),
    channel_ref: stringValue(form, "channel_ref"),
    regex: stringValue(form, "regex"),
    secret: nullableString(form, "secret"),
    enabled: true,
  };
}

function warpPayload(form: FormData) {
  return {
    value: stringValue(form, "value"),
    source_id: nullableNumber(form, "source_id"),
  };
}

function schedulePayload(form: FormData) {
  const kind = stringValue(form, "kind");
  return {
    host_id: numberValue(form, "host_id"),
    kind,
    run_at: kind === "one_time" ? localDateTimeToIso(stringValue(form, "run_at")) : null,
    cron: kind === "cron" ? stringValue(form, "cron") : null,
    interval_seconds: kind === "interval" ? numberValue(form, "interval_seconds") : null,
    timezone: stringValue(form, "timezone"),
    enabled: true,
  };
}

function Metric({ icon, label, value, accent }: { icon: ReactNode; label: string; value: string; accent: string }) {
  return (
    <div className="metric" data-accent={accent}>
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PanelTitle({ title, action, onAction, icon }: { title: string; action?: string; onAction?: () => void; icon?: ReactNode }) {
  return (
    <div className="panelHeader">
      <h2>{title}</h2>
      {action && onAction ? (
        <button className="ghostButton" onClick={onAction}>
          {icon || <Plus size={17} />}
          {action}
        </button>
      ) : null}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="emptyState">{text}</div>;
}

function maskSecret(tail: string) {
  return `•••• •••• •••• ${tail}`;
}

function stringValue(form: FormData, name: string) {
  return String(form.get(name) || "").trim();
}

function nullableString(form: FormData, name: string) {
  const value = stringValue(form, name);
  return value ? value : null;
}

function numberValue(form: FormData, name: string) {
  return Number(stringValue(form, name));
}

function nullableNumber(form: FormData, name: string) {
  const value = stringValue(form, name);
  return value ? Number(value) : null;
}

function localDateTimeToIso(value: string) {
  return value ? new Date(value).toISOString() : null;
}

function describeSchedule(schedule: Schedule) {
  if (schedule.kind === "interval") return `Every ${Math.round((schedule.interval_seconds || 0) / 60)} min`;
  if (schedule.kind === "cron") return schedule.cron || "cron";
  return schedule.run_at ? formatDate(schedule.run_at) : "one time";
}

function describeNextRun(schedules: Schedule[]) {
  if (schedules.length === 0) return "not set";
  return describeSchedule(schedules[0]);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function jobTone(status: JobRun["status"]) {
  if (status === "succeeded") return "good";
  if (status === "failed" || status === "exhausted") return "bad";
  return "warn";
}
