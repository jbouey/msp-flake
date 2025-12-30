import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { CommandBar } from './components/command';
import { Dashboard, Runbooks, Learning, Onboarding, ClientDetail, Login, AuditLogs } from './pages';
import { useFleet, useRefreshFleet, useCommandPalette } from './hooks';
import { AuthProvider, useAuth } from './contexts/AuthContext';

// Create a client for React Query
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

// Page titles for header
const pageTitles: Record<string, string> = {
  '/': 'Dashboard',
  '/onboarding': 'Onboarding Pipeline',
  '/runbooks': 'Runbook Library',
  '/learning': 'Learning Loop',
  '/reports': 'Reports',
  '/audit-logs': 'Audit Logs',
};

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout, addAuditLog } = useAuth();

  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState(new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [commandBarOpen, setCommandBarOpen] = useState(false);

  // Fetch fleet data for sidebar
  const { data: clients = [], dataUpdatedAt } = useFleet();
  const refreshFleet = useRefreshFleet();

  // Register Cmd+K shortcut for command bar
  useCommandPalette(() => setCommandBarOpen(true));

  // Update lastUpdated when data changes
  useEffect(() => {
    if (dataUpdatedAt) {
      setLastUpdated(new Date(dataUpdatedAt));
    }
  }, [dataUpdatedAt]);

  // Log page views
  useEffect(() => {
    const pageName = pageTitles[location.pathname] || location.pathname;
    addAuditLog('VIEW', pageName, `Viewed ${pageName}`);
  }, [location.pathname]);

  const handleClientSelect = (siteId: string) => {
    setSelectedClient(siteId);
    navigate(`/client/${siteId}`);
    addAuditLog('VIEW', 'Client Detail', `Viewed client: ${siteId}`);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    refreshFleet();
    addAuditLog('REFRESH', 'Dashboard', 'Manually refreshed data');
    // Wait a bit for queries to refetch
    setTimeout(() => {
      setRefreshing(false);
      setLastUpdated(new Date());
    }, 1000);
  };

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  // Get current page title
  const getTitle = (): string => {
    if (location.pathname.startsWith('/client/')) {
      const siteId = location.pathname.replace('/client/', '');
      return siteId.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
    return pageTitles[location.pathname] || 'Central Command';
  };

  return (
    <div className="min-h-screen bg-background-primary">
      {/* Sidebar */}
      <Sidebar
        clients={clients}
        onClientSelect={handleClientSelect}
        selectedClient={selectedClient}
        user={user}
        onLogout={handleLogout}
      />

      {/* Main content */}
      <div className="ml-64">
        <Header
          title={getTitle()}
          onRefresh={handleRefresh}
          refreshing={refreshing}
          lastUpdated={lastUpdated}
          user={user}
        />

        <main className="p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/runbooks" element={<Runbooks />} />
            <Route path="/learning" element={<Learning />} />
            <Route path="/audit-logs" element={<AuditLogs />} />
            <Route path="/client/:siteId" element={<ClientDetail />} />
            <Route path="/reports" element={<ComingSoon title="Reports" />} />
          </Routes>
        </main>
      </div>

      {/* Command Bar (Cmd+K) */}
      <CommandBar isOpen={commandBarOpen} onClose={() => setCommandBarOpen(false)} />
    </div>
  );
};

// Placeholder for pages not yet implemented
const ComingSoon: React.FC<{ title: string }> = ({ title }) => (
  <div className="flex flex-col items-center justify-center min-h-[400px] text-center">
    <h1 className="text-2xl font-semibold text-label-primary mb-2">{title}</h1>
    <p className="text-label-tertiary">Coming soon in a future phase.</p>
  </div>
);

const AuthenticatedApp: React.FC = () => {
  const { isAuthenticated } = useAuth();
  const [showApp, setShowApp] = useState(false);

  useEffect(() => {
    setShowApp(isAuthenticated);
  }, [isAuthenticated]);

  if (!showApp) {
    return <Login onSuccess={() => setShowApp(true)} />;
  }

  return <AppLayout />;
};

const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <AuthenticatedApp />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
};

export default App;
