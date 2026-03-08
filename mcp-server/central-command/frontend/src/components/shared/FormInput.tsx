import React from 'react';

interface FormInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helpText?: string;
}

export const FormInput: React.FC<FormInputProps> = ({ label, error, helpText, className = '', id, ...props }) => {
  const inputId = id || `input-${label?.toLowerCase().replace(/\s+/g, '-')}`;
  return (
    <div className={className}>
      {label && (
        <label htmlFor={inputId} className="form-label">{label}</label>
      )}
      <input
        id={inputId}
        className={`form-input ${error ? 'form-input-error' : ''}`}
        aria-invalid={!!error}
        aria-describedby={error ? `${inputId}-error` : helpText ? `${inputId}-help` : undefined}
        {...props}
      />
      {error && (
        <p id={`${inputId}-error`} className="mt-1 text-xs text-health-critical">{error}</p>
      )}
      {helpText && !error && (
        <p id={`${inputId}-help`} className="mt-1 text-xs text-label-tertiary">{helpText}</p>
      )}
    </div>
  );
};

export default FormInput;
