import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const fixtures = {
  hosts: [
    { id: 1, name: "edge-prod", address: "10.0.0.10", ssh_username: "root", ssh_port: 22, ssh_key_id: 1, ready: false, last_ready_at: null },
  ],
  sshKeys: [{ id: 1, name: "ops", fingerprint: "sha256:abc:tail", tail: "tail", private_key: null }],
  telegramSources: [
    { id: 1, name: "owned", access_mode: "bot", channel_ref: "@owned", regex: "KEY-\\d+", enabled: true, last_sync_at: null },
  ],
  warpKeys: [{ id: 1, fingerprint: "sha256:def:Y-42", tail: "Y-42", status: "valid", source_id: 1, created_at: "2026-05-26T10:00:00Z", value: null }],
  schedules: [{ id: 1, host_id: 1, kind: "interval", run_at: null, cron: null, interval_seconds: 3600, timezone: "Europe/Moscow", enabled: true }],
  jobs: [{ id: 1, host_id: 1, schedule_id: null, trigger: "manual", status: "pending", summary: null, created_at: "2026-05-26T10:00:00Z", started_at: null, finished_at: null }],
};

function mockApi() {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/auth/login")) {
        return Response.json({ access_token: "token", token_type: "bearer" });
      }
      if (url.endsWith("/auth/me")) return Response.json({ username: "admin" });
      if (url.endsWith("/hosts") && init?.method === "POST") {
        return Response.json({ ...fixtures.hosts[0], id: 2, name: "edge-new" }, { status: 201 });
      }
      if (url.endsWith("/hosts")) return Response.json(fixtures.hosts);
      if (url.endsWith("/ssh-keys")) return Response.json(fixtures.sshKeys);
      if (url.endsWith("/telegram-sources")) return Response.json(fixtures.telegramSources);
      if (url.endsWith("/warp-keys")) return Response.json(fixtures.warpKeys);
      if (url.endsWith("/schedules")) return Response.json(fixtures.schedules);
      if (url.endsWith("/jobs")) return Response.json(fixtures.jobs);
      if (url.includes("/reveal")) return Response.json({ ...fixtures.warpKeys[0], value: "KEY-42" });
      if (url.includes("/run")) return Response.json({ ...fixtures.jobs[0], id: 2 }, { status: 202 });
      return Response.json({ status: "ok" });
    }),
  );
  return calls;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("logs in and renders live API data across work areas", async () => {
    mockApi();
    render(<App />);

    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "admin");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findAllByText("edge-prod")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "WARP+ Orchestrator" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hosts" })).toBeInTheDocument();
    expect(screen.getByText("Telegram sources")).toBeInTheDocument();
    expect(screen.getByText("WARP keys")).toBeInTheDocument();
    expect(screen.getByText("Job stream")).toBeInTheDocument();
  });

  it("keeps secrets masked until the operator reveals them", async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByText("•••• •••• •••• Y-42");
    await user.click(screen.getByRole("button", { name: "Reveal WARP key Y-42" }));

    expect(await screen.findByText("KEY-42")).toBeInTheDocument();
  });

  it("submits host forms to the backend instead of only changing local placeholders", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findAllByText("edge-prod")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Hosts" }));
    await user.click(screen.getByRole("button", { name: "Add host" }));
    await user.type(screen.getByLabelText("Host name"), "edge-new");
    await user.type(screen.getByLabelText("Address"), "10.0.0.11");
    await user.click(screen.getByRole("button", { name: "Save host" }));

    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith("/hosts") && call.init?.method === "POST")).toBe(true);
    });
  });

  it("shows API errors from failed form actions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/auth/login")) return Response.json({ access_token: "token", token_type: "bearer" });
        if (url.endsWith("/auth/me")) return Response.json({ username: "admin" });
        if (url.endsWith("/hosts") && init?.method === "POST") {
          return Response.json({ detail: "host already exists" }, { status: 409 });
        }
        if (url.endsWith("/hosts")) return Response.json(fixtures.hosts);
        if (url.endsWith("/ssh-keys")) return Response.json(fixtures.sshKeys);
        if (url.endsWith("/telegram-sources")) return Response.json(fixtures.telegramSources);
        if (url.endsWith("/warp-keys")) return Response.json(fixtures.warpKeys);
        if (url.endsWith("/schedules")) return Response.json(fixtures.schedules);
        if (url.endsWith("/jobs")) return Response.json(fixtures.jobs);
        return Response.json({ status: "ok" });
      }),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findAllByText("edge-prod")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Hosts" }));
    await user.click(screen.getByRole("button", { name: "Add host" }));
    await user.type(screen.getByLabelText("Host name"), "edge-prod");
    await user.type(screen.getByLabelText("Address"), "10.0.0.10");
    await user.click(screen.getByRole("button", { name: "Save host" }));

    expect(await screen.findByText("API 409: host already exists")).toBeInTheDocument();
  });

  it("submits the default Telegram regex without double escaping", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findAllByText("edge-prod")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Telegram" }));
    await user.click(screen.getByRole("button", { name: "Add source" }));
    await user.type(screen.getByLabelText("Name"), "new-source");
    await user.type(screen.getByLabelText("Channel ref"), "@new-source");
    await user.click(screen.getByRole("button", { name: "Save source" }));

    const createCall = calls.find((call) => call.url.endsWith("/telegram-sources") && call.init?.method === "POST");
    expect(JSON.parse(String(createCall?.init?.body)).regex).toBe(
      "\\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\\b",
    );
  });
});
