import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

// Mock AuthContext
const mockLogin = vi.fn();
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    login: mockLogin,
  }),
}));

// Mock shared components (OsirisCareLeaf uses SVG that may cause issues)
vi.mock('../components/shared', () => ({
  OsirisCareLeaf: ({ className }: { className?: string }) =>
    React.createElement('svg', { 'data-testid': 'leaf-icon', className }),
}));

import { Login } from './Login';

function renderLogin(onSuccess = vi.fn()) {
  return {
    onSuccess,
    ...render(
      <MemoryRouter>
        <Login onSuccess={onSuccess} />
      </MemoryRouter>
    ),
  };
}

describe('Login', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no OAuth providers
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ providers: { google: false, microsoft: false } }),
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('renders the login form with username and password fields', async () => {
    renderLogin();

    expect(screen.getByLabelText(/username or email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('renders branding text', () => {
    renderLogin();

    expect(screen.getAllByText('OsirisCare').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/msp compliance/i).length).toBeGreaterThan(0);
  });

  it('calls login and onSuccess on successful submit', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ success: true });
    const { onSuccess } = renderLogin();

    await user.type(screen.getByLabelText(/username or email/i), 'admin');
    await user.type(screen.getByLabelText(/password/i), 'secret123');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('admin', 'secret123');
      expect(onSuccess).toHaveBeenCalled();
    });
  });

  it('shows error message on failed login', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ success: false, error: 'Bad credentials' });
    const { onSuccess } = renderLogin();

    await user.type(screen.getByLabelText(/username or email/i), 'admin');
    await user.type(screen.getByLabelText(/password/i), 'wrong');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText('Bad credentials')).toBeInTheDocument();
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('shows default error message when login returns no error string', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ success: false });
    renderLogin();

    await user.type(screen.getByLabelText(/username or email/i), 'admin');
    await user.type(screen.getByLabelText(/password/i), 'wrong');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText('Invalid username or password')).toBeInTheDocument();
    });
  });

  it('shows "Signing in..." while login is in progress', async () => {
    const user = userEvent.setup();
    // Never-resolving login to keep loading state
    mockLogin.mockReturnValue(new Promise(() => {}));
    renderLogin();

    await user.type(screen.getByLabelText(/username or email/i), 'admin');
    await user.type(screen.getByLabelText(/password/i), 'test');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText(/signing in/i)).toBeInTheDocument();
    });
  });

  it('disables the submit button while loading', async () => {
    const user = userEvent.setup();
    mockLogin.mockReturnValue(new Promise(() => {}));
    renderLogin();

    await user.type(screen.getByLabelText(/username or email/i), 'admin');
    await user.type(screen.getByLabelText(/password/i), 'test');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      const button = screen.getByRole('button');
      expect(button).toBeDisabled();
    });
  });

  it('shows OAuth buttons when providers are enabled', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ providers: { google: true, microsoft: true } }),
    });

    renderLogin();

    await waitFor(() => {
      expect(screen.getByText(/sign in with google/i)).toBeInTheDocument();
      expect(screen.getByText(/sign in with microsoft/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/or continue with/i)).toBeInTheDocument();
  });

  it('does not show OAuth section when no providers are enabled', async () => {
    renderLogin();

    // Wait for provider fetch to complete
    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });

    expect(screen.queryByText(/or continue with/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sign in with google/i)).not.toBeInTheDocument();
  });

  it('fetches OAuth provider config on mount', async () => {
    renderLogin();

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith('/api/auth/oauth/config');
    });
  });
});
