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
});
