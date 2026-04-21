import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import RunbookDrawer from "./RunbookDrawer";

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
    invariant: "install_loop",
    display_name: "Install Loop",
    severity: "sev1",
    markdown: "## What this means\nThe installer is looping on a machine.\n",
  }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("RunbookDrawer", () => {
  it("renders rehype-sanitize-safe markdown", async () => {
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="install_loop" onClose={() => {}} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(
      screen.getByText("The installer is looping on a machine."),
    ).toBeInTheDocument());
  });

  it("strips raw HTML script tags", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn(async () => new Response(JSON.stringify({
      invariant: "x", display_name: "x", severity: "sev1",
      markdown: "## x\n<script>alert(1)</script>\nhello",
    }), { status: 200 }));
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="x" onClose={() => {}} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
    expect(document.querySelector("script[data-test]")).toBeNull();
  });

  it("shows error state on 404", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn(async () => new Response(
      JSON.stringify({ detail: "unknown invariant: foo" }), { status: 404 },
    ));
    render(
      <QueryClientProvider client={client()}>
        <RunbookDrawer invariant="foo" onClose={() => {}} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/unknown invariant/)).toBeInTheDocument());
  });
});
