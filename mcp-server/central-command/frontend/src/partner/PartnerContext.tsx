import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { useIdleTimeout } from '../hooks/useIdleTimeout';
import { IdleTimeoutWarning } from '../components/shared/IdleTimeoutWarning';

interface Partner {
  id: string;
  name: string;
  slug: string;
  brand_name: string;
  primary_color: string;
  logo_url: string | null;
  contact_email: string;
  email?: string;  // OAuth email
  auth_provider?: 'microsoft' | 'google' | 'api_key' | null;
  tenant_id?: string | null;
  revenue_share_percent: number;
  site_count: number;
  provisions: {
    pending: number;
    claimed: number;
  };
}

interface PartnerContextType {
  partner: Partner | null;
  apiKey: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (apiKey: string) => Promise<boolean>;
  logout: () => void;
  checkSession: () => Promise<boolean>;
}

const PartnerContext = createContext<PartnerContextType | undefined>(undefined);

const STORAGE_KEY = 'partner_api_key';

export const PartnerProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [partner, setPartner] = useState<Partner | null>(null);
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check for OAuth session first, then fall back to API key
  useEffect(() => {
    const initAuth = async () => {
      // First try OAuth session (cookie-based)
      const sessionValid = await checkSessionAuth();
      if (sessionValid) {
        setIsLoading(false);
        return;
      }

      // Fall back to API key
      const storedKey = localStorage.getItem(STORAGE_KEY);
      if (storedKey) {
        await validateAndLoadPartner(storedKey);
      } else {
        setIsLoading(false);
      }
    };

    initAuth();
  }, []);

  const checkSessionAuth = async (): Promise<boolean> => {
    try {
      const response = await fetch('/api/partner-auth/me', {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        // OAuth session valid - set partner data
        setPartner({
          id: data.id,
          name: data.name,
          slug: data.slug,
          brand_name: data.brand_name || data.name,
          email: data.email,
          auth_provider: data.auth_provider,
          tenant_id: data.tenant_id,
          primary_color: '#4F46E5',
          logo_url: null,
          contact_email: data.email,
          revenue_share_percent: 40,
          site_count: 0,
          provisions: { pending: 0, claimed: 0 },
        });
        return true;
      }
    } catch (e) {
      console.error('Session check failed:', e);
    }
    return false;
  };

  const validateAndLoadPartner = async (key: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/partners/me', {
        headers: { 'X-API-Key': key },
      });

      if (response.ok) {
        const data = await response.json();
        setPartner(data);
        setApiKey(key);
        localStorage.setItem(STORAGE_KEY, key);
      } else {
        localStorage.removeItem(STORAGE_KEY);
        setError('Invalid or expired API key');
        setPartner(null);
        setApiKey(null);
      }
    } catch (e) {
      setError('Failed to connect to server');
    } finally {
      setIsLoading(false);
    }
  };

  const login = async (key: string): Promise<boolean> => {
    await validateAndLoadPartner(key);
    return partner !== null;
  };

  const logout = async () => {
    // Clear API key
    localStorage.removeItem(STORAGE_KEY);

    // Clear OAuth session
    try {
      await fetch('/api/partner-auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
    } catch (e) {
      console.error('Logout request failed:', e);
    }

    setPartner(null);
    setApiKey(null);
    setError(null);
  };

  const checkSession = async (): Promise<boolean> => {
    return checkSessionAuth();
  };

  const handleIdleTimeout = useCallback(async () => {
    await logout();
  }, []);

  const { showWarning, remainingSeconds, dismissWarning } = useIdleTimeout({
    onTimeout: handleIdleTimeout,
    enabled: partner !== null,
  });

  return (
    <PartnerContext.Provider
      value={{
        partner,
        apiKey,
        isAuthenticated: partner !== null,
        isLoading,
        error,
        login,
        logout,
        checkSession,
      }}
    >
      {showWarning && (
        <IdleTimeoutWarning remainingSeconds={remainingSeconds} onDismiss={dismissWarning} />
      )}
      {children}
    </PartnerContext.Provider>
  );
};

export const usePartner = () => {
  const context = useContext(PartnerContext);
  if (context === undefined) {
    throw new Error('usePartner must be used within a PartnerProvider');
  }
  return context;
};
