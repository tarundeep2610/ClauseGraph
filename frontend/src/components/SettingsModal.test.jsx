import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

let mockIsAdmin = true;
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}));

let mockTheme = 'dark';
const mockToggleTheme = vi.fn();
vi.mock('../utils/ThemeContext', () => ({
  useTheme: () => ({ theme: mockTheme, toggleTheme: mockToggleTheme }),
}));

import SettingsModal from './SettingsModal';

const baseProps = {
  open: true,
  onClose: vi.fn(),
  idleState: { label: 'Session active', className: '' },
  gpuStatus: null,
  onExit: vi.fn(),
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
  settings: { output_path: '', idle_timeout_minutes: 15, enable_unlimited_long_prompts: false },
  onSaveOutputPath: vi.fn(),
  onSaveIdleTimeout: vi.fn(),
  onSaveLongPrompts: vi.fn(),
  onSaveGrounding: vi.fn(),
  onSaveAiIndexing: vi.fn(),
  onSaveAiChunkPages: vi.fn(),
  onSaveAiReviewRulePrior: vi.fn(),
  onSaveAiChunkOverlapPages: vi.fn(),
  aiIndexStatus: null,
  onTestAiConnection: vi.fn(),
};

function goTo(category) {
  fireEvent.click(screen.getByTestId(`settings-nav-${category}`));
}

describe('SettingsModal — visibility', () => {
  it('renders nothing when closed', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} open={false} />);
    expect(screen.queryByTestId('settings-modal')).not.toBeInTheDocument();
  });

  it('renders the modal when open', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('settings-modal')).toBeInTheDocument();
  });
});

describe('SettingsModal — categorized nav (Adobe-style preferences layout)', () => {
  it('defaults to the Appearance category on open, with no nav click needed', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('settings-nav-appearance')).toHaveClass('active');
    expect(screen.getByTestId('theme-toggle-btn')).toBeInTheDocument();
  });

  it('lists all 7 categories for an admin, and only Appearance for a non-admin', () => {
    mockIsAdmin = true;
    const { unmount } = render(<SettingsModal {...baseProps} />);
    for (const id of ['appearance', 'session', 'engine', 'prompt', 'output', 'unlimited', 'ai-indexing']) {
      expect(screen.getByTestId(`settings-nav-${id}`)).toBeInTheDocument();
    }
    unmount();

    mockIsAdmin = false;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('settings-nav-appearance')).toBeInTheDocument();
    for (const id of ['session', 'engine', 'prompt', 'output', 'unlimited', 'ai-indexing']) {
      expect(screen.queryByTestId(`settings-nav-${id}`)).not.toBeInTheDocument();
    }
  });

  it('clicking a nav category swaps the visible panel — only the active category\'s fields are in the DOM', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('theme-toggle-btn')).toBeInTheDocument();
    expect(screen.queryByTestId('engine-select')).not.toBeInTheDocument();

    goTo('engine');
    expect(screen.getByTestId('settings-nav-engine')).toHaveClass('active');
    expect(screen.getByTestId('engine-select')).toBeInTheDocument();
    expect(screen.queryByTestId('theme-toggle-btn')).not.toBeInTheDocument();
  });
});

describe('SettingsModal — role gating', () => {
  it('shows theme toggle for everyone, and Output/Session/Engine nav items + Prompt Save/Reset only for admin', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('theme-toggle-btn')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-output')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-session')).toBeInTheDocument();

    goTo('prompt');
    expect(screen.getByText('Save')).toBeInTheDocument();
    expect(screen.getByText('Reset')).toBeInTheDocument();
  });

  it('shows only the theme toggle for a non-admin user — every admin-only category nav item (including Session/GPU) is hidden', () => {
    mockIsAdmin = false;
    render(<SettingsModal {...baseProps} />);
    expect(screen.getByTestId('theme-toggle-btn')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-output')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-session')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-engine')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-prompt')).not.toBeInTheDocument();
    expect(screen.queryByText('Save')).not.toBeInTheDocument();
    expect(screen.queryByText('Reset')).not.toBeInTheDocument();
  });

  it('shows GLM params only when engine=glm, and Unlimited params only when engine=unlimited', () => {
    mockIsAdmin = true;
    const { rerender } = render(<SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'glm' }} />);
    goTo('engine');
    expect(screen.getByTestId('glm-params')).toBeInTheDocument();
    expect(screen.queryByTestId('unlimited-params')).not.toBeInTheDocument();

    rerender(<SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited' }} />);
    expect(screen.queryByTestId('glm-params')).not.toBeInTheDocument();
    expect(screen.getByTestId('unlimited-params')).toBeInTheDocument();
  });

  it('clicking the close button calls onClose', () => {
    mockIsAdmin = true;
    const onClose = vi.fn();
    render(<SettingsModal {...baseProps} onClose={onClose} />);
    screen.getByTestId('settings-modal-close-btn').click();
    expect(onClose).toHaveBeenCalled();
  });
});

