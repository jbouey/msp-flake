import React, { useState } from 'react';

interface User {
  username: string;
  role: 'admin' | 'operator' | 'readonly';
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
  user: _user,
}) => {
  const [searchOpen, setSearchOpen] = useState(false);

  const formatLastUpdated = (date: Date): string => {
    const now = new Date();
    const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSeconds < 60) return 'Just now';
    if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <header className="glass-header h-14 flex items-center justify-between px-6 sticky top-0 z-10">
      {/* Left: Title */}
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold text-label-primary tracking-tight">{title}</h1>
        {subtitle && (
          <span className="text-sm text-label-tertiary">{subtitle}</span>
        )}
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-2">
        {/* Search */}
        <div className="relative">
          {searchOpen ? (
            <input
              type="text"
              placeholder="Search..."
              autoFocus
              onBlur={() => setSearchOpen(false)}
              className="
                w-56 px-3 py-1.5 text-sm
                bg-fill-tertiary rounded-ios-sm
                border-none outline-none
                focus:ring-2 focus:ring-accent-primary/40
                placeholder-label-tertiary
                transition-all
              "
            />
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-ios-sm bg-fill-quaternary hover:bg-fill-tertiary transition-colors"
              aria-label="Search"
            >
              <svg className="w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span className="text-xs text-label-tertiary hidden sm:inline">Search</span>
              <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-medium text-label-tertiary bg-white/60 rounded border border-separator-light">
                <span className="text-[11px]">&#8984;</span>K
              </kbd>
            </button>
          )}
        </div>

        {/* Refresh indicator */}
        {showRefresh && (
          <div className="flex items-center gap-1.5">
            {lastUpdated && (
              <span className="text-[11px] text-label-tertiary tabular-nums">
                {formatLastUpdated(lastUpdated)}
              </span>
            )}
            <button
              onClick={onRefresh}
              disabled={refreshing}
              className="p-1.5 rounded-ios-sm hover:bg-fill-tertiary transition-colors disabled:opacity-50"
              aria-label="Refresh"
            >
              <svg
                className={`w-4 h-4 text-label-tertiary transition-transform ${refreshing ? 'animate-spin' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
        )}

        {/* Custom actions */}
        {actions}
      </div>
    </header>
  );
};

export default Header;
