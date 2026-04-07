import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';
import { csrfHeaders } from '../utils/csrf';
import { CredentialEntryModal } from './CredentialEntryModal';

interface Alert {
  id: string;
  site_id: string;
  site_name: string;
  alert_type: string;
  summary: string;
  severity: string;
  created_at: string;
  status: string;
  incident_id: string | null;
  actions_available: boolean;
}

const ALERT_ICONS: Record<string, string> = {
  patch_available: '\u26A0',
  firewall_off: '\uD83D\uDEE1',
  service_stopped: '\u26D4',
  encryption_off: '\uD83D\uDD12',
  rogue_device: '\u2753',
  credential_needed: '\uD83D\uDD11',
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  high: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
  medium: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
  low: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
};

const ACTION_LABELS: Record<string, Record<string, string>> = {
  patch_available: { approve: 'Approve Patch', dismiss: 'Dismiss' },
  firewall_off: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  service_stopped: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  encryption_off: { approve: 'Approve Fix', dismiss: 'Dismiss' },
  rogue_device: { approve: 'Acknowledge', dismiss: 'Ignore' },
  credential_needed: { approve: 'Enter Credentials', dismiss: 'Dismiss' },
};

export const ClientAlerts: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useClient();

  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState<string | null>(null);
  const [credModal, setCredModal] = useState<{ siteId: string; siteName: string; alertId: string } | null>(null);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchAlerts();
    }
  }, [isAuthenticated]);

  const fetchAlerts = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/client/alerts', { credentials: 'same-origin' });
      if (response.ok) {
        const data = await response.json();
        setAlerts(data.alerts || []);
      }
    } catch (e) {
      console.error('Failed to fetch alerts:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (alertId: string, action: 'approved' | 'dismissed') => {
    setActioning(alertId + ':' + action);
    try {
      const response = await fetch(`/api/client/alerts/${alertId}/action`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders(),
        },
        credentials: 'same-origin',
        body: JSON.stringify({ action }),
      });
      if (response.ok) {
        setAlerts((prev) =>
          prev.map((a) =>
            a.id === alertId
              ? { ...a, status: action, actions_available: false }
              : a
          )
        );
      }
    } catch (e) {
      console.error('Failed to perform alert action:', e);
    } finally {
      setActioning(null);
    }
  };

  const pendingAlerts = alerts.filter(
    (a) => a.status !== 'dismissed' && a.status !== 'approved'
  );
  const resolvedAlerts = alerts.filter(
    (a) => a.status === 'dismissed' || a.status === 'approved'
  );

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const renderAlertCard = (alert: Alert, dimmed = false) => {
    const icon = ALERT_ICONS[alert.alert_type] || '\u2139';
    const severityClass = SEVERITY_COLORS[alert.severity] || SEVERITY_COLORS.low;
    const actionLabels = ACTION_LABELS[alert.alert_type] || { approve: 'Approve', dismiss: 'Dismiss' };
    const isActioning = actioning?.startsWith(alert.id);

    return (
      <div
        key={alert.id}
        className={`p-4 hover:bg-teal-50/30 transition-colors ${dimmed ? 'opacity-60' : ''}`}
      >
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-lg flex-shrink-0">
            {icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span
                className={`px-2 py-0.5 text-xs font-medium rounded-full ${severityClass}`}
              >
                {alert.severity}
              </span>
              <span className="text-xs text-slate-500 font-medium uppercase tracking-wide">
                {alert.alert_type.replace(/_/g, ' ')}
              </span>
            </div>
            <p className="text-sm font-medium text-slate-900">{alert.summary}</p>
            <div className="flex flex-wrap items-center gap-3 mt-1">
              <span className="text-xs text-slate-500">{alert.site_name}</span>
              <span className="text-xs text-slate-400">
                {new Date(alert.created_at).toLocaleString()}
              </span>
              {alert.status !== 'pending' && (
                <span className="text-xs text-slate-400 italic">{alert.status}</span>
              )}
            </div>
            {alert.actions_available && !dimmed && (
              <div className="flex gap-2 mt-3">
                {alert.alert_type === 'credential_needed' ? (
                  <button
                    onClick={() =>
                      setCredModal({
                        siteId: alert.site_id || '',
                        siteName: alert.site_name,
                        alertId: alert.id,
                      })
                    }
                    disabled={!!isActioning}
                    className="px-3 py-1.5 text-xs font-medium bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50 transition-colors"
                  >
                    {actionLabels.approve}
                  </button>
                ) : (
                  <button
                    onClick={() => handleAction(alert.id, 'approved')}
                    disabled={!!isActioning}
                    className="px-3 py-1.5 text-xs font-medium bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50 transition-colors"
                  >
                    {actioning === alert.id + ':approved' ? 'Working...' : actionLabels.approve}
                  </button>
                )}
                <button
                  onClick={() => handleAction(alert.id, 'dismissed')}
                  disabled={!!isActioning}
                  className="px-3 py-1.5 text-xs font-medium bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 disabled:opacity-50 transition-colors"
                >
                  {actioning === alert.id + ':dismissed' ? 'Working...' : actionLabels.dismiss}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header
        className="sticky top-0 z-30 border-b border-slate-200/60"
        style={{
          background: 'rgba(255,255,255,0.82)',
          backdropFilter: 'blur(20px) saturate(180%)',
          WebkitBackdropFilter: 'blur(20px) saturate(180%)',
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-4">
              <Link
                to="/client/dashboard"
                className="p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </Link>
              <div>
                <h1 className="text-lg font-semibold text-slate-900">Alerts</h1>
              </div>
              {pendingAlerts.length > 0 && (
                <span className="px-2 py-1 text-xs font-medium bg-orange-100 text-orange-700 rounded-full">
                  {pendingAlerts.length} pending
                </span>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Credential Entry Modal */}
      {credModal && (
        <CredentialEntryModal
          isOpen={true}
          onClose={() => {
            setCredModal(null);
            fetchAlerts();
          }}
          siteId={credModal.siteId}
          siteName={credModal.siteName}
          alertId={credModal.alertId}
        />
      )}

      {/* Main */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {loading ? (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8 text-center">
            <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
          </div>
        ) : (
          <>
            {/* Pending alerts */}
            <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100">
                <h2 className="text-sm font-semibold text-slate-700">Pending</h2>
              </div>
              {pendingAlerts.length === 0 ? (
                <div className="p-6 text-center">
                  <div className="inline-flex items-center gap-2 px-4 py-3 bg-green-50 text-green-700 rounded-lg text-sm font-medium border border-green-200">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    All clear — no pending alerts.
                  </div>
                </div>
              ) : (
                <div className="divide-y divide-slate-100">
                  {pendingAlerts.map((alert) => renderAlertCard(alert, false))}
                </div>
              )}
            </div>

            {/* Resolved alerts */}
            {resolvedAlerts.length > 0 && (
              <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-100">
                  <h2 className="text-sm font-semibold text-slate-500">Resolved</h2>
                </div>
                <div className="divide-y divide-slate-100">
                  {resolvedAlerts.map((alert) => renderAlertCard(alert, true))}
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
};

export default ClientAlerts;
