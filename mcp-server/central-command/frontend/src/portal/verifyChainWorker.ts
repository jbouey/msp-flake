/**
 * Full-chain browser verification — Web Worker.
 *
 * SESSION 203 Tier 2.1: Batch 5 only verified the latest 10 bundles on the
 * main thread. An auditor running through the verification flow could
 * legitimately object that "ten samples" is not the same as proving the
 * entire chain. This worker walks the FULL chain in batches of N bundles,
 * runs Ed25519 + chain-hash verification on each one in a background
 * thread (so the UI stays responsive), and reports incremental progress
 * via postMessage.
 *
 * Protocol (main thread → worker):
 *   { type: 'init', publicKeys: PublicKeyHex[] }
 *     → set up the keys once at the start
 *
 *   { type: 'batch', bundles: WireBundle[], chainStartPosition: number }
 *     → verify a batch and return progress. The chainStartPosition is the
 *       chain_position of the FIRST bundle in this batch — the worker uses
 *       this to verify prev_hash linkage across batch boundaries.
 *
 *   { type: 'finalize' }
 *     → emit the final summary message
 *
 * Worker → main thread:
 *   { type: 'progress', verified, failed, missing, chainOk, chainBad,
 *     bundlesProcessed, lastChainPosition }
 *
 *   { type: 'done', summary: VerifySummary }
 *
 *   { type: 'error', message }
 *
 * Why a worker?
 *   - 10,000 SHA256 + Ed25519 ops on the main thread freeze the UI for
 *     seconds. Web Workers run on a separate thread.
 *   - The auditor can open browser devtools, see the worker spawn, and
 *     verify that the verification is happening on their machine (not on
 *     the server). This is exactly the independence claim we need.
 */

import * as ed from '@noble/ed25519';

// =============================================================================
// Wire types — must match the JSON shape of /api/evidence/sites/{id}/bundles?include_signatures=true
// =============================================================================

export interface WireBundle {
  bundle_id: string;
  bundle_hash: string;
  prev_hash: string | null;
  chain_position: number;
  chain_hash?: string | null;
  agent_signature?: string | null;
  ots_status?: string | null;
  signed?: boolean;
}

interface InitMsg {
  type: 'init';
  publicKeys: string[];
}

interface BatchMsg {
  type: 'batch';
  bundles: WireBundle[];
}

interface FinalizeMsg {
  type: 'finalize';
}

type IncomingMsg = InitMsg | BatchMsg | FinalizeMsg;

export interface ProgressMsg {
  type: 'progress';
  bundlesProcessed: number;
  signaturesVerified: number;
  signaturesFailed: number;
  signaturesMissing: number;
  chainLinksVerified: number;
  chainLinksFailed: number;
  lastChainPosition: number;
}

export interface VerifySummary {
  bundlesProcessed: number;
  signaturesVerified: number;
  signaturesFailed: number;
  signaturesMissing: number;
  chainLinksVerified: number;
  chainLinksFailed: number;
  lastChainPosition: number;
  status: 'verified' | 'partial' | 'failed';
}

interface DoneMsg {
  type: 'done';
  summary: VerifySummary;
}

interface ErrorMsg {
  type: 'error';
  message: string;
}

export type WorkerOutgoingMsg = ProgressMsg | DoneMsg | ErrorMsg;

// =============================================================================
// Worker state
// =============================================================================

const state = {
  publicKeys: [] as Uint8Array[],
  bundlesProcessed: 0,
  signaturesVerified: 0,
  signaturesFailed: 0,
  signaturesMissing: 0,
  chainLinksVerified: 0,
  chainLinksFailed: 0,
  lastChainPosition: 0,
  // We track the previous bundle's hash so prev_hash linkage can be
  // verified across batch boundaries. The very first batch sees an empty
  // prevBundleHash and skips that link (chain_position 1 has prev_hash = 64×'0').
  prevBundleHash: null as string | null,
};

const GENESIS_PREV_HASH = '0'.repeat(64);

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.replace(/[^0-9a-fA-F]/g, '');
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

