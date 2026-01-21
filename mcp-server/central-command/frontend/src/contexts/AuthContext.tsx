import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface User {
  id?: string;
  username: string;
  role: 'admin' | 'operator' | 'readonly';
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
  login: (username: string, password: string) => Promise<boolean>;
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

  // Get stored token
  const getToken = (): string | null => {
    return localStorage.getItem('auth_token');
  };

  // Set token
  const setToken = (token: string | null) => {
    if (token) {
      localStorage.setItem('auth_token', token);
    } else {
      localStorage.removeItem('auth_token');
    }
  };

  // Check for existing session on mount
  useEffect(() => {
    const validateSession = async () => {
      const token = getToken();
      if (!token) {
        setIsLoading(false);
        return;
      }

      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          headers: {
            'Authorization': `Bearer ${token}`,
          },
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
          // Invalid token - clear it
          setToken(null);
          setUser(null);
        }
      } catch (error) {
        console.error('Session validation failed:', error);
        // Don't clear token on network error - might be temporary
      } finally {
        setIsLoading(false);
      }
    };

    validateSession();
  }, []);

  const refreshAuditLogs = async () => {
    const token = getToken();
    if (!token) return;

    try {
      const response = await fetch(`${API_BASE}/auth/audit-logs?limit=100`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setAuditLogs(data.logs || []);
      }
    } catch (error) {
      console.error('Failed to fetch audit logs:', error);
    }
  };

  const login = async (username: string, password: string): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (data.success && data.token && data.user) {
        setToken(data.token);
        setUser({
          id: data.user.id,
          username: data.user.username,
          role: data.user.role,
          displayName: data.user.displayName,
        });
        return true;
      }

      return false;
    } catch (error) {
      console.error('Login failed:', error);
      return false;
    }
  };

  const logout = async () => {
    const token = getToken();

    if (token) {
      try {
        await fetch(`${API_BASE}/auth/logout`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
          },
        });
      } catch (error) {
        console.error('Logout request failed:', error);
      }
    }

    setToken(null);
    setUser(null);
    setAuditLogs([]);
    setOauthIdentities([]);
  };

  // Set token from OAuth callback and validate session
  const setTokenFromOAuth = (token: string) => {
    setToken(token);
    // Trigger session validation to load user data
    const validateAndSetUser = async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          headers: {
            'Authorization': `Bearer ${token}`,
          },
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

  // Fetch OAuth identities linked to current user
  const refreshOAuthIdentities = async () => {
    const token = getToken();
    if (!token) return;

    try {
      const response = await fetch(`${API_BASE}/auth/oauth/identities`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
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
