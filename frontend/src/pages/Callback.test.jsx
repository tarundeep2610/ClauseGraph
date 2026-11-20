import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { signinRedirectCallbackMock, refreshMock, navigateMock } = vi.hoisted(() => ({
  signinRedirectCallbackMock: vi.fn().mockResolvedValue({}),
  refreshMock: vi.fn().mockResolvedValue(null),
  navigateMock: vi.fn(),
}));

vi.mock('../utils/authService', () => ({
  userManager: { signinRedirectCallback: signinRedirectCallbackMock },
}));
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => ({ refresh: refreshMock }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

import Callback from './Callback';

describe('Callback', () => {
  it('completes the OIDC code exchange, refreshes auth state, then navigates to the dashboard', async () => {
    render(
      <MemoryRouter>
        <Callback />
      </MemoryRouter>
    );

    await waitFor(() => expect(signinRedirectCallbackMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(refreshMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/'));
  });
});
