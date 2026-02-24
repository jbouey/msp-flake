import React from 'react';
import { Outlet, useNavigate, useLocation, Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { OsirisCareLeaf } from '../components/shared';
import { companionColors } from './companion-tokens';

const navItems = [
  { path: '/companion', label: 'Clients', exact: true },
  { path: '/companion/stats', label: 'Progress' },
  { path: '/companion/activity', label: 'Activity' },
];

export const CompanionLayout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = async () => {
    await logout();
    navigate('/');
  };

  // Build breadcrumbs from path
  const pathParts = location.pathname.replace('/companion', '').split('/').filter(Boolean);
  const breadcrumbs: { label: string; path: string }[] = [{ label: 'Clients', path: '/companion' }];
  if (pathParts[0] === 'clients' && pathParts[1]) {
    breadcrumbs.push({ label: decodeURIComponent(pathParts[1]).replace(/-/g, ' '), path: `/companion/clients/${pathParts[1]}` });
    if (pathParts[2]) {
      breadcrumbs.push({ label: pathParts[2].replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()), path: location.pathname });
    }
  } else if (pathParts[0] === 'stats') {
    breadcrumbs.push({ label: 'Progress', path: '/companion/stats' });
  } else if (pathParts[0] === 'activity') {
    breadcrumbs.push({ label: 'Activity', path: '/companion/activity' });
  }

  return (
    <div className="min-h-screen" style={{ background: companionColors.pageBg }}>
      {/* Top Bar */}
      <header
        className="sticky top-0 z-30 flex items-center justify-between px-6"
        style={{
          height: 56,
          background: companionColors.cardBg,
          borderBottom: `1px solid ${companionColors.divider}`,
          boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
        }}
      >
        <div className="flex items-center gap-4">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: `linear-gradient(135deg, ${companionColors.primary}, ${companionColors.primaryDark})` }}
          >
            <OsirisCareLeaf className="w-4 h-4" color="white" />
          </div>
          <span className="font-semibold text-base" style={{ color: companionColors.textPrimary }}>
            Compliance Companion
          </span>

          {/* Nav links */}
          <nav className="flex items-center gap-1 ml-6">
            {navItems.map(item => {
              const active = item.exact
                ? location.pathname === item.path
                : location.pathname.startsWith(item.path);
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                  style={{
                    color: active ? companionColors.primary : companionColors.textSecondary,
                    background: active ? companionColors.primaryLight : 'transparent',
                  }}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-sm" style={{ color: companionColors.textSecondary }}>
            {user?.displayName || user?.username}
          </span>
          <button
            onClick={handleLogout}
            className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
            style={{ color: companionColors.textSecondary, border: `1px solid ${companionColors.cardBorder}` }}
          >
            Sign Out
          </button>
        </div>
      </header>

      {/* Breadcrumbs */}
      {breadcrumbs.length > 1 && (
        <div
          className="px-6 py-2 flex items-center gap-1.5 text-sm"
          style={{ borderBottom: `1px solid ${companionColors.divider}`, background: companionColors.cardBg }}
        >
          {breadcrumbs.map((bc, i) => (
            <React.Fragment key={bc.path}>
              {i > 0 && <span style={{ color: companionColors.textTertiary }}>/</span>}
              {i < breadcrumbs.length - 1 ? (
                <Link
                  to={bc.path}
                  className="hover:underline capitalize"
                  style={{ color: companionColors.primary }}
                >
                  {bc.label}
                </Link>
              ) : (
                <span className="capitalize" style={{ color: companionColors.textSecondary }}>
                  {bc.label}
                </span>
              )}
            </React.Fragment>
          ))}
        </div>
      )}

      {/* Page content */}
      <main className="p-6 max-w-7xl mx-auto">
        <Outlet />
      </main>
    </div>
  );
};
