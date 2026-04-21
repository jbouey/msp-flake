import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import SubstrateRunbookLibrary from "./SubstrateRunbookLibrary";

beforeEach(() => {
  globalThis.fetch = vi.fn(async (input: unknown) => {
    const url = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : (input as { url: string }).url;
    if (url.includes("/admin/substrate/runbooks")) {
      return new Response(JSON.stringify({
        items: [
          {
            invariant: "install_loop",
            display_name: "Install Loop",
            severity: "sev1",
            has_action: true,
            action_key: "cleanup_install_session",
          },
          {
            invariant: "vps_disk_pressure",
            display_name: "VPS Disk Pressure",
            severity: "sev2",
            has_action: false,
            action_key: null,
          },
        ],
      }), { status: 200, headers: { "Content-Type": "application/json" } });
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

describe("SubstrateRunbookLibrary", () => {
  it("renders grid with all invariants", async () => {
    wrap(<SubstrateRunbookLibrary />);
    await waitFor(() => expect(screen.getByText("Install Loop")).toBeInTheDocument());
    expect(screen.getByText("VPS Disk Pressure")).toBeInTheDocument();
  });

  it("filters by has-action", async () => {
    wrap(<SubstrateRunbookLibrary />);
    await waitFor(() => screen.getByText("Install Loop"));
    fireEvent.click(screen.getByLabelText(/only with action/i));
    expect(screen.getByText("Install Loop")).toBeInTheDocument();
    expect(screen.queryByText("VPS Disk Pressure")).toBeNull();
  });
});
