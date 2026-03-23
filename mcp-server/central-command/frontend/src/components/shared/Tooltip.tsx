import React from 'react';

interface TooltipProps {
  text: string;
  children: React.ReactNode;
}

export const Tooltip: React.FC<TooltipProps> = ({ text, children }) => (
  <span className="relative group inline-flex items-center">
    {children}
    <span
      className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-gray-900 text-white text-xs rounded-lg px-3 py-2 max-w-xs opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-normal leading-relaxed z-50"
      role="tooltip"
    >
      {text}
      <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-gray-900" />
    </span>
  </span>
);

export const InfoTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip text={text}>
    <svg
      className="w-3.5 h-3.5 text-label-tertiary hover:text-label-secondary cursor-help inline-block ml-1"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  </Tooltip>
);
