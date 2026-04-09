import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Badge, ActionDropdown, Tooltip } from '../../../components/shared';
import type { SiteDetail as SiteDetailType } from '../../../utils/api';

export interface SiteHeaderProps {
  site: SiteDetailType;
  siteId: string | undefined;
  isWgConnected: boolean;
  isGeneratingLink: boolean;
  deviceTabCount: number | null;
  workstationTabCount: number | null;
  agentTabCount: number | null;
  protectionTabCount: number | null;
  onEditSite: () => void;
  onGeneratePortalLink: () => void;
  onTransferAppliance: () => void;
  onDecommissionSite: () => void;
}

/**
 * Site detail header — decommissioned banner, breadcrumb, identity row, tab nav.
 */
export const SiteHeader: React.FC<SiteHeaderProps> = ({
  site,
  siteId,
  isWgConnected,
  isGeneratingLink,
  deviceTabCount,
  workstationTabCount,
  agentTabCount,
  protectionTabCount,
  onEditSite,
  onGeneratePortalLink,
  onTransferAppliance,
  onDecommissionSite,
}) => {
  const navigate = useNavigate();

  return (
    <>
      {/* Decommissioned banner */}
      {site.status === 'inactive' && (
        <div className="bg-health-critical/10 border border-health-critical/20 rounded-ios p-4 flex items-center gap-3">
          <svg className="w-5 h-5 text-health-critical flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
          </svg>
          <div>
            <p className="text-sm font-semibold text-health-critical">This site has been decommissioned</p>
            <p className="text-xs text-label-tertiary mt-0.5">
              Status is inactive. API keys revoked, portal tokens invalidated. Data retained for HIPAA compliance.
            </p>
          </div>
        </div>
      )}

      {/* Breadcrumb — Sites / [Org] / [Site] */}
      <nav aria-label="Breadcrumb" className="text-xs text-label-tertiary flex items-center gap-1.5 flex-wrap">
        <Link to="/sites" className="hover:text-label-secondary transition-colors">
          Sites
        </Link>
        {site.client_org_id && site.org_name && (
          <>
            <span aria-hidden>/</span>
            <Link
              to={`/organizations/${site.client_org_id}`}
              className="hover:text-label-secondary transition-colors truncate max-w-xs"
              title={site.org_name}
            >
              {site.org_name}
            </Link>
          </>
        )}
        <span aria-hidden>/</span>
        <span className="text-label-secondary truncate max-w-xs" title={site.clinic_name}>
          {site.clinic_name}
        </span>
      </nav>

      {/* Header */}
      <div className="space-y-0">
        {/* Row 1: Site identity + status + action */}
        <div className="flex items-start gap-4">
          <button
            onClick={() => navigate('/sites')}
            className="p-2 mt-1 rounded-ios-sm hover:bg-fill-secondary transition-colors"
          >
            <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-semibold text-label-primary truncate">{site.clinic_name}</h1>
              <Badge variant={site.live_status === 'online' ? 'success' : site.live_status === 'offline' ? 'error' : 'default'}>
                {site.live_status === 'online' ? 'Online' : site.live_status === 'offline' ? 'Offline' : site.live_status?.replace(/\b\w/g, c => c.toUpperCase()) || 'Unknown'}
              </Badge>
              {site.wg_ip && (
                <Tooltip text={`WireGuard tunnel · ${site.wg_ip}${isWgConnected ? ' · Handshake < 5m' : ' · Stale'}`}>
                  <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full cursor-help ${
                    isWgConnected ? 'bg-health-healthy/10 text-health-healthy' : 'bg-fill-secondary text-label-tertiary'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${isWgConnected ? 'bg-health-healthy' : 'bg-label-tertiary'}`} />
                    {isWgConnected ? 'VPN Connected' : 'VPN Stale'}
                  </span>
                </Tooltip>
              )}
            </div>
            {/* site_id hidden from main view — visible in Edit modal */}
          </div>
          <div className="flex items-center gap-2">
            {/* Primary: Edit Site */}
            <button
              onClick={onEditSite}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-label-primary bg-fill-secondary hover:bg-fill-tertiary rounded-ios-sm transition-colors whitespace-nowrap font-medium"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
              Edit Site
            </button>

            {/* Secondary actions collapsed into a More menu */}
            <ActionDropdown
              label="More"
              actions={[
                {
                  label: isGeneratingLink ? 'Generating…' : 'Portal Link',
                  icon: (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                    </svg>
                  ),
                  disabled: isGeneratingLink,
                  onClick: onGeneratePortalLink,
                },
                ...(site.status !== 'inactive' && site.appliances.length > 0 ? [{
                  label: 'Transfer Appliance',
                  icon: (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                    </svg>
                  ),
                  onClick: onTransferAppliance,
                }] : []),
                ...(site.status !== 'inactive' ? [{
                  label: 'Decommission Site',
                  icon: (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                    </svg>
                  ),
                  danger: true,
                  onClick: onDecommissionSite,
                }] : []),
              ]}
            />
          </div>
        </div>

        {/* Row 2: Navigation pills with count badges */}
        <div className="overflow-x-auto -mx-4 px-4 mt-4 pt-3 border-t border-separator-light">
        <nav className="flex items-center gap-1.5 min-w-max">
          <Link
            to={`/sites/${siteId}/devices`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
            Devices
            {deviceTabCount !== null && (
              <span className="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 min-w-[20px] text-[10px] font-semibold rounded-full bg-fill-tertiary text-label-secondary">
                {deviceTabCount}
              </span>
            )}
          </Link>
          <Link
            to={`/sites/${siteId}/workstations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Workstations
            {workstationTabCount !== null && (
              <span className="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 min-w-[20px] text-[10px] font-semibold rounded-full bg-fill-tertiary text-label-secondary">
                {workstationTabCount}
              </span>
            )}
          </Link>
          <Link
            to={`/sites/${siteId}/agents`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
            </svg>
            Go Agents
            {agentTabCount !== null && (
              <span className="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 min-w-[20px] text-[10px] font-semibold rounded-full bg-fill-tertiary text-label-secondary">
                {agentTabCount}
              </span>
            )}
          </Link>
          <Link
            to={`/sites/${siteId}/protection`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
            App Protection
            {protectionTabCount !== null && (
              <span className="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 min-w-[20px] text-[10px] font-semibold rounded-full bg-fill-tertiary text-label-secondary">
                {protectionTabCount}
              </span>
            )}
          </Link>
          <Link
            to={`/sites/${siteId}/drift-config`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Check Config
          </Link>
          <div className="w-px h-5 bg-separator-medium mx-0.5 flex-shrink-0" />
          <Link
            to={`/sites/${siteId}/frameworks`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            Frameworks
          </Link>
          <Link
            to={`/sites/${siteId}/integrations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Cloud Integrations
          </Link>
        </nav>
        </div>
      </div>
    </>
  );
};