describe('SettingsModal — batch mode & escalation controls', () => {
  it('shows batch-mode controls only for the unlimited engine, not glm', () => {
    mockIsAdmin = true;
    const { rerender } = render(<SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited' }} />);
    goTo('engine');
    expect(screen.getByTestId('batch-mode-params')).toBeInTheDocument();

    rerender(<SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'glm' }} />);
    expect(screen.queryByTestId('batch-mode-params')).not.toBeInTheDocument();
  });

  it('only shows the batch-size input when processingMode=batch', () => {
    mockIsAdmin = true;
    const { rerender } = render(
      <SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'single' }} />
    );
    goTo('engine');
    expect(screen.queryByTestId('batch-size-input')).not.toBeInTheDocument();

    rerender(
      <SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'batch' }} />
    );
    expect(screen.getByTestId('batch-size-input')).toBeInTheDocument();
  });

  it('the escalation toggle is checked by default and calls updateForm when unchecked', () => {
    mockIsAdmin = true;
    const updateForm = vi.fn();
    render(<SettingsModal {...baseProps} updateForm={updateForm} />);
    goTo('engine');
    const checkbox = screen.getByTestId('escalate-before-flagging-checkbox');
    expect(checkbox.checked).toBe(true);
    checkbox.click();
    expect(updateForm).toHaveBeenCalledWith({ escalateBeforeFlagging: false });
  });

  it('only shows the overlap checkbox when processingMode=batch', () => {
    mockIsAdmin = true;
    const { rerender } = render(
      <SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'single' }} />
    );
    goTo('engine');
    expect(screen.queryByTestId('enable-overlap-checkbox')).not.toBeInTheDocument();

    rerender(
      <SettingsModal {...baseProps} form={{ ...baseProps.form, engine: 'unlimited', processingMode: 'batch' }} />
    );
    expect(screen.getByTestId('enable-overlap-checkbox')).toBeInTheDocument();
  });
});

describe('SettingsModal — Unlimited-OCR toggles', () => {
  it('long-prompt toggle is unchecked by default, shown only for admin, and saves on toggle', () => {
    mockIsAdmin = true;
    const onSaveLongPrompts = vi.fn();
    render(<SettingsModal {...baseProps} onSaveLongPrompts={onSaveLongPrompts} />);
    goTo('unlimited');
    const checkbox = screen.getByTestId('enable-long-prompts-checkbox');
    expect(checkbox.checked).toBe(false);
    checkbox.click();
    expect(onSaveLongPrompts).toHaveBeenCalledWith(true);
  });

  it('grounding toggle is hidden for a non-admin user', () => {
    mockIsAdmin = false;
    render(<SettingsModal {...baseProps} />);
    expect(screen.queryByTestId('settings-nav-unlimited')).not.toBeInTheDocument();
    expect(screen.queryByTestId('enable-grounding-checkbox')).not.toBeInTheDocument();
  });
});

describe('SettingsModal — AI-indexing review-rule-prior & chunk-overlap knobs', () => {
  const aiIndexingOnProps = {
    ...baseProps,
    settings: { ...baseProps.settings, enable_ai_indexing: true, ai_index_chunk_pages: 7 },
    onSaveAiReviewRulePrior: vi.fn(),
    onSaveAiChunkOverlapPages: vi.fn(),
  };

  it('both controls are absent when AI indexing itself is off', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} />);
    goTo('ai-indexing');
    expect(screen.queryByTestId('enable-ai-review-rule-prior-checkbox')).not.toBeInTheDocument();
    expect(screen.queryByText('Chunk overlap (pages)')).not.toBeInTheDocument();
  });

  it('chunk-overlap input defaults to 0 and saves the typed value on blur', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...aiIndexingOnProps} />);
    goTo('ai-indexing');
    const input = screen.getByTestId('ai-chunk-overlap-input');
    expect(input.value).toBe('0');
    input.focus();
    Object.defineProperty(input, 'value', { value: '2', configurable: true });
    input.blur();
    expect(aiIndexingOnProps.onSaveAiChunkOverlapPages).toHaveBeenCalledWith(2);
  });
});

describe('SettingsModal — idle/GPU status', () => {
  it('shows the idle pill and GPU pill for admin when gpuStatus has resolved', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} gpuStatus={{ gpu_ocr_service_connection: 'online', gpu_model_loaded: true }} />);
    goTo('session');
    expect(screen.getByTestId('gpu-status-pill')).toHaveTextContent('GPU: Ready');
  });

  it('renders nothing before the first health poll resolves (gpuStatus still null)', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} gpuStatus={null} />);
    goTo('session');
    expect(screen.queryByTestId('gpu-status-pill')).not.toBeInTheDocument();
  });

  it('renders "GPU: Offline" when the GPU service connection is unreachable', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} gpuStatus={{ gpu_ocr_service_connection: 'unreachable', gpu_model_loaded: false }} />);
    goTo('session');
    const pill = screen.getByTestId('gpu-status-pill');
    expect(pill).toHaveTextContent('GPU: Offline');
    expect(pill.className).toContain('offline');
  });

  it('renders "GPU: Idle" when online but the model is not loaded', () => {
    mockIsAdmin = true;
    render(<SettingsModal {...baseProps} gpuStatus={{ gpu_ocr_service_connection: 'online', gpu_model_loaded: false }} />);
    goTo('session');
    const pill = screen.getByTestId('gpu-status-pill');
    expect(pill).toHaveTextContent('GPU: Idle');
    expect(pill.className).toContain('warn');
  });

  it('GPU pill is not rendered at all for a non-admin user', () => {
    mockIsAdmin = false;
    render(<SettingsModal {...baseProps} gpuStatus={{ gpu_ocr_service_connection: 'online', gpu_model_loaded: true }} />);
    expect(screen.queryByTestId('gpu-status-pill')).not.toBeInTheDocument();
  });

  it('calls onExit when Exit & Free Memory is clicked (admin only)', () => {
    mockIsAdmin = true;
    const onExit = vi.fn();
    render(<SettingsModal {...baseProps} onExit={onExit} />);
    goTo('session');
    document.getElementById('exitBtn').click();
    expect(onExit).toHaveBeenCalled();
  });
});
