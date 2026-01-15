import React, { useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { rmmComparisonApi } from '../utils/api';
import type {
  RMMCompareRequest,
  RMMComparisonReport,
  RMMMatch,
  RMMGap,
} from '../utils/api';

/**
 * RMM Provider options
 */
const RMM_PROVIDERS = [
  { value: 'connectwise', label: 'ConnectWise Automate' },
  { value: 'datto', label: 'Datto RMM' },
  { value: 'ninja', label: 'NinjaRMM' },
  { value: 'syncro', label: 'Syncro' },
  { value: 'manual', label: 'CSV / Manual' },
] as const;

/**
 * Confidence badge colors
 */
const confidenceColors: Record<string, string> = {
  exact: 'bg-health-healthy text-white',
  high: 'bg-blue-500 text-white',
  medium: 'bg-health-warning text-white',
  low: 'bg-orange-500 text-white',
  no_match: 'bg-gray-500 text-white',
};

const confidenceLabels: Record<string, string> = {
  exact: 'Exact Match',
  high: 'High Confidence',
  medium: 'Medium',
  low: 'Low',
  no_match: 'No Match',
};

/**
 * Gap type badges
 */
const gapColors: Record<string, string> = {
  missing_from_rmm: 'bg-health-warning',
  missing_from_ad: 'bg-blue-500',
  stale_rmm: 'bg-gray-500',
  stale_ad: 'bg-gray-500',
};

const gapLabels: Record<string, string> = {
  missing_from_rmm: 'Missing from RMM',
  missing_from_ad: 'Missing from AD',
  stale_rmm: 'Stale in RMM',
  stale_ad: 'Stale in AD',
};

/**
 * Parse CSV content to device array
 */
function parseCSV(content: string): Array<Record<string, string>> {
  const lines = content.trim().split('\n');
  if (lines.length < 2) return [];

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
  const devices: Array<Record<string, string>> = [];

  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(',').map(v => v.trim());
    const device: Record<string, string> = {};

    headers.forEach((header, index) => {
      if (values[index]) {
        // Map common column names
        if (header === 'hostname' || header === 'computer_name' || header === 'name') {
          device.hostname = values[index];
        } else if (header === 'ip' || header === 'ip_address') {
          device.ip_address = values[index];
        } else if (header === 'mac' || header === 'mac_address') {
          device.mac_address = values[index];
        } else if (header === 'os' || header === 'os_name') {
          device.os_name = values[index];
        } else if (header === 'serial' || header === 'serial_number') {
          device.serial_number = values[index];
        } else if (header === 'device_id' || header === 'id') {
          device.device_id = values[index];
        }
      }
    });

    if (device.hostname) {
      devices.push(device);
    }
  }

  return devices;
}

/**
 * Summary Card Component
 */
const SummaryCard: React.FC<{ summary: RMMComparisonReport['summary'] }> = ({ summary }) => {
  const coverageColor = summary.coverage_rate >= 90 ? 'text-health-healthy' :
                        summary.coverage_rate >= 70 ? 'text-health-warning' : 'text-health-critical';

  return (
    <GlassCard className="p-6 mb-6">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Comparison Summary</h2>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="text-center">
          <div className={`text-3xl font-bold ${coverageColor}`}>
            {summary.coverage_rate.toFixed(0)}%
          </div>
          <div className="text-sm text-label-secondary">Coverage</div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-bold text-label-primary">
            {summary.our_device_count}
          </div>
          <div className="text-sm text-label-secondary">Our Devices</div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-bold text-label-primary">
            {summary.rmm_device_count}
          </div>
          <div className="text-sm text-label-secondary">RMM Devices</div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-bold text-health-healthy">
            {summary.matched_count}
          </div>
          <div className="text-sm text-label-secondary">Matched</div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-bold text-blue-400">
            {summary.exact_match_count}
          </div>
          <div className="text-sm text-label-secondary">Exact Match</div>
        </div>
      </div>
    </GlassCard>
  );
};

/**
 * Match Table Component
 */
