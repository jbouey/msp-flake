import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { GlassCard, Spinner } from '../components/shared';

interface SystemSettings {
  // Display
  timezone: string;
  date_format: string;

  // Session
  session_timeout_minutes: number;
  require_2fa: boolean;

  // Fleet
  auto_update_enabled: boolean;
  update_window_start: string;
  update_window_end: string;
  rollout_percentage: number;

  // Data Retention
  telemetry_retention_days: number;
  incident_retention_days: number;
  audit_log_retention_days: number;

  // Notifications
  email_notifications_enabled: boolean;
  slack_notifications_enabled: boolean;
  escalation_timeout_minutes: number;

  // API
  api_rate_limit: number;
  webhook_timeout_seconds: number;
}

const defaultSettings: SystemSettings = {
  timezone: 'America/New_York',
  date_format: 'MM/DD/YYYY',
  session_timeout_minutes: 60,
  require_2fa: false,
  auto_update_enabled: true,
  update_window_start: '02:00',
  update_window_end: '06:00',
  rollout_percentage: 5,
  telemetry_retention_days: 90,
  incident_retention_days: 365,
  audit_log_retention_days: 730,
  email_notifications_enabled: true,
  slack_notifications_enabled: false,
  escalation_timeout_minutes: 60,
  api_rate_limit: 100,
  webhook_timeout_seconds: 30,
};

const timezones = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Phoenix',
  'UTC',
  'Europe/London',
  'Europe/Paris',
];

const dateFormats = [
  { value: 'MM/DD/YYYY', label: 'MM/DD/YYYY (US)' },
  { value: 'DD/MM/YYYY', label: 'DD/MM/YYYY (EU)' },
  { value: 'YYYY-MM-DD', label: 'YYYY-MM-DD (ISO)' },
];

const SettingSection: React.FC<{
  title: string;
  description: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, description, icon, children }) => (
  <GlassCard className="p-6">
    <div className="flex items-start gap-4 mb-6">
      <div className="w-10 h-10 rounded-ios bg-accent-primary/10 flex items-center justify-center text-accent-primary">
        {icon}
      </div>
      <div>
        <h3 className="font-semibold text-label-primary">{title}</h3>
        <p className="text-sm text-label-tertiary">{description}</p>
      </div>
    </div>
    <div className="space-y-4 pl-14">
      {children}
    </div>
  </GlassCard>
);

