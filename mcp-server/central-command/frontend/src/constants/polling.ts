/**
 * Polling intervals for portal data freshness.
 *
 * Round-table 33 (2026-05-05) ticket #10 — single source of truth so a
 * future "make portals more real-time" or "ease back on polling for
 * battery/cost" change happens in one place, not 14 components.
 *
 * Customer (substrate-class) gets a 30s cadence: appliance state moves
 * slowly, the customer needs eventual consistency, not sub-minute.
 *
 * Operator (partner-class) gets 15s: a partner-admin watching an
 * incident wants to see status flips quickly. Still polled (not
 * websocket'd) — the cost of a websocket upgrade isn't worth it for
 * the per-page presence we have today.
 */
export const POLL_INTERVAL_CLIENT_MS = 30_000;
export const POLL_INTERVAL_PARTNER_MS = 15_000;
