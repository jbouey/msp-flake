import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { commandApi } from '../../utils/api';
import type { CommandResponse } from '../../types';

interface CommandBarProps {
  isOpen: boolean;
  onClose: () => void;
}

interface CommandSuggestion {
  id: string;
  label: string;
  description: string;
  icon: React.ReactNode;
  action: () => void;
  category: 'navigation' | 'action' | 'query';
}

export const CommandBar: React.FC<CommandBarProps> = ({ isOpen, onClose }) => {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [isExecuting, setIsExecuting] = useState(false);
  const [result, setResult] = useState<CommandResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Navigation suggestions
  const navigationSuggestions: CommandSuggestion[] = [
    {
      id: 'nav-dashboard',
      label: 'Go to Dashboard',
      description: 'View fleet overview and incidents',
      category: 'navigation',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
        </svg>
      ),
      action: () => { navigate('/'); onClose(); },
    },
    {
      id: 'nav-runbooks',
      label: 'Go to Runbooks',
      description: 'Browse runbook library',
      category: 'navigation',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
        </svg>
      ),
      action: () => { navigate('/runbooks'); onClose(); },
    },
    {
      id: 'nav-learning',
      label: 'Go to Learning Loop',
      description: 'View L2 to L1 promotions',
      category: 'navigation',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      ),
      action: () => { navigate('/learning'); onClose(); },
    },
    {
      id: 'nav-onboarding',
      label: 'Go to Onboarding',
      description: 'View client pipeline',
      category: 'navigation',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
        </svg>
      ),
      action: () => { navigate('/onboarding'); onClose(); },
    },
    {
      id: 'nav-audit',
      label: 'Go to Audit Logs',
      description: 'View system activity logs',
      category: 'navigation',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      ),
      action: () => { navigate('/audit-logs'); onClose(); },
    },
  ];

  // Action suggestions
  const actionSuggestions: CommandSuggestion[] = [
    {
      id: 'action-refresh',
      label: 'Refresh Data',
      description: 'Reload all dashboard data',
      category: 'action',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      ),
      action: () => { window.location.reload(); },
    },
    {
      id: 'action-docs',
      label: 'Open User Guide',
      description: 'View documentation (PDF)',
      category: 'action',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      ),
      action: () => { window.open('/USER_GUIDE.pdf', '_blank'); onClose(); },
    },
    {
      id: 'action-standards',
      label: 'Open Standards & Procedures',
      description: 'View compliance procedures (PDF)',
      category: 'action',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
        </svg>
      ),
      action: () => { window.open('/STANDARDS_AND_PROCEDURES.pdf', '_blank'); onClose(); },
    },
  ];

  const allSuggestions = [...navigationSuggestions, ...actionSuggestions];

  // Filter suggestions based on query
  const filteredSuggestions = query.trim()
    ? allSuggestions.filter(
        (s) =>
          s.label.toLowerCase().includes(query.toLowerCase()) ||
          s.description.toLowerCase().includes(query.toLowerCase())
      )
    : allSuggestions;

  // Check if query looks like a command
  const isCommand = query.startsWith('/') || query.startsWith('!');

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setResult(null);
      setError(null);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filteredSuggestions.length - 1));
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }

      if (e.key === 'Enter') {
        e.preventDefault();
        if (isCommand && query.length > 1) {
          executeCommand(query);
        } else if (filteredSuggestions[selectedIndex]) {
          filteredSuggestions[selectedIndex].action();
        }
        return;
      }
    },
    [filteredSuggestions, selectedIndex, query, isCommand, onClose]
  );

  // Execute custom command
  const executeCommand = async (cmd: string) => {
    setIsExecuting(true);
    setError(null);
    setResult(null);

    try {
      const response = await commandApi.execute(cmd);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Command failed');
    } finally {
      setIsExecuting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Command palette */}
      <div className="relative w-full max-w-xl bg-white rounded-ios-lg shadow-2xl overflow-hidden">
        {/* Input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-separator-light">
          <svg className="w-5 h-5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
              setResult(null);
              setError(null);
            }}
            onKeyDown={handleKeyDown}
            placeholder="Type a command or search..."
            className="flex-1 bg-transparent border-none outline-none text-label-primary placeholder-label-tertiary"
          />
          {isExecuting && (
            <div className="w-5 h-5 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
          )}
          <kbd className="px-2 py-1 text-xs text-label-tertiary bg-separator-light rounded">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto">
          {/* Command result */}
          {result && (
            <div className="p-4 border-b border-separator-light">
              <div className={`p-3 rounded-ios-md ${result.success ? 'bg-health-healthy/10' : 'bg-health-critical/10'}`}>
                <div className="flex items-center gap-2 mb-2">
                  {result.success ? (
                    <svg className="w-4 h-4 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  )}
                  <span className="text-sm font-medium">
                    {result.success ? 'Success' : 'Failed'}
                  </span>
                </div>
                {result.message && (
                  <p className="text-sm text-label-secondary">{result.message}</p>
                )}
                {result.error && (
                  <p className="text-sm text-health-critical">{result.error}</p>
                )}
                {result.data && (
                  <pre className="mt-2 p-2 bg-black/5 rounded text-xs overflow-x-auto">
                    {JSON.stringify(result.data, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="p-4 border-b border-separator-light">
              <div className="p-3 rounded-ios-md bg-health-critical/10">
                <p className="text-sm text-health-critical">{error}</p>
              </div>
            </div>
          )}

          {/* Suggestions */}
          {!result && !error && (
            <>
              {isCommand ? (
                <div className="p-4 text-center text-label-tertiary text-sm">
                  Press <kbd className="px-1.5 py-0.5 bg-separator-light rounded text-xs">Enter</kbd> to execute command
                </div>
              ) : filteredSuggestions.length === 0 ? (
                <div className="p-8 text-center text-label-tertiary">
                  No results found
                </div>
              ) : (
                <div className="py-2">
                  {/* Group by category */}
                  {['navigation', 'action'].map((category) => {
                    const items = filteredSuggestions.filter((s) => s.category === category);
                    if (items.length === 0) return null;
                    return (
                      <div key={category}>
                        <div className="px-4 py-2 text-xs font-semibold text-label-tertiary uppercase tracking-wide">
                          {category === 'navigation' ? 'Navigate' : 'Actions'}
                        </div>
                        {items.map((suggestion) => {
                          const globalIndex = filteredSuggestions.indexOf(suggestion);
                          return (
                            <button
                              key={suggestion.id}
                              onClick={suggestion.action}
                              className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                                globalIndex === selectedIndex
                                  ? 'bg-accent-primary/10 text-accent-primary'
                                  : 'hover:bg-separator-light text-label-primary'
                              }`}
                            >
                              <span className={globalIndex === selectedIndex ? 'text-accent-primary' : 'text-label-tertiary'}>
                                {suggestion.icon}
                              </span>
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">
                                  {suggestion.label}
                                </p>
                                <p className="text-xs text-label-tertiary truncate">
                                  {suggestion.description}
                                </p>
                              </div>
                              {globalIndex === selectedIndex && (
                                <kbd className="px-1.5 py-0.5 bg-accent-primary/20 rounded text-xs">
                                  Enter
                                </kbd>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-separator-light bg-separator-light/30 flex items-center justify-between text-xs text-label-tertiary">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 bg-white rounded shadow-sm">↑</kbd>
              <kbd className="px-1 py-0.5 bg-white rounded shadow-sm">↓</kbd>
              Navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 bg-white rounded shadow-sm">Enter</kbd>
              Select
            </span>
          </div>
          <span>
            Tip: Start with <code className="px-1 bg-white rounded">/</code> for commands
          </span>
        </div>
      </div>
    </div>
  );
};

export default CommandBar;