const ToggleSetting: React.FC<{
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}> = ({ label, description, checked, onChange, disabled }) => (
  <label className={`flex items-center justify-between py-2 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
    <div>
      <span className="text-sm font-medium text-label-primary">{label}</span>
      {description && <p className="text-xs text-label-tertiary mt-0.5">{description}</p>}
    </div>
    <div className="relative">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="sr-only peer"
      />
      <div className="w-11 h-6 bg-fill-tertiary peer-focus:ring-2 peer-focus:ring-accent-primary/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-accent-primary"></div>
    </div>
  </label>
);

const SelectSetting: React.FC<{
  label: string;
  description?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}> = ({ label, description, value, onChange, options }) => (
  <div className="flex items-center justify-between py-2">
    <div>
      <span className="text-sm font-medium text-label-primary">{label}</span>
      {description && <p className="text-xs text-label-tertiary mt-0.5">{description}</p>}
    </div>
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-1.5 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  </div>
);

const NumberSetting: React.FC<{
  label: string;
  description?: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
}> = ({ label, description, value, onChange, min, max, step = 1, suffix }) => (
  <div className="flex items-center justify-between py-2">
    <div>
      <span className="text-sm font-medium text-label-primary">{label}</span>
      {description && <p className="text-xs text-label-tertiary mt-0.5">{description}</p>}
    </div>
    <div className="flex items-center gap-2">
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value) || 0)}
        min={min}
        max={max}
        step={step}
        className="w-24 px-3 py-1.5 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary text-right"
      />
      {suffix && <span className="text-sm text-label-tertiary">{suffix}</span>}
    </div>
  </div>
);

const TimeSetting: React.FC<{
  label: string;
  description?: string;
  value: string;
  onChange: (value: string) => void;
}> = ({ label, description, value, onChange }) => (
  <div className="flex items-center justify-between py-2">
    <div>
      <span className="text-sm font-medium text-label-primary">{label}</span>
      {description && <p className="text-xs text-label-tertiary mt-0.5">{description}</p>}
    </div>
    <input
      type="time"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-1.5 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary"
    />
  </div>
);

export const Settings: React.FC = () => {
  const { user } = useAuth();
  const [settings, setSettings] = useState<SystemSettings>(defaultSettings);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const token = localStorage.getItem('auth_token');
      const response = await fetch('/api/dashboard/admin/settings', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setSettings({ ...defaultSettings, ...data });
      }
    } catch (error) {
      console.error('Failed to load settings:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const saveSettings = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    try {
      const token = localStorage.getItem('auth_token');
      const response = await fetch('/api/dashboard/admin/settings', {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(settings),
      });
      if (response.ok) {
        setSaveMessage({ type: 'success', text: 'Settings saved successfully' });
        setHasChanges(false);
      } else {
        const error = await response.json();
        setSaveMessage({ type: 'error', text: error.detail || 'Failed to save settings' });
      }
    } catch (error) {
      setSaveMessage({ type: 'error', text: 'Failed to save settings' });
    } finally {
      setIsSaving(false);
    }
  };

  const updateSetting = <K extends keyof SystemSettings>(key: K, value: SystemSettings[K]) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setHasChanges(true);
    setSaveMessage(null);
  };

  if (user?.role !== 'admin') {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-red-600">Access Denied</h1>
        <p className="text-label-tertiary mt-2">You need admin privileges to access system settings.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Settings</h1>
          <p className="text-label-tertiary text-sm mt-1">
            Configure system-wide preferences and behavior
          </p>
        </div>
        <button
          onClick={saveSettings}
          disabled={isSaving || !hasChanges}
          className="px-4 py-2 text-sm rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isSaving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      {/* Save Message */}
      {saveMessage && (
        <div className={`p-4 rounded-ios ${
          saveMessage.type === 'success'
            ? 'bg-green-500/10 text-green-600 border border-green-500/20'
            : 'bg-red-500/10 text-red-600 border border-red-500/20'
        }`}>
          {saveMessage.text}
        </div>
      )}

      {/* Display Settings */}
      <SettingSection
        title="Display"
        description="Configure timezone and date formatting"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      >
        <SelectSetting
          label="Timezone"
          description="Used for scheduling and display"
          value={settings.timezone}
          onChange={(v) => updateSetting('timezone', v)}
          options={timezones.map((tz) => ({ value: tz, label: tz }))}
        />
        <SelectSetting
          label="Date Format"
          value={settings.date_format}
          onChange={(v) => updateSetting('date_format', v)}
          options={dateFormats}
        />
      </SettingSection>

      {/* Security Settings */}
      <SettingSection
        title="Security"
        description="Session and authentication settings"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        }
      >
        <NumberSetting
          label="Session Timeout"
          description="Auto-logout after inactivity"
          value={settings.session_timeout_minutes}
          onChange={(v) => updateSetting('session_timeout_minutes', v)}
          min={5}
          max={480}
          suffix="minutes"
        />
        <ToggleSetting
          label="Require Two-Factor Authentication"
          description="Enforce 2FA for all admin users"
          checked={settings.require_2fa}
          onChange={(v) => updateSetting('require_2fa', v)}
        />
      </SettingSection>

      {/* Fleet Update Settings */}
      <SettingSection
        title="Fleet Updates"
        description="Configure automatic update behavior"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
        }
      >
        <ToggleSetting
          label="Automatic Updates"
          description="Allow appliances to auto-update within maintenance window"
          checked={settings.auto_update_enabled}
          onChange={(v) => updateSetting('auto_update_enabled', v)}
        />
        <TimeSetting
          label="Maintenance Window Start"
          value={settings.update_window_start}
          onChange={(v) => updateSetting('update_window_start', v)}
        />
        <TimeSetting
          label="Maintenance Window End"
          value={settings.update_window_end}
          onChange={(v) => updateSetting('update_window_end', v)}
        />
        <NumberSetting
          label="Default Rollout Percentage"
          description="Initial percentage for staged rollouts"
          value={settings.rollout_percentage}
          onChange={(v) => updateSetting('rollout_percentage', v)}
          min={1}
          max={100}
          suffix="%"
        />
      </SettingSection>

      {/* Data Retention Settings */}
      <SettingSection
        title="Data Retention"
        description="Configure how long data is kept"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
          </svg>
        }
      >
        <NumberSetting
          label="Telemetry Retention"
          description="Execution telemetry and metrics"
          value={settings.telemetry_retention_days}
          onChange={(v) => updateSetting('telemetry_retention_days', v)}
          min={30}
          max={365}
          suffix="days"
        />
        <NumberSetting
          label="Incident Retention"
          description="Incident history and resolution data"
          value={settings.incident_retention_days}
          onChange={(v) => updateSetting('incident_retention_days', v)}
          min={90}
          max={1825}
          suffix="days"
        />
        <NumberSetting
          label="Audit Log Retention"
          description="Admin actions and system events"
          value={settings.audit_log_retention_days}
          onChange={(v) => updateSetting('audit_log_retention_days', v)}
          min={365}
          max={2555}
          suffix="days"
        />
      </SettingSection>

      {/* Notification Settings */}
      <SettingSection
        title="Notifications"
        description="Global notification preferences"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
        }
      >
        <ToggleSetting
          label="Email Notifications"
          description="Send L3 escalations via email"
          checked={settings.email_notifications_enabled}
          onChange={(v) => updateSetting('email_notifications_enabled', v)}
        />
        <ToggleSetting
          label="Slack Notifications"
          description="Post alerts to Slack channels"
          checked={settings.slack_notifications_enabled}
          onChange={(v) => updateSetting('slack_notifications_enabled', v)}
        />
        <NumberSetting
          label="Escalation Timeout"
          description="Re-notify if not acknowledged"
          value={settings.escalation_timeout_minutes}
          onChange={(v) => updateSetting('escalation_timeout_minutes', v)}
          min={5}
          max={1440}
          suffix="minutes"
        />
      </SettingSection>

      {/* API Settings */}
      <SettingSection
        title="API"
        description="Rate limiting and webhook configuration"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
          </svg>
        }
      >
        <NumberSetting
          label="API Rate Limit"
          description="Requests per minute per client"
          value={settings.api_rate_limit}
          onChange={(v) => updateSetting('api_rate_limit', v)}
          min={10}
          max={1000}
          suffix="req/min"
        />
        <NumberSetting
          label="Webhook Timeout"
          description="Max wait time for webhook responses"
          value={settings.webhook_timeout_seconds}
          onChange={(v) => updateSetting('webhook_timeout_seconds', v)}
          min={5}
          max={120}
          suffix="seconds"
        />
      </SettingSection>

      {/* Danger Zone */}
      <GlassCard className="p-6 border-red-500/20">
        <div className="flex items-start gap-4 mb-6">
          <div className="w-10 h-10 rounded-ios bg-red-500/10 flex items-center justify-center text-red-500">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <h3 className="font-semibold text-label-primary">Danger Zone</h3>
            <p className="text-sm text-label-tertiary">Irreversible actions</p>
          </div>
        </div>
        <div className="space-y-4 pl-14">
          <div className="flex items-center justify-between py-2">
            <div>
              <span className="text-sm font-medium text-label-primary">Purge Old Telemetry</span>
              <p className="text-xs text-label-tertiary mt-0.5">Delete telemetry older than retention period</p>
            </div>
            <button className="px-3 py-1.5 text-sm rounded-ios bg-red-500/10 text-red-600 border border-red-500/20 hover:bg-red-500/20 transition-colors">
              Purge Now
            </button>
          </div>
          <div className="flex items-center justify-between py-2">
            <div>
              <span className="text-sm font-medium text-label-primary">Reset Learning Data</span>
              <p className="text-xs text-label-tertiary mt-0.5">Clear all patterns and L1 rules (keeps runbooks)</p>
            </div>
            <button className="px-3 py-1.5 text-sm rounded-ios bg-red-500/10 text-red-600 border border-red-500/20 hover:bg-red-500/20 transition-colors">
              Reset
            </button>
          </div>
        </div>
      </GlassCard>
    </div>
  );
};

export default Settings;
