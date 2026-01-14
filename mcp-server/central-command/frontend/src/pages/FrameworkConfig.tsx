/**
 * Multi-Framework Compliance Configuration Page
 *
 * Allows configuration of which compliance frameworks each appliance reports against.
 * Supports HIPAA, SOC 2, PCI DSS, NIST CSF, and CIS Controls.
 */

import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { frameworksApi, sitesApi, SiteAppliance } from '../utils/api';
import {
  ComplianceFramework,
  FrameworkConfig as IFrameworkConfig,
  FrameworkScore,
  FrameworkMetadata,
  FRAMEWORK_LABELS,
  FRAMEWORK_COLORS,
} from '../types';

const ALL_FRAMEWORKS: ComplianceFramework[] = ['hipaa', 'soc2', 'pci_dss', 'nist_csf', 'cis'];

const INDUSTRIES = [
  { value: 'healthcare', label: 'Healthcare', primary: 'hipaa' },
  { value: 'technology', label: 'Technology / SaaS', primary: 'soc2' },
  { value: 'retail', label: 'Retail / E-commerce', primary: 'pci_dss' },
  { value: 'finance', label: 'Financial Services', primary: 'soc2' },
  { value: 'government', label: 'Government', primary: 'nist_csf' },
  { value: 'general', label: 'General / Other', primary: 'nist_csf' },
];

interface FrameworkCardProps {
  framework: ComplianceFramework;
  enabled: boolean;
  isPrimary: boolean;
  score?: FrameworkScore;
  metadata?: FrameworkMetadata;
  onToggle: () => void;
  onSetPrimary: () => void;
}

