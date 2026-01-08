/**
 * SensorStatus - Component for displaying Windows sensor status in dual-mode architecture.
 *
 * Shows which hosts have active sensors vs need WinRM polling.
 */

import React, { useState, useEffect } from 'react';

interface Sensor {
  hostname: string;
  domain?: string;
  sensor_version?: string;
  last_heartbeat?: string;
  last_drift_count: number;
  last_compliant: boolean;
  is_active: boolean;
  age_seconds: number;
  mode: string;
}

interface SensorStatusProps {
  siteId: string;
}

const SensorStatus: React.FC<SensorStatusProps> = ({ siteId }) => {
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deployingHost, setDeployingHost] = useState<string | null>(null);

  const fetchSensors = async () => {
    try {
      const response = await fetch(`/api/sensors/sites/${siteId}`);
      if (!response.ok) throw new Error('Failed to fetch sensors');
      const data = await response.json();
      setSensors(data.sensors);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSensors();
    const interval = setInterval(fetchSensors, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, [siteId]);

  const deploySensor = async (hostname: string) => {
    setDeployingHost(hostname);
    try {
      const response = await fetch(`/api/sensors/sites/${siteId}/hosts/${hostname}/deploy`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Deployment failed');
      await fetchSensors();
    } catch (err) {
      console.error('Deploy failed:', err);
    } finally {
      setDeployingHost(null);
    }
  };

  const removeSensor = async (hostname: string) => {
    if (!confirm(`Remove sensor from ${hostname}? The appliance will fall back to WinRM polling.`)) {
      return;
    }

    try {
      const response = await fetch(`/api/sensors/sites/${siteId}/hosts/${hostname}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('Removal failed');
      await fetchSensors();
    } catch (err) {
      console.error('Remove failed:', err);
    }
  };

  const formatAge = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };

  if (loading) {
    return (
      <div className="p-4 text-gray-500">
        Loading sensor status...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-red-500">
        Error: {error}
      </div>
    );
  }

  const activeSensors = sensors.filter(s => s.is_active).length;
  const pollingHosts = sensors.filter(s => !s.sensor_version).length;

  return (
    <div className="bg-white rounded-lg shadow p-4">
      {/* Header */}
      <div className="flex justify-between items-center mb-4">
        <div>
          <h3 className="text-lg font-semibold">Windows Sensors</h3>
          <p className="text-sm text-gray-500">
            <span className="text-green-600 font-medium">{activeSensors}</span> sensors active,{' '}
            <span className="text-yellow-600 font-medium">{pollingHosts}</span> via WinRM polling
          </p>
        </div>
        <button
          onClick={fetchSensors}
          className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 rounded"
        >
          Refresh
        </button>
      </div>

      {/* Sensor Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Host
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Status
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Version
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Compliance
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Last Seen
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Mode
              </th>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {sensors.map((sensor) => (
              <tr key={sensor.hostname} className="hover:bg-gray-50">
                {/* Hostname */}
                <td className="px-4 py-2 whitespace-nowrap">
                  <div className="flex items-center">
                    <span className={`w-2 h-2 rounded-full mr-2 ${
                      sensor.is_active ? 'bg-green-500' : 'bg-red-500'
                    }`} />
                    <span className="font-medium">{sensor.hostname}</span>
                  </div>
                </td>

                {/* Status */}
                <td className="px-4 py-2 whitespace-nowrap">
                  {!sensor.sensor_version ? (
                    <span className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded">
                      No Sensor
                    </span>
                  ) : sensor.is_active ? (
                    <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded">
                      Active
                    </span>
                  ) : (
                    <span className="px-2 py-1 text-xs bg-red-100 text-red-700 rounded">
                      Offline
                    </span>
                  )}
                </td>

                {/* Version */}
                <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500">
                  {sensor.sensor_version || '-'}
                </td>

                {/* Compliance */}
                <td className="px-4 py-2 whitespace-nowrap">
                  {!sensor.sensor_version ? (
                    <span className="text-gray-400">-</span>
                  ) : sensor.last_compliant ? (
                    <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded">
                      Compliant
                    </span>
                  ) : (
                    <span className="px-2 py-1 text-xs bg-red-100 text-red-700 rounded">
                      {sensor.last_drift_count} Drifts
                    </span>
                  )}
                </td>

                {/* Last Seen */}
                <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500">
                  {sensor.last_heartbeat ? formatAge(sensor.age_seconds) : '-'}
                </td>

                {/* Mode */}
                <td className="px-4 py-2 whitespace-nowrap">
                  {sensor.sensor_version ? (
                    <span
                      className="px-2 py-1 text-xs bg-blue-100 text-blue-700 rounded cursor-help"
                      title="Drift detection via sensor push (instant)"
                    >
                      Sensor
                    </span>
                  ) : (
                    <span
                      className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded cursor-help"
                      title="Drift detection via WinRM polling (60s)"
                    >
                      WinRM Poll
                    </span>
                  )}
                </td>

                {/* Actions */}
                <td className="px-4 py-2 whitespace-nowrap">
                  {!sensor.sensor_version ? (
                    <button
                      onClick={() => deploySensor(sensor.hostname)}
                      disabled={deployingHost === sensor.hostname}
                      className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
                    >
                      {deployingHost === sensor.hostname ? 'Deploying...' : 'Deploy'}
                    </button>
                  ) : (
                    <button
                      onClick={() => removeSensor(sensor.hostname)}
                      className="px-2 py-1 text-xs bg-red-100 text-red-600 rounded hover:bg-red-200"
                    >
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}

            {sensors.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                  No Windows targets configured.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="mt-4 pt-4 border-t text-sm text-gray-500">
        <p className="mb-1">
          <strong>Sensor Mode:</strong> Instant drift detection via lightweight PowerShell agent.
          Remediation still uses WinRM.
        </p>
        <p>
          <strong>WinRM Poll Mode:</strong> Drift detection every 60 seconds via WinRM.
          Used when sensor not deployed or offline.
        </p>
      </div>
    </div>
  );
};

export default SensorStatus;
