import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Spinner } from '../shared';
import { formatTimeAgo } from '../../constants';

interface IncidentHit {
  id: string;
  incident_type: string;
  title: string;
  severity: string;
  status: string;
  created_at: string;
}

interface DeviceHit {
  id: string;
  hostname: string;
  ip_address: string | null;
  mac_address: string | null;
  device_type: string | null;
}

interface CredentialHit {
  id: string;
  credential_type: string;
  credential_name: string;
}

interface WorkstationHit {
  id: string;
  hostname: string;
  os: string | null;
  compliance_status: string | null;
}

interface SearchResponse {
  site_id: string;
  query: string;
  results: {
    incidents: IncidentHit[];
    devices: DeviceHit[];
    credentials: CredentialHit[];
    workstations: WorkstationHit[];
  };
  total: number;
}

interface Props {
  siteId: string;
  /** Placeholder for the empty input state. Overrideable so the component
   *  stays reusable outside Site Detail if needed. */
  placeholder?: string;
}

/**
 * SiteSearchBar — in-site search across incidents, devices, credentials,
 * and workstations. Debounces user input (250ms) and hits the backend
 * `GET /api/sites/{site_id}/search` endpoint. Results dropdown is
 * keyboard-dismissable (Escape) and click-outside closes.
 *
 * Clicking a result navigates to the appropriate sub-page with the
 * relevant filter already applied — e.g. an incident hit jumps to
 * `/incidents?site_id=...&hostname=...`, a device hit to the site's
 * devices tab.
 */
export const SiteSearchBar: React.FC<Props> = ({ siteId, placeholder }) => {
  const [input, setInput] = useState('');
  const [debouncedInput, setDebouncedInput] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // Debounce the input — no fetch fires until the user stops typing.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedInput(input.trim()), 250);
    return () => clearTimeout(t);
  }, [input]);

  // Click-outside to dismiss
  useEffect(() => {
    if (!isOpen) return;
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [isOpen]);

  // Escape to dismiss
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen]);

  const { data, isLoading } = useQuery<SearchResponse>({
    queryKey: ['site-search', siteId, debouncedInput],
    queryFn: async () => {
      const res = await fetch(
        `/api/sites/${siteId}/search?q=${encodeURIComponent(debouncedInput)}&limit=10`,
        { credentials: 'include' },
      );
      if (!res.ok) {
        throw new Error(`Search failed: ${res.status}`);
      }
      return res.json();
    },
    enabled: !!siteId && debouncedInput.length >= 2,
    staleTime: 30_000,
    retry: false,
  });

  const hasResults = data && data.total > 0;
  const showDropdown = isOpen && debouncedInput.length >= 2;

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary pointer-events-none"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21 21l-4.35-4.35m2.1-5.4a7.5 7.5 0 11-15 0 7.5 7.5 0 0115 0z"
          />
        </svg>
        <input
          type="text"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder={placeholder || 'Search this site…'}
          aria-label="Search site"
          className="w-full pl-9 pr-9 py-2 rounded-ios bg-fill-secondary text-label-primary placeholder:text-label-tertiary border border-separator-light focus:border-accent-primary focus:outline-none text-sm"
        />
        {isLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <Spinner size="sm" />
          </div>
        )}
        {!isLoading && input && (
          <button
            type="button"
            onClick={() => {
              setInput('');
              setDebouncedInput('');
              setIsOpen(false);
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-fill-tertiary text-label-tertiary"
            aria-label="Clear search"
          >
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {showDropdown && (
        <div className="absolute z-30 left-0 right-0 mt-2 rounded-ios bg-background-secondary border border-glass-border shadow-xl max-h-96 overflow-y-auto">
          {isLoading && (
            <div className="p-4 text-center text-sm text-label-tertiary">Searching…</div>
          )}

          {!isLoading && !hasResults && data && (
            <div className="p-4 text-center text-sm text-label-tertiary">
              No matches for <span className="text-label-primary">&ldquo;{debouncedInput}&rdquo;</span>
            </div>
          )}

          {!isLoading && hasResults && data && (
            <div className="divide-y divide-glass-border">
              {data.results.incidents.length > 0 && (
                <SearchCategory
                  label={`Incidents (${data.results.incidents.length})`}
                >
                  {data.results.incidents.map((hit) => (
                    <button
                      key={hit.id}
                      type="button"
                      onClick={() => {
                        navigate(`/incidents?site_id=${siteId}&id=${hit.id}`);
                        setIsOpen(false);
                      }}
                      className="w-full text-left px-3 py-2 hover:bg-fill-secondary transition-colors"
                    >
                      <div className="flex items-start gap-2">
                        <span
                          className={`mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0 ${severityDot(hit.severity)}`}
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-label-primary truncate">{hit.title}</p>
                          <p className="text-xs text-label-tertiary truncate">
                            {hit.incident_type} · {hit.status} · {formatTimeAgo(hit.created_at)}
                          </p>
                        </div>
                      </div>
                    </button>
                  ))}
                </SearchCategory>
              )}

              {data.results.devices.length > 0 && (
                <SearchCategory label={`Devices (${data.results.devices.length})`}>
                  {data.results.devices.map((hit) => (
                    <button
                      key={hit.id}
                      type="button"
                      onClick={() => {
                        navigate(`/sites/${siteId}/devices?hostname=${hit.hostname}`);
                        setIsOpen(false);
                      }}
                      className="w-full text-left px-3 py-2 hover:bg-fill-secondary transition-colors"
                    >
                      <p className="text-sm text-label-primary truncate">{hit.hostname}</p>
                      <p className="text-xs text-label-tertiary truncate">
                        {hit.ip_address || hit.mac_address || hit.device_type || '—'}
                      </p>
                    </button>
                  ))}
                </SearchCategory>
              )}

              {data.results.workstations.length > 0 && (
                <SearchCategory label={`Workstations (${data.results.workstations.length})`}>
                  {data.results.workstations.map((hit) => (
                    <button
                      key={hit.id}
                      type="button"
                      onClick={() => {
                        navigate(`/sites/${siteId}/workstations?hostname=${hit.hostname}`);
                        setIsOpen(false);
                      }}
                      className="w-full text-left px-3 py-2 hover:bg-fill-secondary transition-colors"
                    >
                      <p className="text-sm text-label-primary truncate">{hit.hostname}</p>
                      <p className="text-xs text-label-tertiary truncate">
                        {hit.os || '—'}
                        {hit.compliance_status ? ` · ${hit.compliance_status}` : ''}
                      </p>
                    </button>
                  ))}
                </SearchCategory>
              )}

              {data.results.credentials.length > 0 && (
                <SearchCategory label={`Credentials (${data.results.credentials.length})`}>
                  {data.results.credentials.map((hit) => (
                    <div
                      key={hit.id}
                      className="px-3 py-2 text-sm text-label-primary"
                    >
                      <p className="truncate">{hit.credential_name}</p>
                      <p className="text-xs text-label-tertiary">{hit.credential_type}</p>
                    </div>
                  ))}
                </SearchCategory>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const SearchCategory: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <div>
    <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-label-tertiary font-medium">
      {label}
    </div>
    {children}
  </div>
);

function severityDot(severity: string): string {
  switch ((severity || '').toLowerCase()) {
    case 'critical':
      return 'bg-health-critical';
    case 'high':
      return 'bg-amber-500';
    case 'warning':
    case 'medium':
      return 'bg-health-warning';
    case 'low':
      return 'bg-health-healthy';
    default:
      return 'bg-label-tertiary';
  }
}

export default SiteSearchBar;
