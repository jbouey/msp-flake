import { useEffect, useState } from 'react';
// Vite worker import pattern: the `?worker` query tells Vite to compile the
// .ts file into a Web Worker bundle and emit a default-export class that
// constructs an instance. The type-only import below pulls in the message
// shapes from the worker source without bundling its body twice.
import VerifyChainWorker from './verifyChainWorker.ts?worker';
import type { VerifySummary, WorkerOutgoingMsg } from './verifyChainWorker';

/**
 * Full-chain browser verification hook.
 *
 * SESSION 203 Tier 2.1: companion to `useBrowserVerify` (which only checks
 * the latest 10 bundles). This hook walks the ENTIRE chain in batches of N
 * bundles, runs all verification in a Web Worker (to keep the UI
 * responsive), and reports incremental progress so the user sees a live
 * counter as the verification advances.
 *
 * Independence claim:
 *   - The Worker is loaded from the same origin as the page.
 *   - The verification math (Ed25519, SHA256, hash chain linkage) runs
 *     in the browser, on the auditor's machine, in a thread the auditor
 *     can inspect via Chrome devtools → Sources → Workers.
 *   - The backend only serves raw bundles, public keys, and the count.
 *     It does NOT report a verdict — the verdict is computed client-side.
 *
 * Limitations:
 *   - Does NOT verify OpenTimestamps → Bitcoin anchoring. That requires a
 *     separate `ots verify` binary which the auditor runs off-platform.
 *     This hook covers Ed25519 + hash chain only.
 *   - Bundles with no signature (legacy/pending) are counted as
 *     `signaturesMissing`, not `signaturesFailed`. Honesty about scope.
 */

const BATCH_SIZE = 200;

export interface FullVerifyState {
  status: 'idle' | 'loading' | 'verifying' | 'done' | 'error';
  totalBundles: number;
  bundlesProcessed: number;
  signaturesVerified: number;
  signaturesFailed: number;
  signaturesMissing: number;
  chainLinksVerified: number;
  chainLinksFailed: number;
  publicKeysLoaded: number;
  lastChainPosition: number;
  summary: VerifySummary | null;
  error: string | null;
  startedAt: string | null;
  completedAt: string | null;
}

const EMPTY: FullVerifyState = {
  status: 'idle',
  totalBundles: 0,
  bundlesProcessed: 0,
  signaturesVerified: 0,
  signaturesFailed: 0,
  signaturesMissing: 0,
  chainLinksVerified: 0,
  chainLinksFailed: 0,
  publicKeysLoaded: 0,
  lastChainPosition: 0,
  summary: null,
  error: null,
  startedAt: null,
  completedAt: null,
};

interface PublicKey {
  appliance_id: string;
  public_key_hex: string;
}

interface BundlesPage {
  bundles: Array<{
    bundle_id: string;
    bundle_hash: string;
    prev_hash: string | null;
    chain_position: number;
    chain_hash?: string | null;
    agent_signature?: string | null;
    ots_status?: string | null;
  }>;
  total: number;
  limit: number;
  offset: number;
  order: string;
}

export function useBrowserVerifyFull(
  siteId: string,
  options?: { autoStart?: boolean },
): FullVerifyState & { start: () => void; cancel: () => void } {
  const [state, setState] = useState<FullVerifyState>(EMPTY);
  const [trigger, setTrigger] = useState(0);

  const start = () => setTrigger((t) => t + 1);
  const cancel = () => setState((s) => ({ ...s, status: 'idle' }));

  useEffect(() => {
    if (!siteId) return;
    if (!options?.autoStart && trigger === 0) return;

    let cancelled = false;
    const worker = new VerifyChainWorker();

    setState({
      ...EMPTY,
      status: 'loading',
      startedAt: new Date().toISOString(),
    });

    worker.onmessage = (ev: globalThis.MessageEvent<WorkerOutgoingMsg>) => {
      if (cancelled) return;
      const msg = ev.data;
      if (msg.type === 'progress') {
        setState((s) => ({
          ...s,
          status: 'verifying',
          bundlesProcessed: msg.bundlesProcessed,
          signaturesVerified: msg.signaturesVerified,
          signaturesFailed: msg.signaturesFailed,
          signaturesMissing: msg.signaturesMissing,
          chainLinksVerified: msg.chainLinksVerified,
          chainLinksFailed: msg.chainLinksFailed,
          lastChainPosition: msg.lastChainPosition,
        }));
      } else if (msg.type === 'done') {
        setState((s) => ({
          ...s,
          status: 'done',
          summary: msg.summary,
          completedAt: new Date().toISOString(),
        }));
      } else if (msg.type === 'error') {
        setState((s) => ({
          ...s,
          status: 'error',
          error: msg.message,
        }));
      }
    };

    worker.onerror = (err) => {
      if (cancelled) return;
      setState((s) => ({
        ...s,
        status: 'error',
        error: err.message || 'Worker error',
      }));
    };

    const run = async () => {
      try {
        // 1. Fetch public keys (single request)
        const pkRes = await fetch(`/api/evidence/sites/${siteId}/public-keys`, {
          credentials: 'same-origin', // same-origin-allowed: browser-verify — intentional cryptographic isolation (BUG 2 KEEP 2026-05-12)
        });
        if (!pkRes.ok) {
          throw new Error(`public-keys fetch failed: ${pkRes.status}`);
        }
        const pkData = (await pkRes.json()) as { public_keys: PublicKey[] };
        const keys = pkData.public_keys || [];
        worker.postMessage({
          type: 'init',
          publicKeys: keys.map((k) => k.public_key_hex),
        });
        setState((s) => ({ ...s, publicKeysLoaded: keys.length }));

        // 2. Stream bundles in batches of BATCH_SIZE, ASCENDING by chain_position
        let offset = 0;
        let total = 0;
        while (!cancelled) {
          const url =
            `/api/evidence/sites/${siteId}/bundles?` +
            `limit=${BATCH_SIZE}&offset=${offset}&order=asc&include_signatures=true`;
          const bRes = await fetch(url, { credentials: 'same-origin' }); // same-origin-allowed: browser-verify — intentional cryptographic isolation (BUG 2 KEEP 2026-05-12)
          if (!bRes.ok) {
            throw new Error(`bundles fetch failed at offset ${offset}: ${bRes.status}`);
          }
          const bData = (await bRes.json()) as BundlesPage;
          if (offset === 0) {
            total = bData.total || 0;
            setState((s) => ({ ...s, totalBundles: total }));
          }
          const bundles = bData.bundles || [];
          if (bundles.length === 0) break;
          worker.postMessage({ type: 'batch', bundles });
          offset += bundles.length;
          if (bundles.length < BATCH_SIZE) break;
        }

        if (!cancelled) {
          worker.postMessage({ type: 'finalize' });
        }
      } catch (e) {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          status: 'error',
          error: e instanceof Error ? e.message : String(e),
        }));
      }
    };

    run();

    return () => {
      cancelled = true;
      worker.terminate();
    };
  }, [siteId, trigger, options?.autoStart]);

  return { ...state, start, cancel };
}
