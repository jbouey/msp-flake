import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { CommandBar } from './components/command';
import { ErrorBoundary, Spinner, OsirisCareLeaf } from './components/shared';
import { useFleet, useRefreshFleet, useCommandPalette, useWebSocket, WebSocketContext } from './hooks';
import { AuthProvider, useAuth } from './contexts/AuthContext';

// Critical pages - loaded immediately
import { Dashboard, Login, Sites } from './pages';

// Lazy-loaded pages - loaded on demand for code splitting
// Uses .then() pattern to handle named exports
const Runbooks = lazy(() => import('./pages/Runbooks').then(m => ({ default: m.Runbooks })));
const RunbookConfig = lazy(() => import('./pages/RunbookConfig').then(m => ({ default: m.RunbookConfig })));
const Learning = lazy(() => import('./pages/Learning').then(m => ({ default: m.Learning })));
const Onboarding = lazy(() => import('./pages/Onboarding').then(m => ({ default: m.Onboarding })));
const ClientDetail = lazy(() => import('./pages/ClientDetail').then(m => ({ default: m.ClientDetail })));
const AuditLogs = lazy(() => import('./pages/AuditLogs').then(m => ({ default: m.AuditLogs })));
const SiteDetail = lazy(() => import('./pages/SiteDetail').then(m => ({ default: m.SiteDetail })));
const SiteWorkstations = lazy(() => import('./pages/SiteWorkstations').then(m => ({ default: m.SiteWorkstations })));
const SiteGoAgents = lazy(() => import('./pages/SiteGoAgents').then(m => ({ default: m.SiteGoAgents })));
const SiteDevices = lazy(() => import('./pages/SiteDevices').then(m => ({ default: m.SiteDevices })));
const RMMComparison = lazy(() => import('./pages/RMMComparison').then(m => ({ default: m.RMMComparison })));
const IntegrationError = lazy(() => import('./pages/IntegrationError').then(m => ({ default: m.IntegrationError })));
const Documentation = lazy(() => import('./pages/Documentation').then(m => ({ default: m.Documentation })));
const Partners = lazy(() => import('./pages/Partners').then(m => ({ default: m.Partners })));
const Notifications = lazy(() => import('./pages/Notifications').then(m => ({ default: m.Notifications })));
const NotificationSettings = lazy(() => import('./pages/NotificationSettings').then(m => ({ default: m.NotificationSettings })));
const Incidents = lazy(() => import('./pages/Incidents').then(m => ({ default: m.Incidents })));
const Settings = lazy(() => import('./pages/Settings').then(m => ({ default: m.Settings })));
// These have default exports
const FleetUpdates = lazy(() => import('./pages/FleetUpdates'));
const CVEWatch = lazy(() => import('./pages/CVEWatch'));
const Users = lazy(() => import('./pages/Users'));
const FrameworkConfig = lazy(() => import('./pages/FrameworkConfig'));
const Integrations = lazy(() => import('./pages/Integrations'));
const IntegrationSetup = lazy(() => import('./pages/IntegrationSetup'));
const IntegrationResources = lazy(() => import('./pages/IntegrationResources'));
const SetPassword = lazy(() => import('./pages/SetPassword'));
const OAuthCallback = lazy(() => import('./pages/OAuthCallback'));
const AdminOAuthSettings = lazy(() => import('./pages/AdminOAuthSettings'));

// Lazy-loaded portal/partner/client modules
const PortalDashboard = lazy(() => import('./portal/PortalDashboard').then(m => ({ default: m.PortalDashboard })));
const PortalLogin = lazy(() => import('./portal/PortalLogin').then(m => ({ default: m.PortalLogin })));
const PortalVerify = lazy(() => import('./portal/PortalVerify').then(m => ({ default: m.PortalVerify })));

// Partner module - lazy loaded with provider
const PartnerRoutes = lazy(() => import('./partner').then(m => ({
  default: () => {
    const { PartnerProvider, PartnerLogin, PartnerDashboard } = m;
    return (
      <PartnerProvider>
        <Routes>
          <Route path="login" element={<PartnerLogin />} />
          <Route path="dashboard" element={<PartnerDashboard />} />
          <Route path="*" element={<PartnerLogin />} />
        </Routes>
      </PartnerProvider>
    );
  }
})));

// Client module - lazy loaded with provider
const ClientRoutes = lazy(() => import('./client').then(m => ({
  default: () => {
    const { ClientProvider, ClientLogin, ClientVerify, ClientDashboard, ClientEvidence, ClientReports, ClientNotifications, ClientSettings, ClientHelp } = m;
    return (
      <ClientProvider>
        <Routes>
          <Route path="login" element={<ClientLogin />} />
          <Route path="verify" element={<ClientVerify />} />
          <Route path="dashboard" element={<ClientDashboard />} />
          <Route path="evidence" element={<ClientEvidence />} />
          <Route path="reports" element={<ClientReports />} />
          <Route path="notifications" element={<ClientNotifications />} />
          <Route path="settings" element={<ClientSettings />} />
          <Route path="help" element={<ClientHelp />} />
          <Route path="*" element={<ClientLogin />} />
        </Routes>
      </ClientProvider>
    );
  }
})));

