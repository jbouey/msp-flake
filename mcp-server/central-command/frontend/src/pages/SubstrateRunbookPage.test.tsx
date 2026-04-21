import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import SubstrateRunbookPage from "./SubstrateRunbookPage";

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
    invariant: "install_loop",
    display_name: "Install Loop",
    severity: "sev1",
    markdown: "## What this means\nbody",
  }), { status: 200 })) as unknown as typeof fetch;
});

describe("SubstrateRunbookPage", () => {
  it("renders the runbook by URL param", async () => {
    render(
      <MemoryRouter initialEntries={["/admin/substrate/runbook/install_loop"]}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Routes>
            <Route path="/admin/substrate/runbook/:invariant" element={<SubstrateRunbookPage />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("body")).toBeInTheDocument());
    expect(screen.getByText("Install Loop")).toBeInTheDocument();
  });
});
