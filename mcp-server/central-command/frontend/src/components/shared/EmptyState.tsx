import React from 'react';

interface EmptyStateProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export const EmptyState: React.FC<EmptyStateProps> = ({ icon, title, description, action }) => (
  <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
    <div className="w-12 h-12 rounded-full bg-fill-secondary flex items-center justify-center text-label-tertiary mb-4">
      {icon}
    </div>
    <h3 className="text-sm font-semibold text-label-primary mb-1">{title}</h3>
    <p className="text-xs text-label-tertiary max-w-xs mb-4">{description}</p>
    {action && (
      <button onClick={action.onClick} className="text-xs font-medium text-accent-primary hover:underline">
        {action.label}
      </button>
    )}
  </div>
);

export default EmptyState;
