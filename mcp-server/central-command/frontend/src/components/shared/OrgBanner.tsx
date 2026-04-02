import React from 'react';
import { Link } from 'react-router-dom';
import { useSite } from '../../hooks';

/**
 * Org context banner — shown on site-scoped pages when the site belongs to
 * a multi-site organization. Provides a link to the org dashboard for
 * aggregate views across all sites/appliances.
 */
export const OrgBanner: React.FC<{ siteId: string }> = ({ siteId }) => {
  const { data: site } = useSite(siteId);
  if (!site?.client_org_id) return null;
  return (
    <div className="bg-accent-primary/10 border border-accent-primary/20 rounded-ios px-4 py-2 flex items-center justify-between">
      <span className="text-sm text-label-secondary">
        Part of <span className="font-medium text-label-primary">{site.org_name || 'organization'}</span> — viewing this site only
      </span>
      <Link
        to={`/organizations/${site.client_org_id}`}
        className="text-sm text-accent-primary hover:underline font-medium"
      >
        View all org sites
      </Link>
    </div>
  );
};
