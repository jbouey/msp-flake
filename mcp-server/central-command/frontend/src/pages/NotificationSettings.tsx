import React, { useState, useEffect } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';

/**
 * Notification channel configuration for partners.
 * Allows configuring Slack, PagerDuty, Email, Teams, and Webhook settings.
 */

interface NotificationSettings {
  // Email
  email_enabled: boolean;
  email_recipients: string[];
  email_from_name: string | null;

  // Slack
  slack_enabled: boolean;
  slack_webhook_url: string | null;
  slack_channel: string | null;
  slack_username: string;
  slack_icon_emoji: string;

  // PagerDuty
  pagerduty_enabled: boolean;
  pagerduty_routing_key: string | null;
  pagerduty_service_id: string | null;

  // Teams
  teams_enabled: boolean;
  teams_webhook_url: string | null;

  // Webhook
  webhook_enabled: boolean;
  webhook_url: string | null;
  webhook_secret: string | null;
  webhook_headers: Record<string, string> | null;

  // Behavior
  escalation_timeout_minutes: number;
  auto_acknowledge: boolean;
  include_raw_data: boolean;
}

interface TestResult {
  channel: string;
  status: 'success' | 'failed' | 'pending';
  message?: string;
}

const defaultSettings: NotificationSettings = {
  email_enabled: true,
  email_recipients: [],
  email_from_name: null,
  slack_enabled: false,
  slack_webhook_url: null,
  slack_channel: '#incidents',
  slack_username: 'OsirisCare',
  slack_icon_emoji: ':warning:',
  pagerduty_enabled: false,
  pagerduty_routing_key: null,
  pagerduty_service_id: null,
  teams_enabled: false,
  teams_webhook_url: null,
  webhook_enabled: false,
  webhook_url: null,
  webhook_secret: null,
  webhook_headers: null,
  escalation_timeout_minutes: 60,
  auto_acknowledge: false,
  include_raw_data: true,
};

/**
 * Channel configuration card component
 */
