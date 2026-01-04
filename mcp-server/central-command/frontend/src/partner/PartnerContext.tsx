import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface Partner {
  id: string;
  name: string;
  slug: string;
  brand_name: string;
  primary_color: string;
  logo_url: string | null;
  contact_email: string;
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
}

const PartnerContext = createContext<PartnerContextType | undefined>(undefined);

const STORAGE_KEY = 'partner_api_key';

export const PartnerProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [partner, setPartner] = useState<Partner | null>(null);
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load API key from storage on mount
  useEffect(() => {
    const storedKey = localStorage.getItem(STORAGE_KEY);
    if (storedKey) {
      validateAndLoadPartner(storedKey);
    } else {
      setIsLoading(false);
    }
  }, []);

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

  const logout = () => {
    localStorage.removeItem(STORAGE_KEY);
    setPartner(null);
    setApiKey(null);
    setError(null);
  };

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
      }}
    >
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
