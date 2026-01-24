# React Frontend Patterns

## Stack
- React 18.2 + TypeScript 5.2
- React Router v6
- TanStack Query v5 (React Query)
- Tailwind CSS + custom design tokens
- Vite build tool

## Directory Structure
```
src/
├── pages/          # 31 page components
├── components/     # Domain-organized components
│   ├── shared/     # Button, Badge, Spinner, GlassCard
│   ├── fleet/      # FleetOverview, ClientCard
│   ├── incidents/  # IncidentFeed, IncidentRow
│   └── learning/   # PatternCard, PromotionTimeline
├── hooks/          # 51+ custom hooks
├── contexts/       # Auth, Partner contexts
├── utils/          # API client (api.ts)
├── types/          # TypeScript interfaces
├── partner/        # Partner portal UI
└── client/         # Client portal UI (OsirisCare-branded)
    ├── ClientContext.tsx    # Cookie-based auth context
    ├── ClientLogin.tsx      # Magic link + password
    ├── ClientVerify.tsx     # Token validation
    ├── ClientDashboard.tsx  # Main dashboard with KPIs
    ├── ClientEvidence.tsx   # Evidence archive
    ├── ClientReports.tsx    # Monthly/annual reports
    ├── ClientNotifications.tsx
    ├── ClientSettings.tsx   # Password, transfer provider
    └── ClientHelp.tsx       # Help documentation with visuals
```

## React Query Patterns

### Data Fetching Hook
```typescript
export function useFleet() {
  return useQuery<ClientOverview[]>({
    queryKey: ['fleet'],
    queryFn: fleetApi.getFleet,
    refetchInterval: 60_000,  // 60s polling
    staleTime: 30_000,        // 30s freshness
  });
}
```

### Mutation Hook
```typescript
export function useCreateSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateSiteRequest) => sitesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites'] });
    },
  });
}
```

### Conditional Query
```typescript
export function useClient(siteId?: string) {
  return useQuery({
    queryKey: ['client', siteId],
    queryFn: () => fleetApi.getClient(siteId!),
    enabled: !!siteId,  // Only fetch when siteId exists
  });
}
```

## API Client Pattern

### Module Structure (api.ts)
```typescript
const ENDPOINT = '/api/sites';

export const sitesApi = {
  getAll: () => fetchApi<Site[]>(ENDPOINT),
  getOne: (id: string) => fetchApi<Site>(`${ENDPOINT}/${id}`),
  create: (data: CreateSiteRequest) => fetchApi<Site>(ENDPOINT, {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  update: (id: string, data: UpdateSiteRequest) => fetchApi<Site>(`${ENDPOINT}/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  }),
};
```

### Centralized Fetch with Auth
```typescript
async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('auth_token');
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(token && { 'Authorization': `Bearer ${token}` }),
  };

  const response = await fetch(endpoint, { ...options, headers });
  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(response.status, error.detail || 'Unknown error');
  }
  return response.json();
}
```

## Component Patterns

### Page with Data Fetching
```typescript
export const SitesPage: React.FC = () => {
  const { data: sites, isLoading, error } = useSites();

  if (isLoading) return <Spinner />;
  if (error) return <ErrorMessage error={error} />;

  return (
    <div className="space-y-4">
      {sites?.map(site => (
        <SiteCard key={site.id} site={site} />
      ))}
    </div>
  );
};
```

### Shared Button Component
```typescript
interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  children: React.ReactNode;
  onClick?: () => void;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  isLoading,
  children,
  ...props
}) => (
  <button
    className={cn(buttonVariants({ variant, size }))}
    disabled={isLoading}
    {...props}
  >
    {isLoading ? <Spinner size="sm" /> : children}
  </button>
);
```

## Design Tokens

### Colors (style-tokens.ts)
```typescript
export const tokens = {
  'background-primary': '#0a0a0a',
  'background-secondary': '#141414',
  'label-primary': '#ffffff',
  'label-secondary': '#a1a1a1',
  'accent-primary': '#3b82f6',
  'health-critical': '#ef4444',
  'health-warning': '#f59e0b',
  'health-healthy': '#22c55e',
};
```

### Glass Morphism
```typescript
// GlassCard component
<div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl">
  {children}
</div>
```

## Auth Context

```typescript
interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    // Validate session on mount
    validateSession().then(setUser).catch(() => setUser(null));
  }, []);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
};
```

## Routing Structure

```typescript
// Main routes (admin app)
<Routes>
  <Route path="/" element={<Dashboard />} />
  <Route path="/sites" element={<Sites />} />
  <Route path="/sites/:siteId" element={<SiteDetail />} />
  <Route path="/incidents" element={<Incidents />} />
  <Route path="/runbooks" element={<Runbooks />} />
  <Route path="/learning" element={<Learning />} />
  <Route path="/fleet-updates" element={<FleetUpdates />} />
</Routes>

// Portal routes (site-based magic link)
<Route path="/portal/site/:siteId" element={<PortalLogin />} />
<Route path="/portal/site/:siteId/dashboard" element={<PortalDashboard />} />

// Partner routes (API key auth)
<Route path="/partner/*" element={<PartnerProvider>...</PartnerProvider>} />

// Client portal routes (OsirisCare-branded, cookie auth)
<Route path="/client/*" element={<ClientProvider>
  <Routes>
    <Route path="login" element={<ClientLogin />} />
    <Route path="verify" element={<ClientVerify />} />
    <Route path="dashboard" element={<ClientDashboard />} />
    <Route path="evidence" element={<ClientEvidence />} />
    <Route path="reports" element={<ClientReports />} />
    <Route path="notifications" element={<ClientNotifications />} />
    <Route path="settings" element={<ClientSettings />} />
    <Route path="help" element={<ClientHelp />} />
  </Routes>
</ClientProvider>} />
```

## Key Files
- `src/utils/api.ts` - All API modules (1000+ lines)
- `src/hooks/` - 51+ React Query hooks
- `src/contexts/AuthContext.tsx` - Auth state
- `src/components/shared/` - Reusable UI
- `src/types/index.ts` - TypeScript interfaces
