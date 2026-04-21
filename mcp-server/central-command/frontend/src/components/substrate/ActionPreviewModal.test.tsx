import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import ActionPreviewModal from "./ActionPreviewModal";

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
    action_id: "42", status: "completed", details: { deleted: 1, mac: "aa:bb" },
  }), { status: 200 })) as unknown as typeof fetch;
});

describe("ActionPreviewModal", () => {
  it("disables confirm until reason >=20 chars + initials present", () => {
    render(<ActionPreviewModal
      actionKey="unlock_platform_account"
      requiredReasonChars={20}
      plan="Unlock partners.email=a@b.c"
      targetRef={{ table: "partners", email: "a@b.c" }}
      onClose={() => {}}
      onDone={() => {}}
    />);
    const confirm = screen.getByRole("button", { name: /confirm/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "Short" },
    });
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "Confirmed via phone call, user is legit" },
    });
    expect(confirm).not.toBeDisabled();
  });

  it("POSTs to endpoint and shows action_id on success", async () => {
    const onDone = vi.fn();
    render(<ActionPreviewModal
      actionKey="cleanup_install_session"
      requiredReasonChars={0}
      plan="Delete stale install_sessions row for aa:bb"
      targetRef={{ mac: "aa:bb" }}
      onClose={() => {}}
      onDone={onDone}
    />);
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText(/action_id.*42/)).toBeInTheDocument());
    expect(onDone).toHaveBeenCalledWith("42");
  });

  it("shows error body + CLI fallback on failure", async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn(async () => new Response(
      JSON.stringify({ detail: "no install_sessions row for mac=zz" }), { status: 404 },
    ));
    render(<ActionPreviewModal
      actionKey="cleanup_install_session"
      requiredReasonChars={0}
      plan="Delete stale row"
      targetRef={{ mac: "zz" }}
      onClose={() => {}}
      onDone={() => {}}
      cliFallback="fleet_cli --actor-email you@x"
    />);
    fireEvent.change(screen.getByLabelText(/initials/i), { target: { value: "JR" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText(/no install_sessions row/)).toBeInTheDocument());
    expect(screen.getByText(/fleet_cli --actor-email you@x/)).toBeInTheDocument();
  });
});
