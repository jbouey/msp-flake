import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CopyCliButton from "./CopyCliButton";

beforeEach(() => {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    value: { writeText: vi.fn(async () => {}) },
    configurable: true,
  });
});

describe("CopyCliButton", () => {
  it("substitutes site_id and mac from details into template", async () => {
    render(<CopyCliButton
      template={'fleet_cli create update_daemon --site-id {site_id} --mac {mac} --actor-email YOU@example.com --reason "..."'}
      details={{ site_id: "abc-123", mac: "aa:bb:cc:dd:ee:ff" }}
    />);
    fireEvent.click(screen.getByRole("button", { name: /copy cli/i }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'fleet_cli create update_daemon --site-id abc-123 --mac aa:bb:cc:dd:ee:ff --actor-email YOU@example.com --reason "..."',
    ));
  });

  it("shows run-locally reminder after copy", async () => {
    render(<CopyCliButton template="fleet_cli status" details={{}} />);
    fireEvent.click(screen.getByRole("button", { name: /copy cli/i }));
    await waitFor(() => expect(
      screen.getByText(/run under your own --actor-email/i),
    ).toBeInTheDocument());
  });

  it("does not render when template is empty", () => {
    render(<CopyCliButton template="" details={{}} />);
    expect(screen.queryByRole("button", { name: /copy cli/i })).toBeNull();
  });
});
