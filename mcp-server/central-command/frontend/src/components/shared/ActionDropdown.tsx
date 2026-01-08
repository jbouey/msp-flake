import React, { useState, useRef, useEffect } from 'react';

export interface ActionItem {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}

interface ActionDropdownProps {
  actions: ActionItem[];
  label?: string;
  icon?: React.ReactNode;
  disabled?: boolean;
  className?: string;
}

/**
 * Dropdown menu for additional actions
 */
export const ActionDropdown: React.FC<ActionDropdownProps> = ({
  actions,
  label = 'More',
  icon,
  disabled = false,
  className = '',
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  // Close on escape
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
    }

    return () => {
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  return (
    <div ref={dropdownRef} className={`relative inline-block ${className}`}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled}
        className={`
          flex items-center gap-1 px-2 py-1 text-sm rounded-ios
          bg-fill-secondary hover:bg-fill-tertiary
          text-label-secondary
          disabled:opacity-50 disabled:cursor-not-allowed
          transition-colors
        `}
      >
        {icon || (
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
            <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
          </svg>
        )}
        {label && <span>{label}</span>}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1 w-48 bg-fill-primary rounded-ios shadow-lg border border-separator-light z-[9999]">
          <div className="py-1">
            {actions.map((action, index) => (
              <button
                key={index}
                onClick={() => {
                  if (!action.disabled) {
                    action.onClick();
                    setIsOpen(false);
                  }
                }}
                disabled={action.disabled}
                className={`
                  w-full flex items-center gap-2 px-3 py-2 text-sm text-left
                  ${action.danger
                    ? 'text-white bg-gradient-to-r from-red-600 to-orange-500 hover:from-red-700 hover:to-orange-600 font-medium'
                    : 'text-label-primary hover:bg-fill-secondary'
                  }
                  disabled:opacity-50 disabled:cursor-not-allowed
                  transition-colors
                `}
              >
                {action.icon}
                {action.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default ActionDropdown;
