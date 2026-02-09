import React, { useState, useEffect } from 'react';
import { usePartner } from './PartnerContext';

interface Framework {
  id: string;
  name: string;
  description: string;
  industries: string[];
  enabled?: boolean;
}

interface SiteCompliance {
  site_id: string;
  site_name: string;
  industry: string | null;
  tier: string;
  frameworks: string[];
  status: string;
}

interface ComplianceDefaults {
  default_frameworks: string[];
  default_industry: string;
  default_coverage_tier: string;
  industry_presets: Record<string, string[]>;
}

const INDUSTRIES = [
  { id: 'healthcare', name: 'Healthcare', icon: '🏥' },
  { id: 'finance', name: 'Finance', icon: '🏦' },
  { id: 'technology', name: 'Technology', icon: '💻' },
  { id: 'retail', name: 'Retail', icon: '🛒' },
  { id: 'government', name: 'Government', icon: '🏛️' },
  { id: 'defense', name: 'Defense', icon: '🛡️' },
  { id: 'legal', name: 'Legal', icon: '⚖️' },
  { id: 'education', name: 'Education', icon: '🎓' },
  { id: 'manufacturing', name: 'Manufacturing', icon: '🏭' },
  { id: 'general', name: 'General', icon: '📋' },
];

const COVERAGE_TIERS = [
  { id: 'basic', name: 'Basic', description: 'Essential compliance monitoring' },
  { id: 'standard', name: 'Standard', description: 'Compliance monitoring + guided remediation' },
  { id: 'full', name: 'Full Coverage', description: 'Complete automation + priority support' },
];

