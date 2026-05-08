/**
 * PartnerSiteDetail — Sprint-N+2 D1+D2.
 *
 * Round-table .agent/plans/37-partner-per-site-drill-down-roundtable-
 * 2026-05-08.md. Bookmarkable per-site partner drill-down at
 * /partner/site/:siteId. Reuses safe-by-shape composed components
 * (no admin-route Links / admin-only mutations); admin-only modals
 * (Move/Transfer/Decommission/PortalLink/AddCredential/EditSite) are
 * intentionally NOT imported — partner-side mutations stay in their
 * existing flows (PartnerComplianceSettings credentials, etc.).
 *
 * Drift-finding (Gate 2 reservation): admin SiteHeader + SiteCompliance
 * Hero + SiteActivityTimeline contain admin-route Links / admin-route
 * fetch URLs that would 401 a partner. Plan 37's "PULL: SiteHeader,
 * SiteComplianceHero, SiteActivityTimeline" is therefore retired in
 * favor of a partner-context-aware header + a partner-scoped activity
 * feed implemented inline. Component-allowlist test reflects this:
 * EvidenceChainStatus + OnboardingProgress + ApplianceCard are
 * allowlisted (no admin-route deps); SiteHeader/SiteComplianceHero/
 * SiteActivityTimeline are NOT (admin-route leak class).
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { DangerousActionModal } from '../components/composed/DangerousActionModal';
import { postJson, getJson } from '../utils/portalFetch';

interface SiteAppliance {
  appliance_id: string;
  hostname: string;
  display_name: string;
  status: string;
  agent_version: string | null;
  last_checkin: string | null;
}

interface SiteDetail {
  site_id: string;
  clinic_name: string;
  status: string;
  tier: string | null;
  onboarding_stage: string | null;
  partner_brand: string | null;
}

interface SiteDetailResponse {
  site: SiteDetail;
  appliances?: SiteAppliance[];
  asset_count: number;
  credential_count: number;
}

interface ActivityEvent {
  event_id: string;
  at: string;
  action: string;
  actor: string | null;
  details: Record<string, unknown>;
  kind: 'partner_action' | 'attestation' | 'fleet_order' | 'incident' | 'other';
}

interface MintLinkResponse {
  url: string;
  expires_at: string;
  magic_link_id: string | null;
  attestation_bundle_id: string | null;
  attestation_hash: string | null;
}

function formatTimeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function CountdownBadge({ expiresAt }: { expiresAt: string }): React.ReactElement {
  const [remaining, setRemaining] = useState<number>(() => {
    return Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
  });
  useEffect(() => {
    const id = window.setInterval(() => {
      setRemaining(
        Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000)),
      );
    }, 1000);
    return () => window.clearInterval(id);
  }, [expiresAt]);
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full ${
        remaining > 60
          ? 'bg-emerald-100 text-emerald-800'
          : remaining > 0
          ? 'bg-amber-100 text-amber-800'
          : 'bg-rose-100 text-rose-800'
      }`}
      data-testid="magic-link-countdown"
    >
      {remaining > 0
        ? `expires in ${m}:${s.toString().padStart(2, '0')}`
        : 'expired'}
    </span>
  );
}

export const PartnerSiteDetail: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const { partner, isAuthenticated, isLoading: authLoading } = usePartner();
  const [data, setData] = useState<SiteDetailResponse | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Magic-link mint state
  const [mintModalOpen, setMintModalOpen] = useState(false);
  const [mintReason, setMintReason] = useState('');
  const [mintBusy, setMintBusy] = useState(false);
  const [mintError, setMintError] = useState<string | null>(null);
  const [mintResult, setMintResult] = useState<MintLinkResponse | null>(null);
  const [copied, setCopied] = useState(false);

  // Auth gate — redirect billing-only or unauthenticated to login.
  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      navigate('/partner/login');
    }
  }, [authLoading, isAuthenticated, navigate]);

  const role = partner?.user_role ?? null;
  const canMint = role === 'admin' || role === 'tech';

  const loadSite = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    setError(null);
    try {
      const detail = await getJson<SiteDetailResponse>(
        `/api/partners/me/sites/${encodeURIComponent(siteId)}`,
      );
      if (!detail) {
        setError('Site not found.');
        setLoading(false);
        return;
      }
      setData(detail);
      // Activity feed (last 30 days, partner-scoped). Endpoint reused
      // from existing partner activity log filtered server-side by
      // target=site:<siteId>; we render a thin client-side filter for
      // safety until a dedicated /me/sites/:id/activity ships.
      try {
        const act = await getJson<{ events: ActivityEvent[] }>(
          `/api/partners/me/audit-log?target=site:${encodeURIComponent(siteId)}&days=30&limit=50`,
        );
        setActivity(act?.events ?? []);
      } catch {
        setActivity([]);
      }
    } catch (e) {
      const err = e as { status?: number; message?: string };
      if (err.status === 401) {
        navigate('/partner/login');
        return;
      }
      setError(err.message || 'Failed to load site.');
    } finally {
      setLoading(false);
    }
  }, [siteId, navigate]);

  useEffect(() => {
    if (siteId && isAuthenticated) {
      loadSite();
    }
  }, [siteId, isAuthenticated, loadSite]);

  const onSubmitMint = useCallback(async () => {
    if (!siteId) return;
    if (mintReason.trim().length < 20) {
      setMintError('Reason must be at least 20 characters.');
      return;
    }
    setMintBusy(true);
    setMintError(null);
    try {
      const result = await postJson<MintLinkResponse>(
        `/api/partners/me/sites/${encodeURIComponent(siteId)}/client-portal-link`,
        { reason: mintReason.trim() },
      );
      setMintResult(result);
    } catch (e) {
      const err = e as { status?: number; message?: string; detail?: string };
      if (err.status === 401) {
        navigate('/partner/login');
        return;
      }
      if (err.status === 429) {
        setMintError(
          err.detail ||
            'Magic-link mint rate-limited. Try again later.',
        );
      } else if (err.status === 403) {
        setMintError(
          'Permission denied. Only admin or tech roles may mint a client-portal link.',
        );
      } else {
        setMintError(err.detail || err.message || 'Failed to mint magic link.');
      }
    } finally {
      setMintBusy(false);
    }
  }, [siteId, mintReason, navigate]);

  const onCloseMintModal = useCallback(() => {
    setMintModalOpen(false);
    setMintReason('');
    setMintError(null);
    setMintResult(null);
    setCopied(false);
  }, []);

  const onCopyMintUrl = useCallback(async () => {
    if (!mintResult?.url) return;
    try {
      await navigator.clipboard.writeText(mintResult.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard API unavailable — surface to user
      setMintError('Could not copy to clipboard. Select and copy manually.');
    }
  }, [mintResult]);

  if (authLoading) {
    return (
      <div className="p-6 text-sm text-slate-600" data-testid="auth-loading">
        Checking authentication…
      </div>
    );
  }

  if (!isAuthenticated) {
    return null; // useEffect navigates
  }

  if (loading) {
    return (
      <div className="p-6 text-sm text-slate-600" data-testid="site-loading">
        Loading clinic detail…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6" role="alert" data-testid="site-error">
        <h1 className="text-lg font-semibold text-slate-900">Clinic not found</h1>
        <p className="text-sm text-slate-600 mt-2">
          {error || 'No data for this site.'}
        </p>
        <button
          onClick={() => navigate('/partner/dashboard')}
          className="mt-4 px-3 py-1.5 text-sm bg-slate-100 hover:bg-slate-200 rounded"
        >
          Back to dashboard
        </button>
      </div>
    );
  }

  const { site, appliances = [] } = data;
  const onlineAppliances = appliances.filter((a) => a.status === 'online').length;

  return (
    <div className="p-6 space-y-6" data-testid="partner-site-detail">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="text-xs text-slate-500 flex items-center gap-1.5">
        <Link to="/partner/dashboard" className="hover:text-slate-800">
          Clinics
        </Link>
        <span aria-hidden>/</span>
        <span className="text-slate-700">{site.clinic_name}</span>
      </nav>

      {/* Partner-scoped header (intentionally NOT importing admin SiteHeader —
          drift-class admin-route Links would 401 partners). */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">
              {site.clinic_name}
            </h1>
            <p className="text-xs text-slate-500 mt-1">
              site_id: <code className="font-mono">{site.site_id}</code>
              {site.tier && (
                <>
                  {' · tier '}
                  <span className="font-medium">{site.tier}</span>
                </>
              )}
              {site.partner_brand && (
                <>
                  {' · presented as '}
                  <span className="font-medium">{site.partner_brand}</span>
                </>
              )}
            </p>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 text-xs font-medium rounded-full ${
              site.status === 'inactive'
                ? 'bg-rose-100 text-rose-800'
                : 'bg-emerald-100 text-emerald-800'
            }`}
          >
            {site.status === 'inactive' ? 'Decommissioned' : 'Active'}
          </span>
        </div>

        {/* Sub-route nav */}
        <nav className="flex items-center gap-1.5 mt-4 pt-3 border-t border-slate-100 flex-wrap">
          <Link
            to={`/partner/site/${site.site_id}/agents`}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Workstation agents
          </Link>
          <Link
            to={`/partner/site/${site.site_id}/devices`}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Devices
          </Link>
          <Link
            to={`/partner/site/${site.site_id}/drift-config`}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Check config
          </Link>
          <Link
            to={`/partner/site/${site.site_id}/topology`}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Mesh topology
          </Link>
          <Link
            to={`/partner/site/${site.site_id}/consent`}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Consent
          </Link>
        </nav>
      </div>

      {/* Inventory summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-500">Appliances</p>
          <p className="text-2xl font-semibold text-slate-900 mt-1">
            {onlineAppliances}
            <span className="text-base font-normal text-slate-500"> / {appliances.length} online</span>
          </p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-500">Discovered assets</p>
          <p className="text-2xl font-semibold text-slate-900 mt-1">{data.asset_count}</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <p className="text-xs uppercase tracking-wide text-slate-500">Site credentials</p>
          <p className="text-2xl font-semibold text-slate-900 mt-1">{data.credential_count}</p>
        </div>
      </div>

      {/* Appliances list (no admin-only mutations — read-only summary) */}
      {appliances.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
          <div className="px-5 py-3 border-b border-slate-100">
            <h2 className="text-sm font-semibold text-slate-900">Appliances</h2>
          </div>
          <div className="divide-y divide-slate-100">
            {appliances.map((a) => (
              <div key={a.appliance_id} className="px-5 py-3 flex items-center justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-900 truncate">
                    {a.display_name || a.hostname}
                  </p>
                  <p className="text-xs text-slate-500">
                    {a.agent_version || 'unknown version'} · last check-in {formatTimeAgo(a.last_checkin)}
                  </p>
                </div>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                    a.status === 'online'
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {a.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cross-portal access (D4 magic-link mint) + deep links */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Cross-portal access</h2>
          <p className="text-xs text-slate-600 mt-1">
            Open this clinic's portal as the practice owner. The mint
            event is recorded in the cryptographic audit chain and the
            link expires in 15 minutes.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setMintModalOpen(true)}
            disabled={!canMint}
            className="px-3 py-1.5 text-sm rounded-md bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white"
            data-testid="open-mint-modal"
          >
            Open client portal as practice owner
          </button>
          {!canMint && (
            <span className="text-xs text-slate-500">
              admin or tech role required
            </span>
          )}
          <a
            href={`/client/login?site=${encodeURIComponent(site.site_id)}&intent=letter`}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Issue Compliance Letter →
          </a>
          <a
            href={`/client/login?site=${encodeURIComponent(site.site_id)}&intent=wall_cert`}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
          >
            Issue Wall Certificate →
          </a>
        </div>
      </div>

      {/* Recent privileged-access events for this site (partner-scope) */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">
            Recent privileged-access events
          </h2>
          <span className="text-xs text-slate-500">last 30 days</span>
        </div>
        {activity === null ? (
          <div className="px-5 py-4 text-xs text-slate-500">Loading…</div>
        ) : activity.length === 0 ? (
          <div className="px-5 py-4 text-xs text-slate-500">
            No partner-scoped activity in the last 30 days.
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {activity.slice(0, 25).map((evt) => (
              <li key={evt.event_id} className="px-5 py-2.5 flex items-start gap-3">
                <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-900 truncate">
                    {evt.action}
                  </p>
                  <p className="text-xs text-slate-500">
                    {formatTimeAgo(evt.at)}
                    {evt.actor && <> · {evt.actor}</>}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Mint modal — DangerousActionModal "reversible" tier (per plan
          37 D4: typed-confirm not needed since the link expires; the
          chain attestation is the audit). */}
      {mintModalOpen && !mintResult && (
        <DangerousActionModal
          open={mintModalOpen}
          tier="reversible"
          title="Mint client-portal magic link"
          verb="Mint"
          target={site.clinic_name}
          description={
            <div className="space-y-3 text-sm text-slate-700">
              <p>
                Open this site's client portal as the practice owner.
                This action is logged to the cryptographic audit chain
                and the link expires in 15 minutes.
              </p>
              <label className="block text-xs font-medium text-slate-600">
                Reason (≥ 20 characters)
                <textarea
                  value={mintReason}
                  onChange={(e) => setMintReason(e.target.value)}
                  rows={3}
                  className="mt-1 w-full px-2 py-1.5 text-sm border border-slate-300 rounded-md font-normal text-slate-900"
                  placeholder="e.g. Customer support call: triage 'failing patching' card with practice owner"
                  data-testid="mint-reason-input"
                />
                <span
                  className={`text-[11px] mt-1 block ${
                    mintReason.trim().length >= 20
                      ? 'text-emerald-700'
                      : 'text-slate-500'
                  }`}
                >
                  {mintReason.trim().length} / 20 characters
                </span>
              </label>
            </div>
          }
          busy={mintBusy}
          errorMessage={mintError ?? undefined}
          onConfirm={onSubmitMint}
          onCancel={onCloseMintModal}
        />
      )}

      {/* Result panel — copy URL + expiry countdown + chain hash */}
      {mintResult && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Magic link minted"
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
        >
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 space-y-4">
            <h2 className="text-lg font-semibold text-slate-900">
              Magic link minted
            </h2>
            <p className="text-sm text-slate-600">
              Logged to the cryptographic audit chain. The link expires
              in 15 minutes and works once.
            </p>
            <div className="bg-slate-50 border border-slate-200 rounded-md p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-slate-500">
                  Magic link
                </span>
                <CountdownBadge expiresAt={mintResult.expires_at} />
              </div>
              <code
                className="block text-xs text-slate-800 break-all font-mono"
                data-testid="mint-url"
              >
                {mintResult.url}
              </code>
              <button
                type="button"
                onClick={onCopyMintUrl}
                className="w-full px-3 py-1.5 text-sm rounded-md bg-blue-600 hover:bg-blue-700 text-white"
                data-testid="copy-mint-url"
              >
                {copied ? 'Copied!' : 'Copy to clipboard'}
              </button>
            </div>
            {mintResult.attestation_hash && (
              <p className="text-[11px] text-slate-500 font-mono break-all">
                attestation_hash: {mintResult.attestation_hash}
              </p>
            )}
            <button
              type="button"
              onClick={onCloseMintModal}
              className="w-full px-3 py-1.5 text-sm rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700"
              data-testid="close-mint-result"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerSiteDetail;