const MatchTable: React.FC<{ matches: RMMMatch[] }> = ({ matches }) => {
  const [filter, setFilter] = useState<string>('all');

  const filteredMatches = matches.filter(m => {
    if (filter === 'all') return true;
    return m.confidence === filter;
  });

  return (
    <GlassCard className="mb-6">
      <div className="p-4 border-b border-glass-border">
        <h3 className="text-lg font-semibold text-label-primary mb-3">Device Matches</h3>
        <div className="flex gap-2 flex-wrap">
          {['all', 'exact', 'high', 'medium', 'low', 'no_match'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                filter === f
                  ? 'bg-accent-primary text-white'
                  : 'text-label-secondary hover:bg-glass-bg/50'
              }`}
            >
              {f === 'all' ? 'All' : confidenceLabels[f]}
              <span className="ml-1 text-xs opacity-70">
                ({f === 'all' ? matches.length : matches.filter(m => m.confidence === f).length})
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-glass-border">
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Our Device</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">RMM Device</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Confidence</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Matching Fields</th>
            </tr>
          </thead>
          <tbody>
            {filteredMatches.map((match, idx) => (
              <tr key={idx} className="hover:bg-glass-bg/30 border-b border-glass-border/50">
                <td className="px-4 py-3 font-medium text-label-primary">
                  {match.our_hostname}
                </td>
                <td className="px-4 py-3 text-label-secondary">
                  {match.rmm_device?.hostname || '-'}
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${confidenceColors[match.confidence]}`}>
                    {confidenceLabels[match.confidence]}
                  </span>
                  <span className="ml-2 text-xs text-label-tertiary">
                    ({(match.confidence_score * 100).toFixed(0)}%)
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-1 flex-wrap">
                    {match.matching_fields.map(field => (
                      <span key={field} className="px-2 py-0.5 bg-glass-bg rounded text-xs text-label-secondary">
                        {field}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
};

/**
 * Gaps Card Component
 */
const GapsCard: React.FC<{ gaps: RMMGap[] }> = ({ gaps }) => {
  if (gaps.length === 0) {
    return (
      <GlassCard className="p-6">
        <h3 className="text-lg font-semibold text-label-primary mb-3">Coverage Gaps</h3>
        <div className="text-center text-health-healthy py-4">
          No coverage gaps detected!
        </div>
      </GlassCard>
    );
  }

  // Group gaps by type
  const gapsByType = gaps.reduce((acc, gap) => {
    const type = gap.gap_type;
    if (!acc[type]) acc[type] = [];
    acc[type].push(gap);
    return acc;
  }, {} as Record<string, RMMGap[]>);

  return (
    <GlassCard className="p-6">
      <h3 className="text-lg font-semibold text-label-primary mb-4">Coverage Gaps ({gaps.length})</h3>
      <div className="space-y-4">
        {Object.entries(gapsByType).map(([type, typeGaps]) => (
          <div key={type}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`px-2 py-1 rounded text-xs font-medium text-white ${gapColors[type]}`}>
                {gapLabels[type]}
              </span>
              <span className="text-sm text-label-secondary">({typeGaps.length})</span>
            </div>
            <div className="ml-4 space-y-2">
              {typeGaps.slice(0, 10).map((gap, idx) => (
                <div key={idx} className="flex items-start gap-2 text-sm">
                  <span className="text-label-primary font-medium">
                    {(gap.device as { hostname?: string }).hostname || 'Unknown'}
                  </span>
                  <span className="text-label-tertiary">-</span>
                  <span className="text-label-secondary">{gap.recommendation}</span>
                </div>
              ))}
              {typeGaps.length > 10 && (
                <div className="text-label-tertiary text-sm">
                  ...and {typeGaps.length - 10} more
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
};

/**
 * Upload Form Component
 */
const UploadForm: React.FC<{
  onCompare: (data: RMMCompareRequest) => Promise<void>;
  isLoading: boolean;
}> = ({ onCompare, isLoading }) => {
  const [provider, setProvider] = useState<RMMCompareRequest['provider']>('manual');
  const [csvContent, setCsvContent] = useState('');
  const [deviceCount, setDeviceCount] = useState(0);

  const handleFileUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const content = event.target?.result as string;
      setCsvContent(content);
      const devices = parseCSV(content);
      setDeviceCount(devices.length);
    };
    reader.readAsText(file);
  }, []);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvContent) return;

    const devices = parseCSV(csvContent);
    if (devices.length === 0) return;

    await onCompare({
      provider,
      devices: devices.map(d => ({
        hostname: d.hostname || '',
        device_id: d.device_id,
        ip_address: d.ip_address,
        mac_address: d.mac_address,
        os_name: d.os_name,
        serial_number: d.serial_number,
      })),
    });
  }, [csvContent, provider, onCompare]);

  return (
    <GlassCard className="p-6 mb-6">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Upload RMM Data</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Provider selection */}
        <div>
          <label className="block text-sm font-medium text-label-secondary mb-2">
            RMM Provider
          </label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value as RMMCompareRequest['provider'])}
            className="w-full px-4 py-2 bg-glass-bg border border-glass-border rounded-lg text-label-primary focus:outline-none focus:ring-2 focus:ring-accent-primary"
          >
            {RMM_PROVIDERS.map(p => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </div>

        {/* File upload */}
        <div>
          <label className="block text-sm font-medium text-label-secondary mb-2">
            CSV Export File
          </label>
          <div className="flex items-center gap-4">
            <input
              type="file"
              accept=".csv"
              onChange={handleFileUpload}
              className="flex-1 px-4 py-2 bg-glass-bg border border-glass-border rounded-lg text-label-primary file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-accent-primary file:text-white file:cursor-pointer"
            />
            {deviceCount > 0 && (
              <Badge variant="success" className="whitespace-nowrap">
                {deviceCount} devices
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-label-tertiary">
            CSV should have headers: hostname, ip_address, mac_address, os_name, device_id
          </p>
        </div>

        {/* Submit button */}
        <button
          type="submit"
          disabled={isLoading || deviceCount === 0}
          className="w-full px-4 py-2 bg-accent-primary text-white rounded-lg font-medium hover:bg-accent-primary/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isLoading ? (
            <span className="flex items-center justify-center gap-2">
              <Spinner size="sm" /> Comparing...
            </span>
          ) : (
            'Compare with Workstations'
          )}
        </button>
      </form>
    </GlassCard>
  );
};

/**
 * RMM Comparison Page
 */
export const RMMComparison: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const [report, setReport] = useState<RMMComparisonReport | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCompare = useCallback(async (data: RMMCompareRequest) => {
    if (!siteId) return;

    setIsLoading(true);
    setError(null);

    try {
      const result = await rmmComparisonApi.compare(siteId, data);
      setReport(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to compare');
    } finally {
      setIsLoading(false);
    }
  }, [siteId]);

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/sites" className="text-label-secondary hover:text-label-primary">
          Sites
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}`} className="text-label-secondary hover:text-label-primary">
          {siteId}
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}/workstations`} className="text-label-secondary hover:text-label-primary">
          Workstations
        </Link>
        <span className="text-label-tertiary">/</span>
        <span className="text-label-primary">RMM Comparison</span>
      </div>

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-label-primary">RMM Comparison</h1>
        <p className="text-label-secondary mt-1">
          Compare your AD-discovered workstations with RMM tool data to identify coverage gaps and duplicates
        </p>
      </div>

      {/* Upload form */}
      <UploadForm onCompare={handleCompare} isLoading={isLoading} />

      {/* Error message */}
      {error && (
        <GlassCard className="p-4 bg-health-critical/20 border-health-critical">
          <p className="text-health-critical">{error}</p>
        </GlassCard>
      )}

      {/* Results */}
      {report && (
        <>
          <SummaryCard summary={report.summary} />
          <MatchTable matches={report.matches} />
          <GapsCard gaps={report.gaps} />
        </>
      )}
    </div>
  );
};

export default RMMComparison;