export const PartnerComplianceSettings: React.FC = () => {
  const { apiKey } = usePartner();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [, setDefaults] = useState<ComplianceDefaults | null>(null);
  const [availableFrameworks, setAvailableFrameworks] = useState<Framework[]>([]);
  const [industryPresets, setIndustryPresets] = useState<Record<string, string[]>>({});
  const [sites, setSites] = useState<SiteCompliance[]>([]);
  const [frameworkDistribution, setFrameworkDistribution] = useState<Record<string, number>>({});

  const [selectedIndustry, setSelectedIndustry] = useState('healthcare');
  const [selectedTier, setSelectedTier] = useState('standard');
  const [selectedFrameworks, setSelectedFrameworks] = useState<string[]>([]);

  const [editingSite, setEditingSite] = useState<string | null>(null);
  const [siteFrameworks, setSiteFrameworks] = useState<string[]>([]);
  const [siteIndustry, setSiteIndustry] = useState('');

  const fetchOptions: RequestInit = apiKey
    ? { headers: { 'X-API-Key': apiKey } }
    : { credentials: 'include' };

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);

    try {
      const [defaultsRes, summaryRes] = await Promise.all([
        fetch('/api/partners/me/compliance/defaults', fetchOptions),
        fetch('/api/partners/me/sites/compliance/summary', fetchOptions),
      ]);

      if (defaultsRes.ok) {
        const data = await defaultsRes.json();
        setDefaults(data.defaults);
        setAvailableFrameworks(data.available_frameworks || []);
        setIndustryPresets(data.industry_presets || {});

        // Initialize form state
        setSelectedIndustry(data.defaults?.default_industry || 'healthcare');
        setSelectedTier(data.defaults?.default_coverage_tier || 'standard');
        setSelectedFrameworks(data.defaults?.default_frameworks || ['hipaa']);
      }

      if (summaryRes.ok) {
        const data = await summaryRes.json();
        setSites(data.sites || []);
        setFrameworkDistribution(data.framework_distribution || {});
      }
    } catch (e) {
      setError('Failed to load compliance settings');
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveDefaults = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await fetch('/api/partners/me/compliance/defaults', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          default_frameworks: selectedFrameworks,
          default_industry: selectedIndustry,
          default_coverage_tier: selectedTier,
        }),
      });

      if (response.ok) {
        setSuccess('Default compliance settings updated');
        loadData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to save settings');
      }
    } catch (e) {
      setError('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleApplyPreset = async (siteId: string, industry: string) => {
    try {
      const response = await fetch(`/api/partners/me/sites/${siteId}/compliance/apply-preset?industry=${industry}`, {
        method: 'POST',
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
        credentials: apiKey ? undefined : 'include',
      });

      if (response.ok) {
        setSuccess(`Applied ${industry} preset to site`);
        setEditingSite(null);
        loadData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to apply preset');
      }
    } catch (e) {
      setError('Failed to apply preset');
    }
  };

  const handleSaveSiteCompliance = async (siteId: string) => {
    try {
      const response = await fetch(`/api/partners/me/sites/${siteId}/compliance`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          enabled_frameworks: siteFrameworks,
          industry: siteIndustry || null,
        }),
      });

      if (response.ok) {
        setSuccess('Site compliance settings updated');
        setEditingSite(null);
        loadData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to update site');
      }
    } catch (e) {
      setError('Failed to update site');
    }
  };

  const toggleFramework = (frameworkId: string, list: string[], setter: (v: string[]) => void) => {
    if (list.includes(frameworkId)) {
      setter(list.filter(f => f !== frameworkId));
    } else {
      setter([...list, frameworkId]);
    }
  };

  const getFrameworkName = (id: string) => {
    const fw = availableFrameworks.find(f => f.id === id);
    return fw?.name || id.toUpperCase();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Alerts */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{success}</span>
          <button onClick={() => setSuccess(null)} className="text-green-500 hover:text-green-700">&times;</button>
        </div>
      )}

      {/* Framework Distribution */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Framework Usage</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {Object.entries(frameworkDistribution).map(([fw, count]) => (
            <div key={fw} className="bg-gray-50 rounded-lg p-4 text-center">
              <p className="text-2xl font-bold text-indigo-600">{count}</p>
              <p className="text-sm text-gray-600">{getFrameworkName(fw)}</p>
            </div>
          ))}
          {Object.keys(frameworkDistribution).length === 0 && (
            <p className="text-gray-500 col-span-full">No frameworks configured yet</p>
          )}
        </div>
      </div>

      {/* Default Settings */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-2">Default Compliance Settings</h3>
        <p className="text-sm text-gray-500 mb-6">
          These settings apply to new sites by default. Individual sites can override these settings.
        </p>

        <div className="space-y-6">
          {/* Default Industry */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Default Industry</label>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {INDUSTRIES.map((ind) => (
                <button
                  key={ind.id}
                  onClick={() => setSelectedIndustry(ind.id)}
                  className={`px-3 py-2 rounded-lg text-sm font-medium transition ${
                    selectedIndustry === ind.id
                      ? 'bg-indigo-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  <span className="mr-1">{ind.icon}</span> {ind.name}
                </button>
              ))}
            </div>
          </div>

          {/* Default Coverage Tier */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Default Coverage Tier</label>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {COVERAGE_TIERS.map((tier) => (
                <button
                  key={tier.id}
                  onClick={() => setSelectedTier(tier.id)}
                  className={`p-4 rounded-lg border-2 text-left transition ${
                    selectedTier === tier.id
                      ? 'border-indigo-600 bg-indigo-50'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <p className="font-medium text-gray-900">{tier.name}</p>
                  <p className="text-sm text-gray-500">{tier.description}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Default Frameworks */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Default Frameworks</label>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {availableFrameworks.map((fw) => (
                <label
                  key={fw.id}
                  className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition ${
                    selectedFrameworks.includes(fw.id)
                      ? 'border-indigo-600 bg-indigo-50'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedFrameworks.includes(fw.id)}
                    onChange={() => toggleFramework(fw.id, selectedFrameworks, setSelectedFrameworks)}
                    className="mt-1 h-4 w-4 text-indigo-600 rounded"
                  />
                  <div>
                    <p className="font-medium text-gray-900 text-sm">{fw.name}</p>
                    <p className="text-xs text-gray-500 line-clamp-2">{fw.description}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Industry Preset Preview */}
          {industryPresets[selectedIndustry] && (
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-sm font-medium text-gray-700 mb-2">
                Industry Preset for {INDUSTRIES.find(i => i.id === selectedIndustry)?.name}:
              </p>
              <div className="flex flex-wrap gap-2">
                {industryPresets[selectedIndustry].map((fw) => (
                  <span key={fw} className="px-2 py-1 bg-white rounded text-sm text-gray-600 border">
                    {getFrameworkName(fw)}
                  </span>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={handleSaveDefaults}
            disabled={saving}
            className="px-6 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
          >
            {saving ? 'Saving...' : 'Save Default Settings'}
          </button>
        </div>
      </div>

      {/* Sites Compliance */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="p-6 border-b">
          <h3 className="text-lg font-semibold text-gray-900">Site Compliance Configuration</h3>
          <p className="text-sm text-gray-500">Configure compliance frameworks for each site</p>
        </div>

        {sites.length === 0 ? (
          <div className="p-12 text-center">
            <p className="text-gray-500">No sites configured yet</p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Site</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Industry</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tier</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Frameworks</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {sites.map((site) => (
                <tr key={site.site_id} className="hover:bg-indigo-50/50">
                  <td className="px-6 py-4">
                    <p className="font-medium text-gray-900">{site.site_name}</p>
                    <p className="text-sm text-gray-500">{site.site_id}</p>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600 capitalize">
                    {site.industry || 'Not set'}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600 capitalize">
                    {site.tier}
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1">
                      {site.frameworks.map((fw) => (
                        <span
                          key={fw}
                          className="px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded"
                        >
                          {getFrameworkName(fw)}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                      site.status === 'active' || site.status === 'online'
                        ? 'bg-green-100 text-green-800'
                        : site.status === 'pending'
                        ? 'bg-yellow-100 text-yellow-800'
                        : 'bg-gray-100 text-gray-800'
                    }`}>
                      {site.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button
                      onClick={() => {
                        setEditingSite(site.site_id);
                        setSiteFrameworks(site.frameworks);
                        setSiteIndustry(site.industry || '');
                      }}
                      className="text-indigo-600 hover:text-indigo-800 text-sm font-medium"
                    >
                      Configure
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Edit Site Modal */}
      {editingSite && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
          <div className="bg-white rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              Configure Compliance - {sites.find(s => s.site_id === editingSite)?.site_name}
            </h3>

            {/* Quick Presets */}
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 mb-2">Apply Industry Preset</label>
              <div className="flex flex-wrap gap-2">
                {INDUSTRIES.map((ind) => (
                  <button
                    key={ind.id}
                    onClick={() => handleApplyPreset(editingSite, ind.id)}
                    className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-lg transition"
                  >
                    {ind.icon} {ind.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="border-t pt-4 mb-4">
              <p className="text-sm text-gray-500 mb-4">Or customize frameworks manually:</p>
            </div>

            {/* Site Industry */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">Industry</label>
              <select
                value={siteIndustry}
                onChange={(e) => setSiteIndustry(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
              >
                <option value="">Use default</option>
                {INDUSTRIES.map((ind) => (
                  <option key={ind.id} value={ind.id}>{ind.name}</option>
                ))}
              </select>
            </div>

            {/* Site Frameworks */}
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 mb-2">Enabled Frameworks</label>
              <div className="grid grid-cols-2 gap-3 max-h-64 overflow-y-auto">
                {availableFrameworks.map((fw) => (
                  <label
                    key={fw.id}
                    className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition ${
                      siteFrameworks.includes(fw.id)
                        ? 'border-indigo-600 bg-indigo-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={siteFrameworks.includes(fw.id)}
                      onChange={() => toggleFramework(fw.id, siteFrameworks, setSiteFrameworks)}
                      className="mt-1 h-4 w-4 text-indigo-600 rounded"
                    />
                    <div>
                      <p className="font-medium text-gray-900 text-sm">{fw.name}</p>
                      <p className="text-xs text-gray-500">{fw.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setEditingSite(null)}
                className="px-4 py-2 text-gray-600 hover:text-gray-900 transition"
              >
                Cancel
              </button>
              <button
                onClick={() => handleSaveSiteCompliance(editingSite)}
                className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition"
              >
                Save Changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerComplianceSettings;
