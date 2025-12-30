import React, { useState } from 'react';

interface User {
  username: string;
  role: 'admin' | 'operator';
  displayName: string;
}

interface HeaderProps {
  title?: string;
  subtitle?: string;
  showRefresh?: boolean;
  onRefresh?: () => void;
  refreshing?: boolean;
  lastUpdated?: Date;
  actions?: React.ReactNode;
  user?: User | null;
}

export const Header: React.FC<HeaderProps> = ({
  title = 'Dashboard',
  subtitle,
  showRefresh = true,
  onRefresh,
  refreshing = false,
  lastUpdated,
  actions,
  user,
}) => {
  const [searchOpen, setSearchOpen] = useState(false);

  const formatLastUpdated = (date: Date): string => {
    const now = new Date();
    const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSeconds < 60) return 'Just now';
    if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const userInitials = user?.displayName
    ? user.displayName
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
    : 'U';

  return (
    <header className="glass-header h-16 flex items-center justify-between px-6 sticky top-0 z-10">
      {/* Left: Title */}
      <div>
        <h1 className="text-xl font-semibold text-label-primary">{title}</h1>
        {subtitle && (
          <p className="text-sm text-label-tertiary">{subtitle}</p>
        )}
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-4">
        {/* Search */}
        <div className="relative">
          {searchOpen ? (
            <input
              type="text"
              placeholder="Search..."
              autoFocus
              onBlur={() => setSearchOpen(false)}
              className="
                w-64 px-4 py-2 text-sm
                bg-separator-light rounded-ios-md
                border-none outline-none
                focus:ring-2 focus:ring-accent-primary
                placeholder-label-tertiary
              "
            />
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="p-2 rounded-ios-sm hover:bg-separator-light transition-colors"
              aria-label="Search"
            >
              <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          )}
        </div>

        {/* Refresh indicator */}
        {showRefresh && (
          <div className="flex items-center gap-2">
            {lastUpdated && (
              <span className="text-xs text-label-tertiary">
                {formatLastUpdated(lastUpdated)}
              </span>
            )}
            <button
              onClick={onRefresh}
              disabled={refreshing}
              className={`
                p-2 rounded-ios-sm hover:bg-separator-light transition-colors
                ${refreshing ? 'animate-spin' : ''}
              `}
              aria-label="Refresh"
            >
              <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
        )}

        {/* Custom actions */}
        {actions}

        {/* User info */}
        <div className="flex items-center gap-2 pl-4 border-l border-separator-light">
          <div className="w-8 h-8 bg-accent-primary rounded-full flex items-center justify-center">
            <span className="text-white text-sm font-medium">{userInitials}</span>
          </div>
          <div className="text-sm">
            <span className="text-label-primary font-medium">{user?.displayName || 'User'}</span>
            <span className="text-label-tertiary ml-1 text-xs capitalize">({user?.role || 'guest'})</span>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
