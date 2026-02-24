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

// HTTP-only cookies are now the preferred auth method
// localStorage is kept only for backwards compatibility during transition
const USE_COOKIE_AUTH = true;

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
      // With cookie auth, we don't need a token in localStorage
      // But check for backwards compatibility
      const token = getToken();

      try {
        const headers: Record<string, string> = {};
        // Only add Authorization header if not using cookie auth
        if (!USE_COOKIE_AUTH && token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE}/auth/me`, {
          headers,
          // Include credentials to send HTTP-only cookies
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
          // Invalid token/session - clear localStorage
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
    try {
      const headers: Record<string, string> = {};
      if (!USE_COOKIE_AUTH) {
        const token = getToken();
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
      }

      const response = await fetch(`${API_BASE}/auth/audit-logs?limit=100`, {
        headers,
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

  const login = async (username: string, password: string): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        // Include credentials to receive the HTTP-only cookie
        credentials: 'same-origin',
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (data.success && data.user) {
        // Store token in localStorage for backwards compatibility
        // The HTTP-only cookie is the primary auth method now
        if (data.token) {
          setToken(data.token);
        }
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
    try {
      const headers: Record<string, string> = {};
      if (!USE_COOKIE_AUTH) {
        const token = getToken();
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
      }

      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        headers,
        // Include credentials to clear the HTTP-only cookie
        credentials: 'same-origin',
      });
    } catch (error) {
      console.error('Logout request failed:', error);
    }

    // Clear localStorage token (for backwards compatibility)
    setToken(null);
    setUser(null);
    setAuditLogs([]);
    setOauthIdentities([]);
  };

  // Set token from OAuth callback and validate session
  const setTokenFromOAuth = (token: string) => {
    // Store token in localStorage for backwards compatibility
    setToken(token);
    // Trigger session validation to load user data
    const validateAndSetUser = async () => {
      try {
        const headers: Record<string, string> = {};
        if (!USE_COOKIE_AUTH) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE}/auth/me`, {
          headers,
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

  // Fetch OAuth identities linked to current user
  const refreshOAuthIdentities = async () => {
    try {
      const headers: Record<string, string> = {};
      if (!USE_COOKIE_AUTH) {
        const token = getToken();
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
      }

      const response = await fetch(`${API_BASE}/auth/oauth/identities`, {
        headers,
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
