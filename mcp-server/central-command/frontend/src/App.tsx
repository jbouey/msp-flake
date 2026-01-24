import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { CommandBar } from './components/command';
import { Dashboard, Runbooks, RunbookConfig, Learning, Onboarding, ClientDetail, Login, AuditLogs, Sites, SiteDetail, SiteWorkstations, SiteGoAgents, RMMComparison, IntegrationError, Documentation, Partners, Notifications, NotificationSettings, Incidents } from './pages';
import FleetUpdates from './pages/FleetUpdates';
import Users from './pages/Users';
import FrameworkConfig from './pages/FrameworkConfig';
import Integrations from './pages/Integrations';
import IntegrationSetup from './pages/IntegrationSetup';
import IntegrationResources from './pages/IntegrationResources';
import SetPassword from './pages/SetPassword';
import OAuthCallback from './pages/OAuthCallback';
import AdminOAuthSettings from './pages/AdminOAuthSettings';
import { useFleet, useRefreshFleet, useCommandPalette } from './hooks';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { PortalDashboard } from './portal/PortalDashboard';
import { PortalLogin } from './portal/PortalLogin';
import { PortalVerify } from './portal/PortalVerify';
import { PartnerProvider, PartnerLogin, PartnerDashboard } from './partner';
import { ClientProvider, ClientLogin, ClientVerify, ClientDashboard, ClientEvidence, ClientReports, ClientNotifications, ClientSettings } from './client';

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
  '/sites': 'Sites',
  '/notifications': 'Notifications',
  '/notification-settings': 'Notification Settings',
  '/incidents': 'Incidents',
  '/onboarding': 'Onboarding Pipeline',
  '/partners': 'Partners',
  '/users': 'User Management',
  '/runbooks': 'Runbook Library',
  '/runbook-config': 'Runbook Configuration',
  '/learning': 'Learning Loop',
  '/fleet-updates': 'Fleet Updates',
  '/reports': 'Reports',
  '/audit-logs': 'Audit Logs',
  '/settings/oauth': 'OAuth Settings',
  '/docs': 'Documentation',
};

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

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

  const handleClientSelect = (siteId: string) => {
    setSelectedClient(siteId);
    navigate(`/client/${siteId}`);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    refreshFleet();
    // Wait a bit for queries to refetch
    setTimeout(() => {
      setRefreshing(false);
      setLastUpdated(new Date());
    }, 1000);
  };

  const handleLogout = async () => {
    await logout();
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
            <Route path="/sites" element={<Sites />} />
            <Route path="/sites/:siteId" element={<SiteDetail />} />
            <Route path="/sites/:siteId/frameworks" element={<FrameworkConfig />} />
            <Route path="/sites/:siteId/workstations" element={<SiteWorkstations />} />
            <Route path="/sites/:siteId/workstations/rmm-compare" element={<RMMComparison />} />
            <Route path="/sites/:siteId/agents" element={<SiteGoAgents />} />
            <Route path="/sites/:siteId/integrations" element={<Integrations />} />
            <Route path="/sites/:siteId/integrations/setup" element={<IntegrationSetup />} />
            <Route path="/sites/:siteId/integrations/:integrationId" element={<IntegrationResources />} />
            <Route path="/integrations/error" element={<IntegrationError />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/notification-settings" element={<NotificationSettings />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/partners" element={<Partners />} />
            <Route path="/users" element={<Users />} />
            <Route path="/runbooks" element={<Runbooks />} />
            <Route path="/runbook-config" element={<RunbookConfig />} />
            <Route path="/learning" element={<Learning />} />
            <Route path="/audit-logs" element={<AuditLogs />} />
            <Route path="/settings/oauth" element={<AdminOAuthSettings />} />
            <Route path="/fleet-updates" element={<FleetUpdates />} />
            <Route path="/docs" element={<Documentation />} />
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
  const { isAuthenticated, isLoading } = useAuth();

  // Show loading state while checking session
  if (isLoading) {
    return (
      <div className="min-h-screen bg-background-primary flex items-center justify-center">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-label-tertiary">Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login onSuccess={() => {}} />;
  }

  return <AppLayout />;
};

const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Public routes - no auth required */}
            <Route path="/set-password" element={<SetPassword />} />
            <Route path="/auth/oauth/success" element={<OAuthCallback />} />

            {/* Portal routes - token or session auth */}
            <Route path="/portal/site/:siteId" element={<PortalLogin />} />
            <Route path="/portal/site/:siteId/dashboard" element={<PortalDashboard />} />
            <Route path="/portal/site/:siteId/verify" element={<PortalVerify />} />
            <Route path="/portal/site/:siteId/login" element={<PortalLogin />} />

            {/* Partner routes - API key auth */}
            <Route
              path="/partner/*"
              element={
                <PartnerProvider>
                  <Routes>
                    <Route path="login" element={<PartnerLogin />} />
                    <Route path="dashboard" element={<PartnerDashboard />} />
                    <Route path="*" element={<PartnerLogin />} />
                  </Routes>
                </PartnerProvider>
              }
            />

            {/* Client routes - magic link / cookie auth */}
            <Route
              path="/client/*"
              element={
                <ClientProvider>
                  <Routes>
                    <Route path="login" element={<ClientLogin />} />
                    <Route path="verify" element={<ClientVerify />} />
                    <Route path="dashboard" element={<ClientDashboard />} />
                    <Route path="evidence" element={<ClientEvidence />} />
                    <Route path="reports" element={<ClientReports />} />
                    <Route path="notifications" element={<ClientNotifications />} />
                    <Route path="settings" element={<ClientSettings />} />
                    <Route path="*" element={<ClientLogin />} />
                  </Routes>
                </ClientProvider>
              }
            />

            {/* Admin routes - auth required */}
            <Route path="/*" element={<AuthenticatedApp />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
};

export default App;