async function sha256Hex(input: string): Promise<string> {
  const buf = new globalThis.TextEncoder().encode(input);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

// =============================================================================
// Per-batch verification
// =============================================================================

async function verifyBatch(bundles: WireBundle[]): Promise<void> {
  for (const b of bundles) {
    state.bundlesProcessed++;
    state.lastChainPosition = b.chain_position;

    // 1. chain_hash linkage check — SHA256(bundle_hash:prev_hash:chain_position)
    //    must match the stored chain_hash. This proves the chain was built
    //    correctly and hasn't been rewritten since.
    if (b.chain_hash) {
      const computed = await sha256Hex(
        `${b.bundle_hash}:${b.prev_hash || ''}:${b.chain_position}`,
      );
      if (computed === b.chain_hash) {
        state.chainLinksVerified++;
      } else {
        state.chainLinksFailed++;
      }
    }

    // 2. prev_hash linkage check — the bundle's prev_hash must match the
    //    PREVIOUS bundle's bundle_hash. For chain_position 1 the prev_hash
    //    must be the genesis (64 zeroes). This is the cross-batch invariant.
    if (state.prevBundleHash !== null) {
      if ((b.prev_hash || '') !== state.prevBundleHash) {
        state.chainLinksFailed++;
      } else {
        // Already counted as chainLinksVerified above if chain_hash was OK.
        // Don't double-count, but if chain_hash was missing, count this.
        if (!b.chain_hash) {
          state.chainLinksVerified++;
        }
      }
    } else if (b.chain_position === 1) {
      // First bundle in the chain — prev_hash must be genesis.
      if ((b.prev_hash || '') !== GENESIS_PREV_HASH) {
        state.chainLinksFailed++;
      }
    }
    state.prevBundleHash = b.bundle_hash;

    // 3. Ed25519 signature verification — try every public key until one
    //    verifies. A bundle may have been signed by any of the site's
    //    appliances. Legacy bundles have no signature.
    if (!b.agent_signature) {
      state.signaturesMissing++;
      continue;
    }

    try {
      const msg = hexToBytes(b.bundle_hash);
      const sig = hexToBytes(b.agent_signature);
      if (sig.length !== 64) {
        state.signaturesFailed++;
        continue;
      }

      let verified = false;
      for (const pub of state.publicKeys) {
        if (pub.length !== 32) continue;
        try {
          const ok = await ed.verifyAsync(sig, msg, pub);
          if (ok) {
            verified = true;
            break;
          }
        } catch {
          // invalid key, try next
        }
      }
      if (verified) {
        state.signaturesVerified++;
      } else {
        state.signaturesFailed++;
      }
    } catch {
      state.signaturesFailed++;
    }
  }
}

function snapshotProgress(): ProgressMsg {
  return {
    type: 'progress',
    bundlesProcessed: state.bundlesProcessed,
    signaturesVerified: state.signaturesVerified,
    signaturesFailed: state.signaturesFailed,
    signaturesMissing: state.signaturesMissing,
    chainLinksVerified: state.chainLinksVerified,
    chainLinksFailed: state.chainLinksFailed,
    lastChainPosition: state.lastChainPosition,
  };
}

function summarize(): VerifySummary {
  const status: VerifySummary['status'] =
    state.signaturesFailed > 0 || state.chainLinksFailed > 0
      ? 'failed'
      : state.signaturesVerified > 0 || state.chainLinksVerified > 0
        ? 'verified'
        : 'partial';
  return {
    bundlesProcessed: state.bundlesProcessed,
    signaturesVerified: state.signaturesVerified,
    signaturesFailed: state.signaturesFailed,
    signaturesMissing: state.signaturesMissing,
    chainLinksVerified: state.chainLinksVerified,
    chainLinksFailed: state.chainLinksFailed,
    lastChainPosition: state.lastChainPosition,
    status,
  };
}

// =============================================================================
// Message handler — typed against the DedicatedWorkerGlobalScope from `self`.
// `globalThis` is used to avoid `no-undef` lint warnings on `self` (which is
// a web-worker-only global not present in eslint's default browser env list).
// =============================================================================

interface DedicatedWorkerSelf {
  onmessage:
    | ((ev: globalThis.MessageEvent<IncomingMsg>) => void | Promise<void>)
    | null;
  postMessage(msg: WorkerOutgoingMsg): void;
}

const _self = globalThis as unknown as DedicatedWorkerSelf;

_self.onmessage = async (ev: globalThis.MessageEvent<IncomingMsg>) => {
  const msg = ev.data;
  try {
    if (msg.type === 'init') {
      state.publicKeys = msg.publicKeys
        .map((hex) => {
          try {
            return hexToBytes(hex);
          } catch {
            return new Uint8Array(0);
          }
        })
        .filter((k) => k.length === 32);
      return;
    }

    if (msg.type === 'batch') {
      await verifyBatch(msg.bundles);
      _self.postMessage(snapshotProgress());
      return;
    }

    if (msg.type === 'finalize') {
      const done: DoneMsg = { type: 'done', summary: summarize() };
      _self.postMessage(done);
      return;
    }
  } catch (e) {
    const err: ErrorMsg = {
      type: 'error',
      message: e instanceof Error ? e.message : String(e),
    };
    _self.postMessage(err);
  }
};

// Force the file to be a module so `self.onmessage` is typed correctly
// against DedicatedWorkerGlobalScope.
export {};
