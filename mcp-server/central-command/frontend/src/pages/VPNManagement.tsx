import React, { useState } from 'react';
import { GlassCard, StatCard, Spinner } from '../components/shared';
import { useVPNStatus, useRotateVPNKey } from '../hooks';
import type { VPNPeer } from '../utils/api';
import { formatTimeAgo, formatBytes } from '../constants';

const formatRelativeTime = formatTimeAgo;

const ConnectionDot: React.FC<{ connected: boolean }> = ({ connected }) => (
  <span
    className={`inline-block w-2.5 h-2.5 rounded-full ${
      connected
        ? 'bg-health-healthy shadow-[0_0_6px_rgba(52,199,89,0.5)]'
        : 'bg-health-critical'
    }`}
    title={connected ? 'Connected' : 'Disconnected'}
  />
);

const PeerRow: React.FC<{
  peer: VPNPeer;
  onRotateKey: (siteId: string) => void;
  isRotating: boolean;
}> = ({ peer, onRotateKey, isRotating }) => {
  const [confirmRotate, setConfirmRotate] = useState(false);

  const handleRotate = () => {
    if (!confirmRotate) {
      setConfirmRotate(true);
      return;
    }
    onRotateKey(peer.site_id);
    setConfirmRotate(false);
  };

  return (
    <tr className="border-b border-separator-light last:border-b-0 hover:bg-fill-quaternary transition-colors">
      <td className="px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-label-primary">{peer.clinic_name}</span>
          {peer.appliance_count > 1 && (
            <span className="inline-flex items-center gap-1 text-xs text-label-tertiary">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M12 5l7 7-7 7" />
              </svg>
              {peer.appliance_count} appliances
            </span>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm text-label-secondary font-mono tabular-nums">{peer.wg_ip}</span>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <ConnectionDot connected={peer.connected} />
          <span className={`text-sm font-medium ${peer.connected ? 'text-health-healthy' : 'text-health-critical'}`}>
            {peer.connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm text-label-secondary tabular-nums">
          {peer.last_handshake ? formatRelativeTime(peer.last_handshake) : 'No handshake yet'}
        </span>
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-col">
          <span className="text-xs text-label-secondary tabular-nums">
            {formatBytes(peer.bytes_received)} rx
          </span>
          <span className="text-xs text-label-tertiary tabular-nums">
            {formatBytes(peer.bytes_sent)} tx
          </span>
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm text-label-secondary font-mono tabular-nums">
          {peer.endpoint || 'Not configured'}
        </span>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm text-label-secondary tabular-nums">
          {peer.agent_version || 'Not available'}
        </span>
      </td>
      <td className="px-4 py-3">
        {confirmRotate ? (
          <div className="flex items-center gap-2">
            <button
              onClick={handleRotate}
              disabled={isRotating}
              className="px-2.5 py-1 text-xs font-medium rounded-ios-sm bg-health-critical text-white hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {isRotating ? 'Sending...' : 'Confirm'}
            </button>
            <button
              onClick={() => setConfirmRotate(false)}
              className="px-2.5 py-1 text-xs font-medium rounded-ios-sm bg-fill-tertiary text-label-secondary hover:bg-fill-secondary transition-colors"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={handleRotate}
            className="px-2.5 py-1 text-xs font-medium rounded-ios-sm bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors"
            title="Rotate WireGuard key"
          >
            Rotate Key
          </button>
        )}
      </td>
    </tr>
  );
};

export const VPNManagement: React.FC = () => {
  const { data, isLoading, error } = useVPNStatus();
  const rotateKey = useRotateVPNKey();

  const handleRotateKey = (siteId: string) => {
    rotateKey.mutate(siteId);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] text-center">
        <p className="text-label-primary text-lg font-semibold mb-2">Failed to load VPN status</p>
        <p className="text-label-tertiary text-sm">
          {error instanceof Error ? error.message : 'Unknown error'}
        </p>
      </div>
    );
  }

  const peers = data?.peers ?? [];
  const total = data?.total ?? 0;
  const connected = data?.connected ?? 0;
  const disconnected = data?.disconnected ?? 0;

  return (
    <div className="space-y-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          label="Total Peers"
          value={total}
          icon={
            <svg className="w-5 h-5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          }
          color="#14A89E"
        />
        <StatCard
          label="Connected"
          value={connected}
          icon={
            <svg className="w-5 h-5 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          }
          color="#34C759"
        />
        <StatCard
          label="Disconnected"
          value={disconnected}
          icon={
            <svg className="w-5 h-5 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
          color="#FF3B30"
        />
      </div>

      {/* Peer Table */}
      <GlassCard padding="none">
        <div className="px-6 py-4 border-b border-separator-light">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-label-primary">WireGuard Peers</h2>
              <p className="text-xs text-label-tertiary mt-0.5">Real-time tunnel status across the fleet</p>
            </div>
            <span className="text-xs text-label-tertiary">Auto-refreshes every 30s</span>
          </div>
        </div>

        {peers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
            <svg className="w-12 h-12 text-label-quaternary mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <p className="text-label-secondary font-medium">No WireGuard peers configured</p>
            <p className="text-label-tertiary text-sm mt-1">
              Sites with WireGuard tunnels will appear here once provisioned.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-separator-medium bg-fill-quaternary">
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Site</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">VPN IP</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Status</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Handshake</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Traffic</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Endpoint</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Agent</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody>
                {peers.map((peer) => (
                  <PeerRow
                    key={peer.site_id}
                    peer={peer}
                    onRotateKey={handleRotateKey}
                    isRotating={rotateKey.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* Mutation feedback */}
      {rotateKey.isSuccess && (
        <div className="fixed bottom-4 right-4 bg-health-healthy text-white px-4 py-2 rounded-ios-md shadow-lg text-sm font-medium animate-fade-in">
          Key rotation ordered successfully
        </div>
      )}
      {rotateKey.isError && (
        <div className="fixed bottom-4 right-4 bg-health-critical text-white px-4 py-2 rounded-ios-md shadow-lg text-sm font-medium animate-fade-in">
          Failed to order key rotation
        </div>
      )}
    </div>
  );
};

export default VPNManagement;
