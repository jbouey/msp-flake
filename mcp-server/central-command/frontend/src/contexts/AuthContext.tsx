import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface User {
  id?: string;
  username: string;
  role: 'admin' | 'operator' | 'readonly' | 'companion';
  displayName: string;
}

interface AuditLog {
  id: number;
  timestamp: string;
  user: string;
  action: string;
  target: string;
  details?: string;
  ip?: string;
}

interface OAuthIdentity {
  provider: 'google' | 'microsoft';
  email: string;
  name: string | null;
  linked_at: string | null;
  last_login_at: string | null;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  setTokenFromOAuth: (token: string) => void;
  oauthIdentities: OAuthIdentity[];
  refreshOAuthIdentities: () => Promise<void>;
  auditLogs: AuditLog[];
  refreshAuditLogs: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// API base URL - uses relative path to work with proxy
const API_BASE = '/api';

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [oauthIdentities, setOauthIdentities] = useState<OAuthIdentity[]>([]);

  // Clear legacy localStorage token if present
  const clearLegacyToken = () => {
    localStorage.removeItem('auth_token');
  };

  // Check for existing session on mount
  useEffect(() => {
    clearLegacyToken();

    const validateSession = async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          credentials: 'same-origin',
        });

        if (response.ok) {
          const userData = await response.json();
          setUser({
            id: userData.id,
            username: userData.username,
            role: userData.role,
            displayName: userData.displayName,
          });
        } else {
          setUser(null);
        }
      } catch (error) {
        console.error('Session validation failed:', error);
      } finally {
        setIsLoading(false);
      }
    };

    validateSession();
  }, []);

  const refreshAuditLogs = async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/audit-logs?limit=100`, {
        credentials: 'same-origin',
      });

      if (response.ok) {
        const data = await response.json();
        setAuditLogs(data.logs || []);
      }
    } catch (error) {
      console.error('Failed to fetch audit logs:', error);
    }
  };

  const login = async (username: string, password: string): Promise<{ success: boolean; error?: string }> => {
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'same-origin',
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (data.success && data.user) {
        setUser({
          id: data.user.id,
          username: data.user.username,
          role: data.user.role,
          displayName: data.user.displayName,
        });
        return { success: true };
      }

      return { success: false, error: data.error || 'Invalid username or password' };
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: 'Network error. Please try again.' };
    }
  };

  const logout = async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        credentials: 'same-origin',
      });
    } catch (error) {
      console.error('Logout request failed:', error);
    }

    clearLegacyToken();
    setUser(null);
    setAuditLogs([]);
    setOauthIdentities([]);
  };

  // Validate session after OAuth callback (cookie already set by server)
  const setTokenFromOAuth = (_token: string) => {
    const validateAndSetUser = async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          credentials: 'same-origin',
        });

        if (response.ok) {
          const userData = await response.json();
          setUser({
            id: userData.id,
            username: userData.username,
            role: userData.role,
            displayName: userData.displayName,
          });
        }
      } catch (error) {
        console.error('OAuth session validation failed:', error);
      }
    };
    validateAndSetUser();
  };

  const refreshOAuthIdentities = async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/oauth/identities`, {
        credentials: 'same-origin',
      });

      if (response.ok) {
        const data = await response.json();
        setOauthIdentities(data.identities || []);
      }
    } catch (error) {
      console.error('Failed to fetch OAuth identities:', error);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        logout,
        setTokenFromOAuth,
        oauthIdentities,
        refreshOAuthIdentities,
        auditLogs,
        refreshAuditLogs,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
