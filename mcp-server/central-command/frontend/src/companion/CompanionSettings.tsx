import React, { useState, useEffect } from 'react';
import { companionColors, companionShadows } from './companion-tokens';
import { csrfHeaders } from '../utils/csrf';

interface CompanionPrefs {
  display_name: string;
  email_notifications: boolean;
  alert_digest: 'immediate' | 'daily' | 'weekly';
}

export const CompanionSettings: React.FC = () => {
  const [prefs, setPrefs] = useState<CompanionPrefs>({
    display_name: '',
    email_notifications: true,
    alert_digest: 'daily',
  });
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<{ email: string; display_name: string } | null>(null);

  useEffect(() => {
    // Fetch current user profile
    fetch('/api/companion/me', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setCurrentUser(data);
          setPrefs(p => ({
            ...p,
            display_name: data.display_name || data.email || '',
            email_notifications: data.email_notifications ?? true,
            alert_digest: data.alert_digest || 'daily',
          }));
        }
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await fetch('/api/companion/me/preferences', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify(prefs),
      });
      if (res.ok) {
        setSaveMsg('Settings saved');
        setTimeout(() => setSaveMsg(null), 3000);
      } else {
        setSaveMsg('Failed to save');
      }
    } catch {
      setSaveMsg('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const cardStyle: React.CSSProperties = {
    background: companionColors.cardBg,
    border: `1px solid ${companionColors.cardBorder}`,
    borderRadius: 12,
    boxShadow: companionShadows.sm,
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 12px',
    borderRadius: 8,
    border: `1px solid ${companionColors.cardBorder}`,
    background: companionColors.pageBg,
    color: companionColors.textPrimary,
    fontSize: 14,
    outline: 'none',
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-6" style={{ color: companionColors.textPrimary }}>
        Settings
      </h1>

      {/* Profile */}
      <div style={cardStyle} className="p-5 mb-4">
        <h2 className="text-base font-medium mb-4" style={{ color: companionColors.textPrimary }}>
          Profile
        </h2>

        <div className="space-y-4">
          <div>
            <label className="block text-sm mb-1" style={{ color: companionColors.textSecondary }}>
              Email
            </label>
            <div className="text-sm font-medium" style={{ color: companionColors.textPrimary }}>
              {currentUser?.email || '—'}
            </div>
          </div>

          <div>
            <label className="block text-sm mb-1" style={{ color: companionColors.textSecondary }}>
              Display Name
            </label>
            <input
              type="text"
              value={prefs.display_name}
              onChange={e => setPrefs({ ...prefs, display_name: e.target.value })}
              style={inputStyle}
              placeholder="Your name"
            />
          </div>
        </div>
      </div>

      {/* Notifications */}
      <div style={cardStyle} className="p-5 mb-4">
        <h2 className="text-base font-medium mb-4" style={{ color: companionColors.textPrimary }}>
          Notifications
        </h2>

        <div className="space-y-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={prefs.email_notifications}
              onChange={e => setPrefs({ ...prefs, email_notifications: e.target.checked })}
              className="rounded"
              style={{ accentColor: companionColors.primary }}
            />
            <div>
              <div className="text-sm font-medium" style={{ color: companionColors.textPrimary }}>
                Email notifications
              </div>
              <div className="text-xs" style={{ color: companionColors.textTertiary }}>
                Receive alerts when compliance items need attention
              </div>
            </div>
          </label>

          <div>
            <label className="block text-sm mb-1" style={{ color: companionColors.textSecondary }}>
              Alert Digest Frequency
            </label>
            <select
              value={prefs.alert_digest}
              onChange={e => setPrefs({ ...prefs, alert_digest: e.target.value as CompanionPrefs['alert_digest'] })}
              style={inputStyle}
            >
              <option value="immediate">Immediate (each alert)</option>
              <option value="daily">Daily digest</option>
              <option value="weekly">Weekly digest</option>
            </select>
          </div>
        </div>
      </div>

      {/* About */}
      <div style={cardStyle} className="p-5 mb-6">
        <h2 className="text-base font-medium mb-3" style={{ color: companionColors.textPrimary }}>
          About
        </h2>
        <div className="space-y-1 text-sm" style={{ color: companionColors.textSecondary }}>
          <div>OsirisCare Compliance Companion</div>
          <div>Role: Compliance Companion</div>
          <div className="text-xs" style={{ color: companionColors.textTertiary }}>
            Companions help client organizations navigate their HIPAA compliance journey.
            For questions, contact your administrator.
          </div>
        </div>
      </div>

      {/* Save */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 text-sm font-medium text-white rounded-lg transition-opacity hover:opacity-90 disabled:opacity-50"
          style={{ background: companionColors.primary }}
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        {saveMsg && (
          <span
            className="text-sm font-medium"
            style={{ color: saveMsg === 'Settings saved' ? companionColors.complete : companionColors.actionNeeded }}
          >
            {saveMsg}
          </span>
        )}
      </div>
    </div>
  );
};