function FrameworkCard({
  framework,
  enabled,
  isPrimary,
  score,
  metadata,
  onToggle,
  onSetPrimary,
}: FrameworkCardProps) {
  const colorClass = FRAMEWORK_COLORS[framework];

  return (
    <div
      className={`bg-gray-800 rounded-lg p-4 border-2 transition-all ${
        enabled ? `border-${colorClass}-500` : 'border-gray-700'
      } ${isPrimary ? 'ring-2 ring-yellow-500' : ''}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            {FRAMEWORK_LABELS[framework]}
            {isPrimary && (
              <span className="text-xs bg-yellow-500 text-black px-2 py-0.5 rounded">
                Primary
              </span>
            )}
          </h3>
          {metadata && (
            <p className="text-sm text-gray-400">{metadata.version}</p>
          )}
        </div>
        <label className="relative inline-flex items-center cursor-pointer">
          <input
            type="checkbox"
            className="sr-only peer"
            checked={enabled}
            onChange={onToggle}
          />
          <div className="w-11 h-6 bg-gray-600 peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
        </label>
      </div>

      {metadata && (
        <p className="text-sm text-gray-400 mb-3">{metadata.description}</p>
      )}

      {score && enabled && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm text-gray-400">Compliance Score</span>
            <span
              className={`text-lg font-bold ${
                score.is_compliant
                  ? 'text-green-400'
                  : score.at_risk
                  ? 'text-red-400'
                  : 'text-yellow-400'
              }`}
            >
              {score.score_percentage.toFixed(1)}%
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className={`h-2 rounded-full ${
                score.is_compliant
                  ? 'bg-green-500'
                  : score.at_risk
                  ? 'bg-red-500'
                  : 'bg-yellow-500'
              }`}
              style={{ width: `${score.score_percentage}%` }}
            ></div>
          </div>
          <div className="flex justify-between text-xs text-gray-500 mt-1">
            <span>{score.passing_controls} passing</span>
            <span>{score.failing_controls} failing</span>
            <span>{score.unknown_controls} unknown</span>
          </div>
        </div>
      )}

      {enabled && !isPrimary && (
        <button
          onClick={onSetPrimary}
          className="text-sm text-blue-400 hover:text-blue-300"
        >
          Set as primary
        </button>
      )}
    </div>
  );
}

export default function FrameworkConfig() {
  const { siteId } = useParams<{ siteId: string }>();
  const [appliances, setAppliances] = useState<SiteAppliance[]>([]);
  const [selectedAppliance, setSelectedAppliance] = useState<string | null>(null);
  const [config, setConfig] = useState<IFrameworkConfig | null>(null);
  const [scores, setScores] = useState<FrameworkScore[]>([]);
  const [metadata, setMetadata] = useState<Record<ComplianceFramework, FrameworkMetadata> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Load appliances and metadata
  useEffect(() => {
    async function loadData() {
      if (!siteId) return;

      try {
        setLoading(true);
        const [appliancesData, metadataResponse] = await Promise.all([
          sitesApi.getAppliances(siteId),
          frameworksApi.getMetadata(),
        ]);
        setAppliances(appliancesData.appliances);
        // API returns {frameworks: {...}, supported_count: N} - extract frameworks object
        setMetadata((metadataResponse as any).frameworks || metadataResponse);

        // Auto-select first appliance
        if (appliancesData.appliances.length > 0) {
          setSelectedAppliance(appliancesData.appliances[0].appliance_id);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, [siteId]);

  // Load config and scores when appliance changes
  useEffect(() => {
    async function loadApplianceData() {
      if (!selectedAppliance) return;

      try {
        const [configData, scoresData] = await Promise.all([
          frameworksApi.getConfig(selectedAppliance).catch(() => null),
          frameworksApi.getScores(selectedAppliance).catch(() => []),
        ]);

        setConfig(
          configData || {
            appliance_id: selectedAppliance,
            site_id: siteId || '',
            enabled_frameworks: ['hipaa'],
            primary_framework: 'hipaa',
            industry: 'healthcare',
            framework_metadata: {},
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }
        );
        setScores(scoresData);
      } catch (err) {
        console.error('Failed to load appliance config:', err);
      }
    }

    loadApplianceData();
  }, [selectedAppliance, siteId]);

  const handleToggleFramework = (framework: ComplianceFramework) => {
    if (!config) return;

    const isEnabled = config.enabled_frameworks.includes(framework);
    let newEnabled: ComplianceFramework[];

    if (isEnabled) {
      // Don't allow disabling the primary framework
      if (framework === config.primary_framework) {
        setError('Cannot disable the primary framework. Set a different primary first.');
        return;
      }
      newEnabled = config.enabled_frameworks.filter((f) => f !== framework);
    } else {
      newEnabled = [...config.enabled_frameworks, framework];
    }

    setConfig({ ...config, enabled_frameworks: newEnabled });
    setError(null);
  };

  const handleSetPrimary = (framework: ComplianceFramework) => {
    if (!config) return;

    // Ensure framework is enabled
    const enabled = config.enabled_frameworks.includes(framework)
      ? config.enabled_frameworks
      : [...config.enabled_frameworks, framework];

    setConfig({
      ...config,
      enabled_frameworks: enabled,
      primary_framework: framework,
    });
  };

  const handleIndustryChange = (industry: string) => {
    if (!config) return;

    const industryInfo = INDUSTRIES.find((i) => i.value === industry);
    const primary = (industryInfo?.primary || 'nist_csf') as ComplianceFramework;

    // Ensure primary framework is enabled
    const enabled = config.enabled_frameworks.includes(primary)
      ? config.enabled_frameworks
      : [...config.enabled_frameworks, primary];

    setConfig({
      ...config,
      industry,
      enabled_frameworks: enabled,
      primary_framework: primary,
    });
  };

  const handleSave = async () => {
    if (!config || !selectedAppliance) return;

    try {
      setSaving(true);
      setError(null);
      await frameworksApi.updateConfig(selectedAppliance, {
        enabled_frameworks: config.enabled_frameworks,
        primary_framework: config.primary_framework,
        industry: config.industry,
        framework_metadata: config.framework_metadata,
      });
      setSuccess('Framework configuration saved successfully');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 p-6 flex items-center justify-center">
        <div className="text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <Link
            to={`/sites/${siteId}`}
            className="text-blue-400 hover:text-blue-300 text-sm mb-2 inline-block"
          >
            Back to Site
          </Link>
          <h1 className="text-2xl font-bold text-white">
            Multi-Framework Compliance Configuration
          </h1>
          <p className="text-gray-400 mt-1">
            Configure which compliance frameworks this appliance reports against.
            One check can satisfy controls across multiple frameworks.
          </p>
        </div>

        {/* Alerts */}
        {error && (
          <div className="bg-red-900/50 border border-red-500 text-red-200 px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}
        {success && (
          <div className="bg-green-900/50 border border-green-500 text-green-200 px-4 py-3 rounded mb-4">
            {success}
          </div>
        )}

        {/* Appliance Selector */}
        {appliances.length > 1 && (
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-400 mb-2">
              Select Appliance
            </label>
            <select
              value={selectedAppliance || ''}
              onChange={(e) => setSelectedAppliance(e.target.value)}
              className="bg-gray-800 border border-gray-600 text-white rounded-lg px-4 py-2 w-full max-w-md"
            >
              {appliances.map((a) => (
                <option key={a.appliance_id} value={a.appliance_id}>
                  {a.hostname || a.appliance_id}
                </option>
              ))}
            </select>
          </div>
        )}

        {config && (
          <>
            {/* Industry Selector */}
            <div className="bg-gray-800 rounded-lg p-4 mb-6">
              <label className="block text-sm font-medium text-gray-400 mb-2">
                Industry
              </label>
              <p className="text-sm text-gray-500 mb-3">
                Select your industry to get recommended frameworks and set the primary framework.
              </p>
              <select
                value={config.industry}
                onChange={(e) => handleIndustryChange(e.target.value)}
                className="bg-gray-700 border border-gray-600 text-white rounded-lg px-4 py-2 w-full max-w-md"
              >
                {INDUSTRIES.map((ind) => (
                  <option key={ind.value} value={ind.value}>
                    {ind.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Framework Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
              {ALL_FRAMEWORKS.map((framework) => (
                <FrameworkCard
                  key={framework}
                  framework={framework}
                  enabled={config.enabled_frameworks.includes(framework)}
                  isPrimary={config.primary_framework === framework}
                  score={scores.find((s) => s.framework === framework)}
                  metadata={metadata?.[framework]}
                  onToggle={() => handleToggleFramework(framework)}
                  onSetPrimary={() => handleSetPrimary(framework)}
                />
              ))}
            </div>

            {/* Save Button */}
            <div className="flex justify-end">
              <button
                onClick={handleSave}
                disabled={saving}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium px-6 py-2 rounded-lg transition-colors"
              >
                {saving ? 'Saving...' : 'Save Configuration'}
              </button>
            </div>
          </>
        )}

        {appliances.length === 0 && (
          <div className="text-center text-gray-400 py-12">
            No appliances found for this site. Deploy an appliance first to configure frameworks.
          </div>
        )}
      </div>
    </div>
  );
}
