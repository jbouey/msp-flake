import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AdminSubstrateHealth from "./AdminSubstrateHealth";

const violationsPayload = {
  active: [
    {
      invariant: "install_loop",
      display_name: "Install Loop",
      recommended_action: "BIOS boot order",
      severity: "sev1",
      site_id: null,
      detected_at: "2026-04-19T12:00:00Z",
      last_seen_at: "2026-04-19T12:00:00Z",
      minutes_open: 5,
      details: { mac: "aa:bb:cc:dd:ee:ff", stage: "live_usb" },
    },
    {
      invariant: "auth_failure_lockout",
      display_name: "Account Lockout",
      recommended_action: "Unlock after verification",
      severity: "sev1",
      site_id: null,
      detected_at: "2026-04-19T12:00:00Z",
      last_seen_at: "2026-04-19T12:00:00Z",
      minutes_open: 2,
      details: { table: "partners", email: "x@y.z" },
    },
    {
      invariant: "vps_disk_pressure",
      display_name: "VPS Disk Pressure",
      recommended_action: "free disk",
      severity: "sev2",
      site_id: null,
      detected_at: "2026-04-19T12:00:00Z",
      last_seen_at: "2026-04-19T12:00:00Z",
      minutes_open: 3,
      details: {},
    },
  ],
  rollup: { sev1: 2, sev2: 1, sev3: 0 },
  active_total: 3,
  resolved_24h: [],
};

const slaPayload = {
  sample_count: 0,
  target_minutes: 5,
  breaches_over_target: 0,
  p50_minutes: null,
  p95_minutes: null,
  p99_minutes: null,
  max_minutes: null,
  min_minutes: null,
};

const fleetUpdateHealthPayload = {
  nixos_rebuild: {
    success_7d: 0,
    failed_7d: 1,
    expired_7d: 0,
    success_30d: 0,
    failed_30d: 5,
    expired_30d: 1,
    last_success_at: "2026-02-21T04:53:12Z",
    last_failure_at: "2026-04-21T23:44:20Z",
    days_since_last_success: 59,
  },
  update_daemon: {
    completed_7d: 3,
    skipped_7d: 12,
    failed_7d: 0,
    last_completion_at: "2026-04-22T00:01:30Z",
  },
  agent_versions: [
    { version: "0.4.7", count: 3 },
    { version: "0.4.4", count: 1 },
  ],
};

beforeEach(() => {
  globalThis.fetch = vi.fn(async (input: unknown) => {
    const url = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : (input as { url: string }).url;
    if (url.includes("/admin/substrate-violations")) {
      return new Response(JSON.stringify(violationsPayload), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/admin/substrate-installation-sla")) {
      return new Response(JSON.stringify(slaPayload), { status: 200 });
    }
    if (url.includes("/admin/substrate-fleet-update-health")) {
      return new Response(JSON.stringify(fleetUpdateHealthPayload), { status: 200 });
    }
    if (url.includes("/admin/substrate/runbook/")) {
      return new Response(JSON.stringify({
        invariant: "install_loop",
        display_name: "Install Loop",
        severity: "sev1",
        markdown: "## What this means\nbody",
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
});

const wrap = (ui: React.ReactNode) => render(
  <MemoryRouter>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>
  </MemoryRouter>,
);

describe("AdminSubstrateHealth upgrades", () => {
  it("shows Run action only on whitelisted invariants", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => expect(screen.getByText("Install Loop")).toBeInTheDocument());
    expect(screen.getAllByRole("button", { name: /run action/i }).length).toBeGreaterThanOrEqual(2);
    const vpsRow = screen.getByText("VPS Disk Pressure").closest("[data-testid='violation-row']")!;
    expect(vpsRow).not.toBeNull();
    expect(vpsRow.querySelector("[data-action='run']")).toBeNull();
  });

  it("every row has View runbook", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /view runbook/i }))
      .toHaveLength(3));
  });

  it("opens drawer when View runbook clicked", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => screen.getByText("Install Loop"));
    fireEvent.click(screen.getAllByRole("button", { name: /view runbook/i })[0]);
    await waitFor(() => expect(screen.getByText("body")).toBeInTheDocument());
  });

  it("renders Fleet update health card with drought + version distribution", async () => {
    wrap(<AdminSubstrateHealth />);
    await waitFor(() => expect(screen.getByText("Fleet update health")).toBeInTheDocument());
    // Days since last success = 59 from fixture
    expect(screen.getByText("59")).toBeInTheDocument();
    // 7d success / total = 0 / 1
    expect(screen.getByText("0 / 1")).toBeInTheDocument();
    // At least one version chip
    expect(screen.getByText(/0\.4\.7/)).toBeInTheDocument();
  });
});
