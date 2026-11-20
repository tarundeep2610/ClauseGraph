import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

let mockAuth = {};
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

import Header from './Header';

const idleState = { label: 'idle', className: '', text: 'Session active' };

describe('Header — user menu / logout', () => {
  it('opens the menu, shows the email, and calls logout when clicked', () => {
    const logout = vi.fn();
    mockAuth = { isAdmin: false, user: { profile: { email: 'a@b.com' } }, logout };

    render(<Header idleState={idleState} onExit={vi.fn()} />);

    expect(document.querySelector('.user-menu-dropdown')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /a@b.com/i }));
    expect(document.querySelector('.user-menu-dropdown')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it('falls back to sub when profile has no email', () => {
    mockAuth = { isAdmin: false, user: { profile: { sub: 'user-123' } }, logout: vi.fn() };
    render(<Header idleState={idleState} onExit={vi.fn()} />);
    expect(screen.getByRole('button', { name: /user-123/i })).toBeInTheDocument();
  });

  it('renders without the user menu when signed out (user is null)', () => {
    mockAuth = { isAdmin: false, user: null, logout: vi.fn() };
    expect(() => render(<Header idleState={idleState} onExit={vi.fn()} />)).not.toThrow();
    expect(screen.queryByRole('button', { name: /logout/i })).not.toBeInTheDocument();
  });

  it('closes the menu on an outside click without calling logout', () => {
    const logout = vi.fn();
    mockAuth = { isAdmin: false, user: { profile: { email: 'a@b.com' } }, logout };

    render(<Header idleState={idleState} onExit={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /a@b.com/i }));
    expect(document.querySelector('.user-menu-dropdown')).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(document.querySelector('.user-menu-dropdown')).not.toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
  });

  it('keeps the admin Exit button working alongside the user menu', () => {
    const onExit = vi.fn();
    const logout = vi.fn();
    mockAuth = { isAdmin: true, user: { profile: { email: 'admin@b.com' } }, logout };

    render(<Header idleState={idleState} onExit={onExit} />);
    fireEvent.click(screen.getByRole('button', { name: /Exit & Free Memory/i }));
    expect(onExit).toHaveBeenCalledOnce();
    expect(logout).not.toHaveBeenCalled();
  });
});

describe('Header — GPU status pill', () => {
  beforeEach(() => {
    mockAuth = { isAdmin: false, user: null, logout: vi.fn() };
  });

  it('renders nothing before the first health poll resolves (gpuStatus still null)', () => {
    render(<Header idleState={idleState} gpuStatus={null} onExit={vi.fn()} />);
    expect(screen.queryByTestId('gpu-status-pill')).not.toBeInTheDocument();
  });

  it('renders "GPU: Offline" when the GPU service connection is unreachable', () => {
    const gpuStatus = { gpu_ocr_service_connection: 'unreachable', gpu_model_loaded: false };
    render(<Header idleState={idleState} gpuStatus={gpuStatus} onExit={vi.fn()} />);
    const pill = screen.getByTestId('gpu-status-pill');
    expect(pill).toHaveTextContent('GPU: Offline');
    expect(pill.className).toContain('offline');
  });

  it('renders "GPU: Offline" when the GPU service connection is offline (reachable but errored)', () => {
    const gpuStatus = { gpu_ocr_service_connection: 'offline', gpu_model_loaded: false };
    render(<Header idleState={idleState} gpuStatus={gpuStatus} onExit={vi.fn()} />);
    expect(screen.getByTestId('gpu-status-pill')).toHaveTextContent('GPU: Offline');
  });

  it('renders "GPU: Idle" when online but the model is not loaded', () => {
    const gpuStatus = { gpu_ocr_service_connection: 'online', gpu_model_loaded: false };
    render(<Header idleState={idleState} gpuStatus={gpuStatus} onExit={vi.fn()} />);
    const pill = screen.getByTestId('gpu-status-pill');
    expect(pill).toHaveTextContent('GPU: Idle');
    expect(pill.className).toContain('warn');
  });

  it('renders "GPU: Ready" when online and the model is loaded', () => {
    const gpuStatus = { gpu_ocr_service_connection: 'online', gpu_model_loaded: true };
    render(<Header idleState={idleState} gpuStatus={gpuStatus} onExit={vi.fn()} />);
    const pill = screen.getByTestId('gpu-status-pill');
    expect(pill).toHaveTextContent('GPU: Ready');
    expect(pill.className).not.toContain('warn');
    expect(pill.className).not.toContain('offline');
  });
});
