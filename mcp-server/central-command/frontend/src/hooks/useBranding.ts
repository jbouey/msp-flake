import { useState, useEffect, useCallback } from 'react';
import { WHITE_LABEL } from '../constants';

export interface PartnerBranding {
  brand_name: string;
  logo_url: string | null;
  primary_color: string;
  secondary_color: string;
  tagline: string | null;
  support_email: string | null;
  support_phone: string | null;
  partner_slug: string;
}

const CACHE_KEY_PREFIX = 'partner_branding_';

function getCached(slug: string): PartnerBranding | null {
  try {
    const raw = window.sessionStorage.getItem(CACHE_KEY_PREFIX + slug);
    if (raw) return JSON.parse(raw) as PartnerBranding;
  } catch {
    // Corrupted cache — ignore
  }
  return null;
}

function setCache(slug: string, branding: PartnerBranding): void {
  try {
    window.sessionStorage.setItem(CACHE_KEY_PREFIX + slug, JSON.stringify(branding));
  } catch {
    // Storage full — non-critical
  }
}

function getDefaultBranding(): PartnerBranding {
  return {
    brand_name: WHITE_LABEL.DEFAULT_BRAND,
    logo_url: null,
    primary_color: WHITE_LABEL.DEFAULT_PRIMARY,
    secondary_color: WHITE_LABEL.DEFAULT_SECONDARY,
    tagline: WHITE_LABEL.DEFAULT_TAGLINE,
    support_email: null,
    support_phone: null,
    partner_slug: '',
  };
}

export function useBranding(slug?: string): {
  branding: PartnerBranding | null;
  loading: boolean;
  applyTheme: () => void;
} {
  const [branding, setBranding] = useState<PartnerBranding | null>(() => {
    if (!slug) return getDefaultBranding();
    return getCached(slug) ?? null;
  });
  const [loading, setLoading] = useState(!!slug && !getCached(slug));

  useEffect(() => {
    if (!slug) {
      setBranding(getDefaultBranding());
      setLoading(false);
      return;
    }

    const cached = getCached(slug);
    if (cached) {
      setBranding(cached);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    fetch(`/api/portal/branding/${encodeURIComponent(slug)}`)
      .then((res) => {
        if (!res.ok) throw new Error('Branding not found');
        return res.json();
      })
      .then((data: PartnerBranding) => {
        if (cancelled) return;
        setBranding(data);
        setCache(slug, data);
      })
      .catch(() => {
        if (cancelled) return;
        setBranding(getDefaultBranding());
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  const applyTheme = useCallback(() => {
    if (!branding) return;
    document.documentElement.style.setProperty('--brand-primary', branding.primary_color);
    document.documentElement.style.setProperty('--brand-secondary', branding.secondary_color);
  }, [branding]);

  // Auto-apply theme when branding changes
  useEffect(() => {
    if (branding) {
      document.documentElement.style.setProperty('--brand-primary', branding.primary_color);
      document.documentElement.style.setProperty('--brand-secondary', branding.secondary_color);
    }
  }, [branding]);

  return { branding, loading, applyTheme };
}
