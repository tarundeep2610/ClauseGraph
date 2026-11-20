import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

let mockAuth = {};
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

import Header from './Header';

describe('Header — unified top bar', () => {
  it('renders the wordmark and a Settings button that calls onOpenSettings', () => {
    mockAuth = { user: null, logout: vi.fn() };
    const onOpenSettings = vi.fn();
    render(<Header onOpenSettings={onOpenSettings} />);
    fireEvent.click(screen.getByTestId('open-settings-btn'));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });

  it('renders the ClauseGraph wordmark and logo mark, not the old OCR Rokka branding', () => {
    mockAuth = { user: null, logout: vi.fn() };
    render(<Header onOpenSettings={vi.fn()} />);
    expect(screen.getByText('ClauseGraph')).toBeInTheDocument();
    expect(screen.queryByText('OCR Rokka')).not.toBeInTheDocument();
    expect(screen.getByTestId('app-header-logo')).toBeInTheDocument();
  });
});

describe('Header — user menu / logout', () => {
  it('opens the menu, shows the email, and calls logout when clicked', () => {
    const logout = vi.fn();
    mockAuth = { user: { profile: { email: 'a@b.com' } }, logout };

    render(<Header onOpenSettings={vi.fn()} />);

    expect(document.querySelector('.user-menu-dropdown')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /a@b.com/i }));
    expect(document.querySelector('.user-menu-dropdown')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it('falls back to sub when profile has no email', () => {
    mockAuth = { user: { profile: { sub: 'user-123' } }, logout: vi.fn() };
    render(<Header onOpenSettings={vi.fn()} />);
    expect(screen.getByRole('button', { name: /user-123/i })).toBeInTheDocument();
  });

  it('renders without the user menu when signed out (user is null)', () => {
    mockAuth = { user: null, logout: vi.fn() };
    expect(() => render(<Header onOpenSettings={vi.fn()} />)).not.toThrow();
    expect(screen.queryByRole('button', { name: /logout/i })).not.toBeInTheDocument();
  });

  it('closes the menu on an outside click without calling logout', () => {
    const logout = vi.fn();
    mockAuth = { user: { profile: { email: 'a@b.com' } }, logout };

    render(<Header onOpenSettings={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /a@b.com/i }));
    expect(document.querySelector('.user-menu-dropdown')).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(document.querySelector('.user-menu-dropdown')).not.toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
  });

  it('renders a circular avatar badge with the initial and a vertical divider', () => {
    mockAuth = { user: { profile: { email: 'developer@example.com' } }, logout: vi.fn() };
    render(<Header onOpenSettings={vi.fn()} />);
    const badge = document.querySelector('.user-avatar-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('D');
    expect(document.querySelector('.bar-divider')).toBeInTheDocument();
  });
});
