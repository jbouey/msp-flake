import React from 'react';

interface Incident {
  incident_id: string;
  incident_type: string;
  severity: string;
  auto_fixed: boolean;
  resolution_time_sec?: number;
  created_at: string;
  resolved_at?: string;
}

interface IncidentListProps {
  incidents: Incident[];
}

export const IncidentList: React.FC<IncidentListProps> = ({ incidents }) => {
  const severityColors = {
    critical: 'bg-red-100 text-red-800',
    high: 'bg-orange-100 text-orange-800',
    medium: 'bg-yellow-100 text-yellow-800',
    low: 'bg-blue-100 text-blue-800'
  };

  const formatType = (type: string) => {
    return type
      .replace(/_/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '-';
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  if (incidents.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <div className="text-4xl mb-3">✓</div>
        <p className="text-gray-600">No recent incidents</p>
        <p className="text-sm text-gray-400 mt-1">Your systems are running smoothly</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
      {incidents.map((incident) => (
        <div key={incident.incident_id} className="p-4 hover:bg-blue-50/50">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className={`px-2 py-0.5 text-xs font-medium rounded ${
                  severityColors[incident.severity as keyof typeof severityColors] || severityColors.medium
                }`}>
                  {incident.severity.toUpperCase()}
                </span>
                <span className="text-sm font-medium text-gray-900 truncate">
                  {formatType(incident.incident_type)}
                </span>
              </div>
              <p className="text-xs text-gray-500">
                {new Date(incident.created_at).toLocaleString()}
              </p>
            </div>

            <div className="flex flex-col items-end gap-1">
              {incident.auto_fixed ? (
                <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-100 px-2 py-1 rounded-full">
                  <span>✓</span>
                  <span>Auto-fixed</span>
                </span>
              ) : incident.resolved_at ? (
                <span className="inline-flex items-center gap-1 text-xs text-blue-700 bg-blue-100 px-2 py-1 rounded-full">
                  <span>✓</span>
                  <span>Resolved</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-orange-700 bg-orange-100 px-2 py-1 rounded-full">
                  <span>⏳</span>
                  <span>In Progress</span>
                </span>
              )}
              {incident.resolution_time_sec && (
                <span className="text-xs text-gray-400">
                  {formatDuration(incident.resolution_time_sec)}
                </span>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};
