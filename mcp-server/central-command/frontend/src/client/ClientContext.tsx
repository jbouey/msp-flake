import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface ClientOrg {
  id: string;
  name: string;
}

interface ClientUser {
  id: string;
  email: string;
  name: string | null;
  role: 'owner' | 'admin' | 'viewer';
  org: ClientOrg;
}

interface ClientContextType {
  user: ClientUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  logout: () => Promise<void>;
  checkSession: () => Promise<boolean>;
}

const ClientContext = createContext<ClientContextType | undefined>(undefined);

export const ClientProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<ClientUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    checkSession();
  }, []);

  const checkSession = async (): Promise<boolean> => {
    setIsLoading(true);
    try {
      const response = await fetch('/api/client/auth/me', {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        setUser({
          id: data.id,
          email: data.email,
          name: data.name,
          role: data.role,
          org: {
            id: data.org.id,
            name: data.org.name,
          },
        });
        setError(null);
        return true;
      } else {
        setUser(null);
        return false;
      }
    } catch (e) {
      console.error('Session check failed:', e);
      setUser(null);
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    try {
      await fetch('/api/client/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
    } catch (e) {
      console.error('Logout request failed:', e);
    }

    setUser(null);
    setError(null);
  };

  return (
    <ClientContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        error,
        logout,
        checkSession,
      }}
    >
      {children}
    </ClientContext.Provider>
  );
};

export const useClient = () => {
  const context = useContext(ClientContext);
  if (context === undefined) {
    throw new Error('useClient must be used within a ClientProvider');
  }
  return context;
};
