import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

let mockIsAdmin = true;
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}));

import AdvancedSettingsPanel from './AdvancedSettingsPanel';

const baseProps = {
  form: {
    engine: 'unlimited',
    scale: '1.5',
    promptMode: 'document',
    customPrompt: 'a prompt',
    glmTiling: true,
    glmTemperature: 0,
    glmNumCtx: '8192',
    glmNumPredict: '2048',
    glmRepeatPenalty: 1.2,
    unlimitedBaseSize: '1024',
  },
  updateForm: vi.fn(),
  prompts: { document: 'a prompt' },
  defaults: { document: 'a prompt' },
  onSavePrompt: vi.fn(),
  onResetPrompt: vi.fn(),
  settings: { output_path: '', idle_timeout_minutes: 15 },
  onSaveOutputPath: vi.fn(),
  onSaveIdleTimeout: vi.fn(),
};

describe('AdvancedSettingsPanel — role gating', () => {
  it('shows Output/Session sections and prompt save/reset controls for admin', () => {
    mockIsAdmin = true;
    render(<AdvancedSettingsPanel {...baseProps} />);
    expect(screen.getByText('Output')).toBeInTheDocument();
    expect(screen.getByText('Session')).toBeInTheDocument();
    expect(screen.getByText('Save')).toBeInTheDocument(); // the prompt panel's text-labeled Save button
    expect(screen.getByText('Reset')).toBeInTheDocument();
  });

  it('hides Output/Session sections and prompt save/reset controls for a non-admin user', () => {
    mockIsAdmin = false;
    render(<AdvancedSettingsPanel {...baseProps} />);
    expect(screen.queryByText('Output')).not.toBeInTheDocument();
    expect(screen.queryByText('Session')).not.toBeInTheDocument();
    expect(screen.queryByText('Save')).not.toBeInTheDocument();
    expect(screen.queryByText('Reset')).not.toBeInTheDocument();
  });

  it('shows GLM params only when engine=glm, and Unlimited params only when engine=unlimited', () => {
    mockIsAdmin = true;
    const { rerender } = render(<AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'glm' }} />);
    expect(screen.getByTestId('glm-params')).toBeInTheDocument();
    expect(screen.queryByTestId('unlimited-params')).not.toBeInTheDocument();

    rerender(<AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited' }} />);
    expect(screen.queryByTestId('glm-params')).not.toBeInTheDocument();
    expect(screen.getByTestId('unlimited-params')).toBeInTheDocument();
  });
});

describe('AdvancedSettingsPanel — batch mode & escalation controls', () => {
  it('shows batch-mode controls only for the unlimited engine, not glm', () => {
    mockIsAdmin = true;
    const { rerender } = render(<AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited' }} />);
    expect(screen.getByTestId('batch-mode-params')).toBeInTheDocument();

    rerender(<AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'glm' }} />);
    expect(screen.queryByTestId('batch-mode-params')).not.toBeInTheDocument();
  });

  it('only shows the batch-size input when processingMode=batch', () => {
    mockIsAdmin = true;
    const { rerender } = render(
      <AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'single' }} />
    );
    expect(screen.queryByTestId('batch-size-input')).not.toBeInTheDocument();

    rerender(
      <AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'batch' }} />
    );
    expect(screen.getByTestId('batch-size-input')).toBeInTheDocument();
  });

  it('the escalation toggle is checked by default and calls updateForm when unchecked', () => {
    mockIsAdmin = true;
    const updateForm = vi.fn();
    render(<AdvancedSettingsPanel {...baseProps} updateForm={updateForm} />);
    const checkbox = screen.getByTestId('escalate-before-flagging-checkbox');
    expect(checkbox.checked).toBe(true);
    checkbox.click();
    expect(updateForm).toHaveBeenCalledWith({ escalateBeforeFlagging: false });
  });

  it('the escalation toggle is engine-agnostic — shown for glm too', () => {
    mockIsAdmin = true;
    render(<AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'glm' }} />);
    expect(screen.getByTestId('escalate-before-flagging-checkbox')).toBeInTheDocument();
  });

  it('only shows the overlap checkbox when processingMode=batch', () => {
    mockIsAdmin = true;
    const { rerender } = render(
      <AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'single' }} />
    );
    expect(screen.queryByTestId('enable-overlap-checkbox')).not.toBeInTheDocument();

    rerender(
      <AdvancedSettingsPanel {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'batch' }} />
    );
    expect(screen.getByTestId('enable-overlap-checkbox')).toBeInTheDocument();
  });

  it('the overlap toggle is checked by default and calls updateForm when unchecked', () => {
    mockIsAdmin = true;
    const updateForm = vi.fn();
    render(
      <AdvancedSettingsPanel
        {...baseProps}
        updateForm={updateForm}
        form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'batch' }}
      />
    );
    const checkbox = screen.getByTestId('enable-overlap-checkbox');
    expect(checkbox.checked).toBe(true);
    checkbox.click();
    expect(updateForm).toHaveBeenCalledWith({ enableOverlap: false });
  });
});
