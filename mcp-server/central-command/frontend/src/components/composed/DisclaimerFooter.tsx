import React from 'react';
import { DISCLAIMERS, BRANDING } from '../../constants';

interface DisclaimerFooterProps {
  showBranding?: boolean;
  className?: string;
}

/**
 * DisclaimerFooter -- reusable legal footer.
 *
 * Reads from DISCLAIMERS.footer. Change the constant, every page updates.
 */
export const DisclaimerFooter: React.FC<DisclaimerFooterProps> = ({
  showBranding = true,
  className = '',
}) => {
  return (
    <footer className={`mt-8 pt-4 border-t border-separator-light ${className}`}>
      {showBranding && (
        <p className="text-xs text-label-tertiary mb-2">
          Powered by {BRANDING.name} {BRANDING.tagline}
        </p>
      )}
      <p className="text-[10px] text-label-tertiary leading-relaxed">
        {DISCLAIMERS.footer}
      </p>
    </footer>
  );
};

export default DisclaimerFooter;