// Suspense fallback component
const PageLoader: React.FC = () => (
  <div className="flex items-center justify-center min-h-[400px]">
    <Spinner size="lg" />
  </div>
);

// Global error handler for unhandled query errors
const handleQueryError = (error: unknown): void => {
  // Don't log aborted requests
  if (error instanceof Error && error.message.includes('cancelled')) {
    return;
  }
  console.error('Query error:', error);
};

// Create a client for React Query with improved error handling
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof Error && 'status' in error) {
          const status = (error as { status: number }).status;
          // Don't retry on 304 (not modified) or 4xx (client errors)
          if (status === 304 || (status >= 400 && status < 500)) return false;
        }
        // Retry up to 2 times for other errors
        return failureCount < 2;
      },
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      throwOnError: false,
    },
    mutations: {
      // Don't retry mutations by default
      retry: false,
      onError: handleQueryError,
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
  '/cve-watch': 'CVE Watch',
  '/reports': 'Reports',
  '/audit-logs': 'Audit Logs',
  '/settings/oauth': 'OAuth Settings',
  '/settings': 'Settings',
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
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Fetch fleet data for sidebar
  const { data: clients = [], dataUpdatedAt } = useFleet();
  const refreshFleet = useRefreshFleet();

  // Connect WebSocket for real-time event push
  const wsState = useWebSocket();

  // Register Cmd+K shortcut for command bar
  useCommandPalette(() => setCommandBarOpen(true));

  // Close sidebar on route change (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

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
    try {
      await refreshFleet();
    } finally {
      setRefreshing(false);
      setLastUpdated(new Date());
    }
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
    return pageTitles[location.pathname] || 'Dashboard';
  };

  return (
    <WebSocketContext.Provider value={wsState}>
    <div className="min-h-screen bg-background-primary">
      {/* Sidebar */}
      <Sidebar
        clients={clients}
        onClientSelect={handleClientSelect}
        selectedClient={selectedClient}
        user={user}
        onLogout={handleLogout}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* Main content */}
      <div className="lg:ml-64">
        <Header
          title={getTitle()}
          onRefresh={handleRefresh}
          refreshing={refreshing}
          lastUpdated={lastUpdated}
          user={user}
          onMenuToggle={() => setSidebarOpen(true)}
        />

        <main className="p-6">
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/sites" element={<Sites />} />
              <Route path="/sites/:siteId" element={<SiteDetail />} />
              <Route path="/sites/:siteId/frameworks" element={<FrameworkConfig />} />
              <Route path="/sites/:siteId/workstations" element={<SiteWorkstations />} />
              <Route path="/sites/:siteId/workstations/rmm-compare" element={<RMMComparison />} />
              <Route path="/sites/:siteId/agents" element={<SiteGoAgents />} />
              <Route path="/sites/:siteId/devices" element={<SiteDevices />} />
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
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/oauth" element={<AdminOAuthSettings />} />
              <Route path="/fleet-updates" element={<FleetUpdates />} />
              <Route path="/cve-watch" element={<CVEWatch />} />
              <Route path="/docs" element={<Documentation />} />
              <Route path="/client/:siteId" element={<ClientDetail />} />
              <Route path="/reports" element={<ComingSoon title="Reports" />} />
            </Routes>
          </Suspense>
        </main>
      </div>

      {/* Command Bar (Cmd+K) */}
      <CommandBar isOpen={commandBarOpen} onClose={() => setCommandBarOpen(false)} />
    </div>
    </WebSocketContext.Provider>
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
        <div className="text-center animate-fade-in">
          <div
            className="w-10 h-10 rounded-ios-md mx-auto flex items-center justify-center mb-4 animate-pulse-soft"
            style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 2px 12px rgba(60, 188, 180, 0.35)' }}
          >
            <OsirisCareLeaf className="w-5 h-5" color="white" />
          </div>
          <p className="text-label-tertiary text-sm">Loading...</p>
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
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                {/* Public routes - no auth required */}
                <Route path="/set-password" element={<SetPassword />} />
                <Route path="/auth/oauth/success" element={<OAuthCallback />} />

                {/* Portal routes - token or session auth */}
                <Route path="/portal/site/:siteId" element={<PortalLogin />} />
                <Route path="/portal/site/:siteId/dashboard" element={<PortalDashboard />} />
                <Route path="/portal/site/:siteId/verify" element={<PortalVerify />} />
                <Route path="/portal/site/:siteId/login" element={<PortalLogin />} />

                {/* Partner routes - API key auth (lazy loaded module) */}
                <Route path="/partner/*" element={<PartnerRoutes />} />

                {/* Client routes - magic link / cookie auth (lazy loaded module) */}
                <Route path="/client/*" element={<ClientRoutes />} />

                {/* Admin routes - auth required */}
                <Route path="/*" element={<AuthenticatedApp />} />
              </Routes>
            </Suspense>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
};

export default App;
