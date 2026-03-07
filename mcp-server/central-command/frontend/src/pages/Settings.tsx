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

  // Healing
  default_healing_tier: string;

  // Data Retention
  telemetry_retention_days: number;
  incident_retention_days: number;
  audit_log_retention_days: number;

  // Notifications
  email_notifications_enabled: boolean;
  slack_notifications_enabled: boolean;
  escalation_timeout_minutes: number;
  smtp_host: string;
  smtp_port: number;
  smtp_from: string;
  smtp_username: string;
  smtp_password: string;
  smtp_tls: boolean;

  // Learning Loop
  promotion_min_success_rate: number;
  promotion_min_executions: number;
  auto_promote_enabled: boolean;

  // Branding
  company_name: string;
  logo_url: string;
  support_email: string;

  // Evidence
  minio_endpoint: string;
  minio_bucket: string;
  ots_calendar_url: string;
  evidence_retention_days: number;

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
  default_healing_tier: 'standard',
  telemetry_retention_days: 90,
  incident_retention_days: 365,
  audit_log_retention_days: 730,
  email_notifications_enabled: true,
  slack_notifications_enabled: false,
  escalation_timeout_minutes: 60,
  smtp_host: '',
  smtp_port: 587,
  smtp_from: '',
  smtp_username: '',
  smtp_password: '',
  smtp_tls: true,
  promotion_min_success_rate: 80,
  promotion_min_executions: 5,
  auto_promote_enabled: false,
  company_name: 'OsirisCare',
  logo_url: '',
  support_email: '',
  minio_endpoint: '',
  minio_bucket: 'evidence-worm-v2',
  ots_calendar_url: 'https://alice.btc.calendar.opentimestamps.org',
  evidence_retention_days: 2555,
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
      <div className="w-11 h-6 bg-fill-tertiary peer-focus:ring-2 peer-focus:ring-accent-primary/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-accent-primary"></div>
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

