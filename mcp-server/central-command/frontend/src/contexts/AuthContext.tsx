import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface User {
  username: string;
  role: 'admin' | 'operator';
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

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
  auditLogs: AuditLog[];
  addAuditLog: (action: string, target: string, details?: string) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Default admin user (in production, this would be from a database)
const USERS: Record<string, { password: string; role: 'admin' | 'operator'; displayName: string }> = {
  admin: { password: 'admin', role: 'admin', displayName: 'Administrator' },
};

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);

  // Check for existing session on mount
  useEffect(() => {
    const savedUser = localStorage.getItem('auth_user');
    if (savedUser) {
      try {
        setUser(JSON.parse(savedUser));
      } catch {
        localStorage.removeItem('auth_user');
      }
    }

    // Load audit logs
    const savedLogs = localStorage.getItem('audit_logs');
    if (savedLogs) {
      try {
        setAuditLogs(JSON.parse(savedLogs));
      } catch {
        localStorage.removeItem('audit_logs');
      }
    }
  }, []);

  const addAuditLog = (action: string, target: string, details?: string) => {
    const newLog: AuditLog = {
      id: Date.now(),
      timestamp: new Date().toISOString(),
      user: user?.username || 'anonymous',
      action,
      target,
      details,
    };

    setAuditLogs((prev) => {
      const updated = [newLog, ...prev].slice(0, 1000); // Keep last 1000 logs
      localStorage.setItem('audit_logs', JSON.stringify(updated));
      return updated;
    });
  };

  const login = async (username: string, password: string): Promise<boolean> => {
    const userData = USERS[username];
    if (userData && userData.password === password) {
      const loggedInUser: User = {
        username,
        role: userData.role,
        displayName: userData.displayName,
      };
      setUser(loggedInUser);
      localStorage.setItem('auth_user', JSON.stringify(loggedInUser));

      // Log the login
      const loginLog: AuditLog = {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        user: username,
        action: 'LOGIN',
        target: 'Authentication',
        details: 'User logged in successfully',
      };
      setAuditLogs((prev) => {
        const updated = [loginLog, ...prev].slice(0, 1000);
        localStorage.setItem('audit_logs', JSON.stringify(updated));
        return updated;
      });

      return true;
    }
    return false;
  };

  const logout = () => {
    if (user) {
      addAuditLog('LOGOUT', 'Authentication', 'User logged out');
    }
    setUser(null);
    localStorage.removeItem('auth_user');
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        login,
        logout,
        auditLogs,
        addAuditLog,
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