const ChannelCard: React.FC<{
  title: string;
  description: string;
  icon: React.ReactNode;
  enabled: boolean;
  onToggle: () => void;
  onTest?: () => void;
  testResult?: TestResult;
  children: React.ReactNode;
}> = ({ title, description, icon, enabled, onToggle, onTest, testResult, children }) => {
  return (
    <GlassCard className="p-6">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-ios flex items-center justify-center ${
            enabled ? 'bg-accent-primary/20 text-accent-primary' : 'bg-fill-tertiary text-label-tertiary'
          }`}>
            {icon}
          </div>
          <div>
            <h3 className="font-medium text-label-primary">{title}</h3>
            <p className="text-xs text-label-tertiary">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {enabled && onTest && (
            <button
              onClick={onTest}
              disabled={testResult?.status === 'pending'}
              className="px-3 py-1.5 text-xs rounded-ios bg-fill-secondary text-label-primary border border-separator-light hover:bg-fill-tertiary transition-colors disabled:opacity-50"
            >
              {testResult?.status === 'pending' ? 'Testing...' : 'Test'}
            </button>
          )}
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={onToggle}
              className="sr-only peer"
            />
            <div className="w-11 h-6 bg-fill-tertiary peer-focus:ring-2 peer-focus:ring-accent-primary/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-accent-primary"></div>
          </label>
        </div>
      </div>

      {testResult && testResult.status !== 'pending' && (
        <div className={`mb-4 p-3 rounded-ios text-sm ${
          testResult.status === 'success'
            ? 'bg-green-500/10 text-green-500 border border-green-500/20'
            : 'bg-red-500/10 text-red-500 border border-red-500/20'
        }`}>
          {testResult.status === 'success' ? 'Test notification sent successfully!' : `Test failed: ${testResult.message}`}
        </div>
      )}

      {enabled && (
        <div className="space-y-4 pt-4 border-t border-separator-light">
          {children}
        </div>
      )}
    </GlassCard>
  );
};

/**
 * Input field component
 */
const Field: React.FC<{
  label: string;
  type?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  helpText?: string;
  required?: boolean;
}> = ({ label, type = 'text', value, onChange, placeholder, helpText, required }) => {
  return (
    <div>
      <label className="block text-sm font-medium text-label-secondary mb-1">
        {label} {required && <span className="text-red-500">*</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary"
      />
      {helpText && <p className="text-xs text-label-tertiary mt-1">{helpText}</p>}
    </div>
  );
};

/**
 * Multi-value input for email recipients
 */
const MultiValueInput: React.FC<{
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
}> = ({ label, values, onChange, placeholder }) => {
  const [inputValue, setInputValue] = useState('');

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const trimmed = inputValue.trim();
      if (trimmed && !values.includes(trimmed)) {
        onChange([...values, trimmed]);
        setInputValue('');
      }
    }
  };

  const removeValue = (index: number) => {
    onChange(values.filter((_, i) => i !== index));
  };

  return (
    <div>
      <label className="block text-sm font-medium text-label-secondary mb-1">{label}</label>
      <div className="flex flex-wrap gap-2 p-2 rounded-ios bg-fill-secondary border border-separator-light min-h-[42px]">
        {values.map((value, index) => (
          <Badge key={index} variant="default" className="flex items-center gap-1">
            {value}
            <button
              onClick={() => removeValue(index)}
              className="ml-1 hover:text-red-500"
            >
              x
            </button>
          </Badge>
        ))}
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={values.length === 0 ? placeholder : ''}
          className="flex-1 min-w-[150px] bg-transparent outline-none text-sm text-label-primary"
        />
      </div>
      <p className="text-xs text-label-tertiary mt-1">Press Enter or comma to add</p>
    </div>
  );
};

/**
 * Main NotificationSettings page component
 */
export const NotificationSettings: React.FC = () => {
  const [settings, setSettings] = useState<NotificationSettings>(defaultSettings);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  // Load settings on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const response = await fetch('/api/partners/me/notifications/settings', {
        headers: {
          'X-API-Key': localStorage.getItem('partner_api_key') || '',
        },
      });
      if (response.ok) {
        const data = await response.json();
        setSettings({ ...defaultSettings, ...data });
      }
    } catch (error) {
      console.error('Failed to load notification settings:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const saveSettings = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    try {
      const response = await fetch('/api/partners/me/notifications/settings', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': localStorage.getItem('partner_api_key') || '',
        },
        body: JSON.stringify(settings),
      });
      if (response.ok) {
        setSaveMessage({ type: 'success', text: 'Settings saved successfully!' });
      } else {
        const error = await response.text();
        setSaveMessage({ type: 'error', text: `Failed to save: ${error}` });
      }
    } catch (error) {
      setSaveMessage({ type: 'error', text: 'Failed to save settings. Please try again.' });
    } finally {
      setIsSaving(false);
    }
  };

  const testChannel = async (channel: string) => {
    setTestResults((prev) => ({ ...prev, [channel]: { channel, status: 'pending' } }));
    try {
      const response = await fetch(`/api/partners/me/notifications/settings/test?channel=${channel}`, {
        method: 'POST',
        headers: {
          'X-API-Key': localStorage.getItem('partner_api_key') || '',
        },
      });
      const result = await response.json();
      setTestResults((prev) => ({
        ...prev,
        [channel]: {
          channel,
          status: result.status === 'sent' ? 'success' : 'failed',
          message: result.error,
        },
      }));
    } catch (error) {
      setTestResults((prev) => ({
        ...prev,
        [channel]: { channel, status: 'failed', message: 'Connection error' },
      }));
    }
  };

  const updateSettings = (partial: Partial<NotificationSettings>) => {
    setSettings((prev) => ({ ...prev, ...partial }));
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Notification Settings</h1>
          <p className="text-label-tertiary text-sm mt-1">
            Configure how you receive L3 escalation alerts
          </p>
        </div>
        <button
          onClick={saveSettings}
          disabled={isSaving}
          className="px-4 py-2 text-sm rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
        >
          {isSaving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>

      {/* Save Message */}
      {saveMessage && (
        <div className={`p-4 rounded-ios ${
          saveMessage.type === 'success'
            ? 'bg-green-500/10 text-green-500 border border-green-500/20'
            : 'bg-red-500/10 text-red-500 border border-red-500/20'
        }`}>
          {saveMessage.text}
        </div>
      )}

      {/* Email Channel */}
      <ChannelCard
        title="Email"
        description="Receive escalation alerts via email"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        }
        enabled={settings.email_enabled}
        onToggle={() => updateSettings({ email_enabled: !settings.email_enabled })}
        onTest={() => testChannel('email')}
        testResult={testResults.email}
      >
        <MultiValueInput
          label="Recipients"
          values={settings.email_recipients}
          onChange={(values) => updateSettings({ email_recipients: values })}
          placeholder="Enter email addresses"
        />
        <Field
          label="From Name"
          value={settings.email_from_name || ''}
          onChange={(value) => updateSettings({ email_from_name: value })}
          placeholder="OsirisCare Alerts"
          helpText="Display name for outgoing emails"
        />
      </ChannelCard>

      {/* Slack Channel */}
      <ChannelCard
        title="Slack"
        description="Post escalation alerts to a Slack channel"
        icon={
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"/>
          </svg>
        }
        enabled={settings.slack_enabled}
        onToggle={() => updateSettings({ slack_enabled: !settings.slack_enabled })}
        onTest={() => testChannel('slack')}
        testResult={testResults.slack}
      >
        <Field
          label="Webhook URL"
          value={settings.slack_webhook_url || ''}
          onChange={(value) => updateSettings({ slack_webhook_url: value })}
          placeholder="https://hooks.slack.com/services/..."
          required
        />
        <Field
          label="Channel"
          value={settings.slack_channel || ''}
          onChange={(value) => updateSettings({ slack_channel: value })}
          placeholder="#incidents"
        />
        <div className="grid grid-cols-2 gap-4">
          <Field
            label="Bot Username"
            value={settings.slack_username}
            onChange={(value) => updateSettings({ slack_username: value })}
            placeholder="OsirisCare"
          />
          <Field
            label="Icon Emoji"
            value={settings.slack_icon_emoji}
            onChange={(value) => updateSettings({ slack_icon_emoji: value })}
            placeholder=":warning:"
          />
        </div>
      </ChannelCard>

      {/* PagerDuty Channel */}
      <ChannelCard
        title="PagerDuty"
        description="Create PagerDuty incidents for critical alerts"
        icon={
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M9.211 0c-2.06 0-3.801 1.67-3.801 3.73v16.54c0 2.06 1.741 3.73 3.801 3.73h5.578c2.06 0 3.801-1.67 3.801-3.73V3.73C18.59 1.67 16.849 0 14.789 0H9.211zm2.894 5.94a4.04 4.04 0 0 1 4.04 4.04 4.04 4.04 0 0 1-4.04 4.04 4.04 4.04 0 0 1-4.04-4.04 4.04 4.04 0 0 1 4.04-4.04z"/>
          </svg>
        }
        enabled={settings.pagerduty_enabled}
        onToggle={() => updateSettings({ pagerduty_enabled: !settings.pagerduty_enabled })}
        onTest={() => testChannel('pagerduty')}
        testResult={testResults.pagerduty}
      >
        <Field
          label="Routing Key"
          type="password"
          value={settings.pagerduty_routing_key || ''}
          onChange={(value) => updateSettings({ pagerduty_routing_key: value })}
          placeholder="Enter your PagerDuty routing key"
          helpText="Events API v2 routing key from your PagerDuty service"
          required
        />
        <Field
          label="Service ID"
          value={settings.pagerduty_service_id || ''}
          onChange={(value) => updateSettings({ pagerduty_service_id: value })}
          placeholder="P123ABC"
          helpText="Optional: Service ID for reference"
        />
      </ChannelCard>

      {/* Microsoft Teams Channel */}
      <ChannelCard
        title="Microsoft Teams"
        description="Post alerts to a Teams channel"
        icon={
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M20.625 8.066h-3.748v7.873h-2.19V8.066H10.94V6.123h9.684v1.943zm-10.59 7.873H8.66v-4.654c0-.93.046-1.547-.098-1.862-.145-.315-.457-.473-.94-.473-.506 0-.867.172-1.082.517-.214.346-.32.84-.32 1.483v4.989H4.846V8.066h1.282v.986c.368-.397.72-.685 1.056-.862.335-.176.744-.265 1.227-.265.774 0 1.377.232 1.808.696.432.464.647 1.144.647 2.039v5.279H10.035z"/>
          </svg>
        }
        enabled={settings.teams_enabled}
        onToggle={() => updateSettings({ teams_enabled: !settings.teams_enabled })}
        onTest={() => testChannel('teams')}
        testResult={testResults.teams}
      >
        <Field
          label="Webhook URL"
          value={settings.teams_webhook_url || ''}
          onChange={(value) => updateSettings({ teams_webhook_url: value })}
          placeholder="https://outlook.office.com/webhook/..."
          helpText="Incoming webhook URL from your Teams channel connector"
          required
        />
      </ChannelCard>

      {/* Generic Webhook Channel */}
      <ChannelCard
        title="Webhook"
        description="Send alerts to a custom webhook (PSA/RMM integration)"
        icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
          </svg>
        }
        enabled={settings.webhook_enabled}
        onToggle={() => updateSettings({ webhook_enabled: !settings.webhook_enabled })}
        onTest={() => testChannel('webhook')}
        testResult={testResults.webhook}
      >
        <Field
          label="Webhook URL"
          value={settings.webhook_url || ''}
          onChange={(value) => updateSettings({ webhook_url: value })}
          placeholder="https://your-psa.com/api/webhook"
          required
        />
        <Field
          label="Signing Secret"
          type="password"
          value={settings.webhook_secret || ''}
          onChange={(value) => updateSettings({ webhook_secret: value })}
          placeholder="Optional HMAC signing secret"
          helpText="If provided, requests will include X-OsirisCare-Signature header"
        />
      </ChannelCard>

      {/* Behavior Settings */}
      <GlassCard className="p-6">
        <h3 className="font-medium text-label-primary mb-4">Escalation Behavior</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Escalation Timeout (minutes)
            </label>
            <input
              type="number"
              min={5}
              max={1440}
              value={settings.escalation_timeout_minutes}
              onChange={(e) => updateSettings({ escalation_timeout_minutes: parseInt(e.target.value) || 60 })}
              className="w-32 px-3 py-2 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
            <p className="text-xs text-label-tertiary mt-1">
              Re-notify if not acknowledged within this time
            </p>
          </div>

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.auto_acknowledge}
              onChange={(e) => updateSettings({ auto_acknowledge: e.target.checked })}
              className="w-4 h-4 rounded border-separator-light text-accent-primary focus:ring-accent-primary/30"
            />
            <div>
              <span className="text-sm font-medium text-label-primary">Auto-acknowledge</span>
              <p className="text-xs text-label-tertiary">Automatically mark tickets as acknowledged when notification is sent</p>
            </div>
          </label>

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.include_raw_data}
              onChange={(e) => updateSettings({ include_raw_data: e.target.checked })}
              className="w-4 h-4 rounded border-separator-light text-accent-primary focus:ring-accent-primary/30"
            />
            <div>
              <span className="text-sm font-medium text-label-primary">Include raw data</span>
              <p className="text-xs text-label-tertiary">Include full incident data in webhook payloads</p>
            </div>
          </label>
        </div>
      </GlassCard>
    </div>
  );
};

export default NotificationSettings;
