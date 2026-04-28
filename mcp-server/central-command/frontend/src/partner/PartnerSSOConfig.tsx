import React, { useState, useEffect, useCallback } from 'react';
import { usePartner } from './PartnerContext';
import { SSO_LABELS } from '../constants';
import { csrfHeaders } from '../utils/csrf';

interface SSOConfig {
  issuer_url: string;
  client_id: string;
  client_secret: string;
  allowed_domains: string[];
  sso_enforced: boolean;
}

interface PartnerSSOConfigProps {
  orgId: string;
  orgName: string;
  onBack: () => void;
}

export const PartnerSSOConfig: React.FC<PartnerSSOConfigProps> = ({ orgId, orgName, onBack }) => {
  const { apiKey } = usePartner();

  const [issuerUrl, setIssuerUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [allowedDomains, setAllowedDomains] = useState('');
  const [ssoEnforced, setSsoEnforced] = useState(false);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [hasExisting, setHasExisting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  const fetchOptions = useCallback((): RequestInit => {
    // #182 (Session 211 Phase 3): always 'include' so GETs send the
    // session cookie, with X-API-Key additive when present. Pre-fix
    // the apiKey branch dropped to the default 'same-origin' which
    // silently skipped cookies — the additive form preserves
    // defense-in-depth (cookie + apiKey both present when both exist).
    return {
      credentials: 'include',
      headers: apiKey ? { 'X-API-Key': apiKey } : {},
    };
  }, [apiKey]);

  const fetchOptionsWithBody = useCallback((method: string, body: unknown): RequestInit => {
    // #182 follow-on (Session 211 Phase 3): canonical additive form.
    // Pre-fix the apiKey branch lost BOTH cookies AND CSRF — when a
    // partner had an apiKey, all mutations bypassed session-cookie
    // auth + CSRF entirely. Now: cookies + CSRF unconditional,
    // X-API-Key additive when present.
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...csrfHeaders(),
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    };
    return {
      method,
      headers,
      credentials: 'include',
      body: JSON.stringify(body),
    };
  }, [apiKey]);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const res = await fetch(`/api/partners/me/orgs/${orgId}/sso`, fetchOptions());
        if (res.ok) {
          const data: SSOConfig = await res.json();
          setIssuerUrl(data.issuer_url || '');
          setClientId(data.client_id || '');
          setClientSecret(''); // Never pre-fill secret
          setAllowedDomains((data.allowed_domains || []).join(', '));
          setSsoEnforced(data.sso_enforced || false);
          setHasExisting(true);
        } else if (res.status === 404) {
          setHasExisting(false);
        }
      } catch {
        // Load failed — form stays empty for fresh config
      } finally {
        setLoading(false);
      }
    };
    loadConfig();
  }, [orgId, fetchOptions]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);
    setValidationErrors({});

    // Client-side validation
    const errors: Record<string, string> = {};
    if (!issuerUrl.trim()) errors.issuer_url = 'Issuer URL is required.';
    if (!clientId.trim()) errors.client_id = 'Client ID is required.';
    if (!hasExisting && !clientSecret.trim()) errors.client_secret = 'Client Secret is required for new configurations.';

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }

    setSaving(true);
    try {
      const domains = allowedDomains
        .split(',')
        .map(d => d.trim())
        .filter(Boolean);

      const body: Record<string, unknown> = {
        issuer_url: issuerUrl.trim(),
        client_id: clientId.trim(),
        allowed_domains: domains,
        sso_enforced: ssoEnforced,
      };

      // Only send client_secret if provided (allows keeping existing secret)
      if (clientSecret.trim()) {
        body.client_secret = clientSecret.trim();
      }

      const res = await fetch(
        `/api/partners/me/orgs/${orgId}/sso`,
        fetchOptionsWithBody('PUT', body),
      );

      if (res.ok) {
        setMessage({ type: 'success', text: SSO_LABELS.sso_saved });
        setHasExisting(true);
        setClientSecret('');
      } else {
        const err = await res.json().catch(() => null);

        // Backend validation errors
        if (err?.validation_errors) {
          setValidationErrors(err.validation_errors);
        } else {
          setMessage({ type: 'error', text: err?.detail || 'Failed to save SSO configuration.' });
        }
      }
    } catch {
      setMessage({ type: 'error', text: 'Network error. Please try again.' });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(SSO_LABELS.sso_delete_confirm)) return;

    setDeleting(true);
    setMessage(null);

    try {
      const opts: RequestInit = apiKey
        ? { method: 'DELETE', headers: { 'X-API-Key': apiKey } }
        : { method: 'DELETE', credentials: 'include', headers: { ...csrfHeaders() } };

      const res = await fetch(`/api/partners/me/orgs/${orgId}/sso`, opts);
      if (res.ok) {
        setMessage({ type: 'success', text: SSO_LABELS.sso_deleted });
        setIssuerUrl('');
        setClientId('');
        setClientSecret('');
        setAllowedDomains('');
        setSsoEnforced(false);
        setHasExisting(false);
      } else {
        const err = await res.json().catch(() => null);
        setMessage({ type: 'error', text: err?.detail || 'Failed to remove SSO configuration.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'Network error. Please try again.' });
    } finally {
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 font-medium"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back
        </button>
        <div className="bg-white rounded-2xl p-8 shadow-sm border border-slate-100 animate-pulse">
          <div className="h-6 bg-slate-200 rounded w-1/3 mb-6" />
          <div className="space-y-4">
            <div className="h-10 bg-slate-100 rounded" />
            <div className="h-10 bg-slate-100 rounded" />
            <div className="h-10 bg-slate-100 rounded" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 font-medium"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to Sites
      </button>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-100">
          <h2 className="text-lg font-semibold text-slate-900">{SSO_LABELS.sso_config_title}</h2>
          <p className="text-sm text-slate-500 mt-1">
            {SSO_LABELS.sso_config_description} Organization: <strong>{orgName}</strong>
          </p>
        </div>

        <form onSubmit={handleSave} className="p-6 space-y-5">
          {message && (
            <div className={`p-4 rounded-lg border ${
              message.type === 'success'
                ? 'bg-emerald-50 border-emerald-200'
                : 'bg-red-50 border-red-200'
            }`}>
              <p className={`text-sm ${
                message.type === 'success' ? 'text-emerald-700' : 'text-red-700'
              }`}>{message.text}</p>
            </div>
          )}

          <div>
            <label htmlFor="issuerUrl" className="block text-sm font-medium text-slate-700 mb-1">
              {SSO_LABELS.sso_issuer_url}
            </label>
            <input
              type="url"
              id="issuerUrl"
              value={issuerUrl}
              onChange={(e) => { setIssuerUrl(e.target.value); setValidationErrors(prev => { const next = { ...prev }; delete next.issuer_url; return next; }); }}
              placeholder="https://login.microsoftonline.com/tenant-id/v2.0"
              className={`w-full px-4 py-2.5 bg-slate-50/80 border rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm ${
                validationErrors.issuer_url ? 'border-red-300' : 'border-slate-200'
              }`}
            />
            {validationErrors.issuer_url && (
              <p className="text-red-600 text-xs mt-1">{validationErrors.issuer_url}</p>
            )}
          </div>

          <div>
            <label htmlFor="clientId" className="block text-sm font-medium text-slate-700 mb-1">
              {SSO_LABELS.sso_client_id}
            </label>
            <input
              type="text"
              id="clientId"
              value={clientId}
              onChange={(e) => { setClientId(e.target.value); setValidationErrors(prev => { const next = { ...prev }; delete next.client_id; return next; }); }}
              placeholder="00000000-0000-0000-0000-000000000000"
              className={`w-full px-4 py-2.5 bg-slate-50/80 border rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm font-mono ${
                validationErrors.client_id ? 'border-red-300' : 'border-slate-200'
              }`}
            />
            {validationErrors.client_id && (
              <p className="text-red-600 text-xs mt-1">{validationErrors.client_id}</p>
            )}
          </div>

          <div>
            <label htmlFor="clientSecret" className="block text-sm font-medium text-slate-700 mb-1">
              {SSO_LABELS.sso_client_secret}
            </label>
            <input
              type="password"
              id="clientSecret"
              value={clientSecret}
              onChange={(e) => { setClientSecret(e.target.value); setValidationErrors(prev => { const next = { ...prev }; delete next.client_secret; return next; }); }}
              placeholder={hasExisting ? 'Leave blank to keep existing secret' : 'Enter client secret'}
              className={`w-full px-4 py-2.5 bg-slate-50/80 border rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm ${
                validationErrors.client_secret ? 'border-red-300' : 'border-slate-200'
              }`}
            />
            {validationErrors.client_secret && (
              <p className="text-red-600 text-xs mt-1">{validationErrors.client_secret}</p>
            )}
          </div>

          <div>
            <label htmlFor="allowedDomains" className="block text-sm font-medium text-slate-700 mb-1">
              {SSO_LABELS.sso_allowed_domains}
            </label>
            <input
              type="text"
              id="allowedDomains"
              value={allowedDomains}
              onChange={(e) => setAllowedDomains(e.target.value)}
              placeholder="example.com, clinic.org"
              className="w-full px-4 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
            />
            <p className="text-xs text-slate-500 mt-1">Comma-separated list of email domains allowed to use SSO.</p>
          </div>

          <div className="flex items-center justify-between py-3 px-4 bg-slate-50 rounded-xl">
            <div>
              <p className="text-sm font-medium text-slate-700">{SSO_LABELS.sso_enforced}</p>
              <p className="text-xs text-slate-500 mt-0.5">{SSO_LABELS.sso_enforced_help}</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={ssoEnforced}
              onClick={() => setSsoEnforced(!ssoEnforced)}
              className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out ${
                ssoEnforced ? 'bg-indigo-600' : 'bg-slate-200'
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                  ssoEnforced ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="px-6 py-2.5 text-white font-medium rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110 text-sm"
              style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', boxShadow: '0 4px 14px rgba(79, 70, 229, 0.35)' }}
            >
              {saving ? 'Saving...' : hasExisting ? 'Update Configuration' : 'Save Configuration'}
            </button>

            {hasExisting && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2.5 text-red-600 font-medium rounded-xl border border-red-200 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
              >
                {deleting ? 'Removing...' : 'Remove SSO'}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
};

export default PartnerSSOConfig;
