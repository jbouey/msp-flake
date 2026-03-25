import React, { ReactNode } from 'react';
import { WHITE_LABEL } from '../constants';
import type { PartnerBranding } from '../hooks/useBranding';

interface BrandedLayoutProps {
  branding: PartnerBranding | null;
  children: ReactNode;
}

export const BrandedLayout: React.FC<BrandedLayoutProps> = ({ branding, children }) => {
  const brandName = branding?.brand_name ?? WHITE_LABEL.DEFAULT_BRAND;
  const primaryColor = branding?.primary_color ?? WHITE_LABEL.DEFAULT_PRIMARY;
  const logoUrl = branding?.logo_url ?? null;
  const supportEmail = branding?.support_email ?? null;
  const supportPhone = branding?.support_phone ?? null;

  return (
    <div className="min-h-screen flex flex-col bg-background-primary">
      {/* Accent bar */}
      <div className="h-1 w-full" style={{ backgroundColor: primaryColor }} />

      {/* Header */}
      <header className="flex items-center gap-3 px-6 py-3 border-b border-slate-200 bg-white">
        {logoUrl ? (
          <img
            src={logoUrl}
            alt={`${brandName} logo`}
            className="h-8 w-auto object-contain"
          />
        ) : (
          <div
            className="h-8 w-8 rounded-lg flex items-center justify-center text-white font-bold text-sm"
            style={{ backgroundColor: primaryColor }}
          >
            {brandName.charAt(0)}
          </div>
        )}
        <span className="text-lg font-semibold text-slate-900">{brandName}</span>
      </header>

      {/* Main content */}
      <main className="flex-1">
        {children}
      </main>

      {/* Footer */}
      <footer className="px-6 py-4 border-t border-slate-200 bg-white">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-2">
          <p className="text-xs text-slate-400">
            {WHITE_LABEL.POWERED_BY}
          </p>
          {(supportEmail || supportPhone) && (
            <div className="flex items-center gap-4 text-xs text-slate-500">
              {supportEmail && (
                <a href={`mailto:${supportEmail}`} className="hover:text-slate-700 transition-colors">
                  {supportEmail}
                </a>
              )}
              {supportPhone && (
                <a href={`tel:${supportPhone}`} className="hover:text-slate-700 transition-colors">
                  {supportPhone}
                </a>
              )}
            </div>
          )}
        </div>
      </footer>
    </div>
  );
};

export default BrandedLayout;
