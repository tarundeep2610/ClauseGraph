import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const { signinRedirectMock } = vi.hoisted(() => ({ signinRedirectMock: vi.fn() }));

vi.mock('./utils/authService', () => ({
  DEV_MODE: false,
  getUser: vi.fn().mockResolvedValue(null),
  getRole: () => 'user',
  login: async () => { signinRedirectMock(); },
  userManager: { signinRedirect: signinRedirectMock, signinRedirectCallback: vi.fn() },
}));

import App from './App';

describe('App — unauthenticated', () => {
  it('redirects to the login screen and triggers the AuthNexus sign-in redirect, rendering no protected UI', async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByText(/Redirecting to sign-in/i)).toBeInTheDocument());
    expect(signinRedirectMock).toHaveBeenCalled();
    expect(screen.queryByTestId('submit-btn')).not.toBeInTheDocument();
    expect(screen.queryByTestId('history-list')).not.toBeInTheDocument();
  });
});
