import React from 'react';

type SpinnerSize = 'sm' | 'md' | 'lg' | 'xl';

interface SpinnerProps {
  size?: SpinnerSize;
  color?: string;
  className?: string;
}

const sizeClasses: Record<SpinnerSize, string> = {
  sm: 'h-4 w-4',
  md: 'h-6 w-6',
  lg: 'h-8 w-8',
  xl: 'h-12 w-12',
};

export const Spinner: React.FC<SpinnerProps> = ({
  size = 'md',
  color = 'text-accent-primary',
  className = '',
}) => {
  return (
    <svg
      className={`animate-spin ${sizeClasses[size]} ${color} ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      role="status"
      aria-label="Loading"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
};

// Full-page loading state
export const LoadingScreen: React.FC<{ message?: string }> = ({
  message = 'Loading...',
}) => {
  return (
    <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
      <Spinner size="xl" />
      <p className="text-label-secondary text-sm">{message}</p>
    </div>
  );
};

// Inline loading state
export const LoadingInline: React.FC<{ message?: string }> = ({
  message,
}) => {
  return (
    <div className="flex items-center gap-2 text-label-secondary">
      <Spinner size="sm" />
      {message && <span className="text-sm">{message}</span>}
    </div>
  );
};

// Skeleton loader for cards - glassmorphic shimmer
export const SkeletonCard: React.FC<{ lines?: number }> = ({ lines = 3 }) => {
  return (
    <div className="glass-card p-6">
      <div className="skeleton h-4 w-3/4 mb-4" />
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-3 mb-2"
          style={{ width: `${Math.random() * 40 + 50}%` }}
        />
      ))}
    </div>
  );
};

// Skeleton loader for text - glassmorphic shimmer
export const SkeletonText: React.FC<{ width?: string; height?: string }> = ({
  width = '100%',
  height = '1rem',
}) => {
  return (
    <div
      className="skeleton"
      style={{ width, height }}
    />
  );
};

export default Spinner;
