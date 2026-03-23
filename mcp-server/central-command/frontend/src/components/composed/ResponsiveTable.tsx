import React from 'react';

interface Column<T> {
  key: string;
  header: string;
  render: (item: T) => React.ReactNode;
  mobileRender?: (item: T) => React.ReactNode;
  hideOnMobile?: boolean;
  sortable?: boolean;
  width?: string;
  align?: 'left' | 'center' | 'right';
}

interface ResponsiveTableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (item: T) => string;
  onRowClick?: (item: T) => void;
  emptyMessage?: string;
  emptyIcon?: React.ReactNode;
  mobileCardRender?: (item: T) => React.ReactNode;
  className?: string;
}

/**
 * ResponsiveTable -- table with built-in mobile card fallback.
 *
 * Desktop (>=768px): renders as a standard table.
 * Mobile (<768px): renders as a card list (uses mobileCardRender or auto-generates from columns).
 */
export function ResponsiveTable<T>({
  columns,
  data,
  rowKey,
  onRowClick,
  emptyMessage = 'No data available',
  emptyIcon,
  mobileCardRender,
  className = '',
}: ResponsiveTableProps<T>) {
  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
        {emptyIcon && <div className="text-label-tertiary mb-3">{emptyIcon}</div>}
        <p className="text-sm text-label-tertiary">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <>
      {/* Desktop table */}
      <div className={`hidden md:block overflow-x-auto rounded-ios-md ${className}`}>
        <table className="w-full">
          <thead>
            <tr className="border-b border-separator-medium">
              {columns.map(col => (
                <th
                  key={col.key}
                  className={`px-4 py-3 text-xs font-semibold uppercase tracking-wider text-label-secondary text-${col.align || 'left'}`}
                  style={col.width ? { width: col.width } : undefined}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="stagger-list">
            {data.map(item => (
              <tr
                key={rowKey(item)}
                className={`border-b border-separator-light transition-colors ${
                  onRowClick ? 'cursor-pointer hover:bg-fill-secondary' : ''
                }`}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
              >
                {columns.map(col => (
                  <td key={col.key} className={`px-4 py-3 text-${col.align || 'left'}`}>
                    {col.render(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile card list */}
      <div className={`md:hidden space-y-2 ${className}`}>
        {data.map(item => (
          <div
            key={rowKey(item)}
            className={`glass-card p-4 ${onRowClick ? 'cursor-pointer' : ''}`}
            onClick={onRowClick ? () => onRowClick(item) : undefined}
            role={onRowClick ? 'button' : undefined}
            tabIndex={onRowClick ? 0 : undefined}
          >
            {mobileCardRender ? (
              mobileCardRender(item)
            ) : (
              <div className="space-y-2">
                {columns
                  .filter(col => !col.hideOnMobile)
                  .map(col => (
                    <div key={col.key} className="flex items-center justify-between gap-2">
                      <span className="text-xs text-label-tertiary">{col.header}</span>
                      <span className="text-sm text-label-primary">
                        {col.mobileRender ? col.mobileRender(item) : col.render(item)}
                      </span>
                    </div>
                  ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

export type { Column as ResponsiveColumn };

export default ResponsiveTable;