const TextSetting: React.FC<{
  label: string;
  description?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}> = ({ label, description, value, onChange, placeholder, type = 'text' }) => (
  <div className="flex items-center justify-between py-2">
    <div className="min-w-0 mr-4">
      <span className="text-sm font-medium text-label-primary">{label}</span>
      {description && <p className="text-xs text-label-tertiary mt-0.5">{description}</p>}
    </div>
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-64 px-3 py-1.5 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary"
    />
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
        <SelectSetting
          label="Default Healing Tier"
          description="Applied to new sites unless overridden"
          value={settings.default_healing_tier}
          onChange={(v) => updateSetting('default_healing_tier', v)}
          options={[
            { value: 'standard', label: 'Standard (21 core rules)' },
            { value: 'full_coverage', label: 'Full Coverage (all L1 rules)' },
            { value: 'monitor_only', label: 'Monitor Only (no auto-healing)' },
          ]}
        />
      </SettingSection>

      {/* Learning Loop Settings */}
      <SettingSection
        title="Learning Loop"
        description="Control when L2 patterns are eligible for L1 promotion"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        }
      >
        <NumberSetting
          label="Minimum Success Rate"
          description="Pattern must exceed this rate to be promotable"
          value={settings.promotion_min_success_rate}
          onChange={(v) => updateSetting('promotion_min_success_rate', v)}
          min={50}
          max={100}
          suffix="%"
        />
        <NumberSetting
          label="Minimum Executions"
          description="Pattern must have this many executions before promotion"
          value={settings.promotion_min_executions}
          onChange={(v) => updateSetting('promotion_min_executions', v)}
          min={1}
          max={100}
        />
        <ToggleSetting
          label="Auto-Promote"
          description="Automatically promote patterns that meet thresholds (skip manual review)"
          checked={settings.auto_promote_enabled}
          onChange={(v) => updateSetting('auto_promote_enabled', v)}
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
        <div className="border-t border-separator-light pt-4 mt-4">
          <p className="text-xs font-medium text-label-tertiary uppercase tracking-wide mb-3">SMTP Configuration</p>
          <TextSetting
            label="SMTP Host"
            value={settings.smtp_host}
            onChange={(v) => updateSetting('smtp_host', v)}
            placeholder="smtp.gmail.com"
          />
          <NumberSetting
            label="SMTP Port"
            value={settings.smtp_port}
            onChange={(v) => updateSetting('smtp_port', v)}
            min={25}
            max={2525}
          />
          <TextSetting
            label="From Address"
            value={settings.smtp_from}
            onChange={(v) => updateSetting('smtp_from', v)}
            placeholder="alerts@yourcompany.com"
          />
          <TextSetting
            label="Username"
            value={settings.smtp_username}
            onChange={(v) => updateSetting('smtp_username', v)}
            placeholder="SMTP username"
          />
          <TextSetting
            label="Password"
            value={settings.smtp_password}
            onChange={(v) => updateSetting('smtp_password', v)}
            placeholder="••••••••"
            type="password"
          />
          <ToggleSetting
            label="Use TLS"
            description="Encrypt SMTP connection (STARTTLS)"
            checked={settings.smtp_tls}
            onChange={(v) => updateSetting('smtp_tls', v)}
          />
        </div>
      </SettingSection>

      {/* Branding */}
      <SettingSection
        title="Branding"
        description="White-label settings for partner portal"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
          </svg>
        }
      >
        <TextSetting
          label="Company Name"
          description="Displayed in portal header and emails"
          value={settings.company_name}
          onChange={(v) => updateSetting('company_name', v)}
          placeholder="Your Company"
        />
        <TextSetting
          label="Logo URL"
          description="Square logo for portal and reports (PNG/SVG)"
          value={settings.logo_url}
          onChange={(v) => updateSetting('logo_url', v)}
          placeholder="https://example.com/logo.png"
        />
        <TextSetting
          label="Support Email"
          description="Shown in client portal and escalation emails"
          value={settings.support_email}
          onChange={(v) => updateSetting('support_email', v)}
          placeholder="support@yourcompany.com"
        />
      </SettingSection>

      {/* Evidence & WORM */}
      <SettingSection
        title="Evidence Storage"
        description="WORM-compliant evidence chain and timestamping"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
        }
      >
        <TextSetting
          label="MinIO Endpoint"
          description="S3-compatible object storage endpoint"
          value={settings.minio_endpoint}
          onChange={(v) => updateSetting('minio_endpoint', v)}
          placeholder="minio:9000"
        />
        <TextSetting
          label="WORM Bucket"
          description="Object-locked bucket for evidence bundles"
          value={settings.minio_bucket}
          onChange={(v) => updateSetting('minio_bucket', v)}
          placeholder="evidence-worm-v2"
        />
        <TextSetting
          label="OTS Calendar URL"
          description="OpenTimestamps calendar for Bitcoin anchoring"
          value={settings.ots_calendar_url}
          onChange={(v) => updateSetting('ots_calendar_url', v)}
          placeholder="https://alice.btc.calendar.opentimestamps.org"
        />
        <NumberSetting
          label="Evidence Retention"
          description="HIPAA minimum: 6 years (2190 days)"
          value={settings.evidence_retention_days}
          onChange={(v) => updateSetting('evidence_retention_days', v)}
          min={2190}
          max={3650}
          suffix="days"
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
            <button
              onClick={async () => {
                if (!confirm('Permanently delete old telemetry data? This cannot be undone.')) return;
                try {
                  const token = localStorage.getItem('auth_token');
                  const res = await fetch('/api/dashboard/admin/settings/purge-telemetry', {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` },
                  });
                  const data = await res.json();
                  if (res.ok) {
                    setSaveMessage({ type: 'success', text: `Purged ${data.deleted} telemetry records (>${data.retention_days} days old)` });
                  } else {
                    setSaveMessage({ type: 'error', text: data.detail || 'Purge failed' });
                  }
                } catch {
                  setSaveMessage({ type: 'error', text: 'Failed to purge telemetry' });
                }
              }}
              className="px-3 py-1.5 text-sm rounded-ios bg-red-500/10 text-red-600 border border-red-500/20 hover:bg-red-500/20 transition-colors"
            >
              Purge Now
            </button>
          </div>
          <div className="flex items-center justify-between py-2">
            <div>
              <span className="text-sm font-medium text-label-primary">Reset Learning Data</span>
              <p className="text-xs text-label-tertiary mt-0.5">Clear all patterns and L1 rules (keeps runbooks)</p>
            </div>
            <button
              onClick={async () => {
                if (!confirm('Reset ALL learning data? This will delete all patterns and auto-promoted L1 rules. This cannot be undone.')) return;
                try {
                  const token = localStorage.getItem('auth_token');
                  const res = await fetch('/api/dashboard/admin/settings/reset-learning', {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` },
                  });
                  const data = await res.json();
                  if (res.ok) {
                    setSaveMessage({ type: 'success', text: `Reset complete: ${data.patterns_deleted} patterns and ${data.rules_deleted} rules deleted` });
                  } else {
                    setSaveMessage({ type: 'error', text: data.detail || 'Reset failed' });
                  }
                } catch {
                  setSaveMessage({ type: 'error', text: 'Failed to reset learning data' });
                }
              }}
              className="px-3 py-1.5 text-sm rounded-ios bg-red-500/10 text-red-600 border border-red-500/20 hover:bg-red-500/20 transition-colors"
            >
              Reset
            </button>
          </div>
        </div>
      </GlassCard>
    </div>
  );
};

export default Settings;
