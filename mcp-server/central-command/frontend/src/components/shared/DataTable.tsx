import React, { useState, useMemo } from 'react';

interface Column<T> {
  key: string;
  label: string;
  sortable?: boolean;
  width?: string;
  render?: (item: T, index: number) => React.ReactNode;
  align?: 'left' | 'center' | 'right';
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  onRowClick?: (item: T) => void;
  rowKey: (item: T) => string;
  emptyMessage?: string;
  emptyIcon?: React.ReactNode;
  className?: string;
  stickyHeader?: boolean;
  compact?: boolean;
}

type SortDir = 'asc' | 'desc' | null;

function SortIcon({ active, direction }: { active: boolean; direction: SortDir }) {
  return (
    <span className={`inline-flex ml-1 transition-opacity ${active ? 'opacity-100' : 'opacity-30'}`}>
      <svg width="10" height="14" viewBox="0 0 10 14" fill="none">
        <path d="M5 1L8 5H2L5 1Z" fill="currentColor" opacity={direction === 'asc' && active ? 1 : 0.3}/>
        <path d="M5 13L2 9H8L5 13Z" fill="currentColor" opacity={direction === 'desc' && active ? 1 : 0.3}/>
      </svg>
    </span>
  );
}

export function DataTable<T>({
  columns,
  data,
  onRowClick,
  rowKey,
  emptyMessage = 'No data available',
  emptyIcon,
  className = '',
  stickyHeader = false,
  compact = false,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(null);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(prev => prev === 'asc' ? 'desc' : prev === 'desc' ? null : 'asc');
      if (sortDir === 'desc') setSortKey(null);
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const sortedData = useMemo(() => {
    if (!sortKey || !sortDir) return data;
    return [...data].sort((a, b) => {
      const aVal = (a as Record<string, unknown>)[sortKey];
      const bVal = (b as Record<string, unknown>)[sortKey];
      if (aVal == null) return 1;
      if (bVal == null) return -1;
      const cmp = String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
      return sortDir === 'desc' ? -cmp : cmp;
    });
  }, [data, sortKey, sortDir]);

  const cellPadding = compact ? 'px-3 py-2' : 'px-4 py-3';

  return (
    <div className={`overflow-x-auto rounded-ios-md ${className}`}>
      <table className="w-full">
        <thead>
          <tr className={`border-b border-separator-medium ${stickyHeader ? 'sticky top-0 z-10 bg-background-secondary' : ''}`}>
            {columns.map(col => (
              <th
                key={col.key}
                className={`${cellPadding} text-xs font-semibold uppercase tracking-wider text-label-secondary text-${col.align || 'left'} ${col.sortable ? 'cursor-pointer select-none hover:text-label-primary transition-colors' : ''}`}
                style={col.width ? { width: col.width } : undefined}
                onClick={col.sortable ? () => handleSort(col.key) : undefined}
              >
                <span className="inline-flex items-center">
                  {col.label}
                  {col.sortable && <SortIcon active={sortKey === col.key} direction={sortKey === col.key ? sortDir : null} />}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="stagger-list">
          {sortedData.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-4 py-12 text-center">
                <div className="flex flex-col items-center gap-2">
                  {emptyIcon && <div className="text-label-tertiary">{emptyIcon}</div>}
                  <p className="text-sm text-label-tertiary">{emptyMessage}</p>
                </div>
              </td>
            </tr>
          ) : (
            sortedData.map((item, idx) => (
              <tr
                key={rowKey(item)}
                className={`border-b border-separator-light transition-colors ${
                  onRowClick ? 'cursor-pointer hover:bg-fill-secondary' : ''
                }`}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
              >
                {columns.map(col => (
                  <td key={col.key} className={`${cellPadding} text-${col.align || 'left'}`}>
                    {col.render ? col.render(item, idx) : String((item as Record<string, unknown>)[col.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export type { Column, DataTableProps };

export default DataTable;
