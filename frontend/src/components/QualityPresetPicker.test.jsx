import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import QualityPresetPicker, { QUALITY_PRESETS, detectQualityPreset } from './QualityPresetPicker';

const standardForm = {
  engine: 'unlimited',
  scale: '1.5',
  promptMode: 'document',
  unlimitedBaseSize: '1024',
  processingMode: 'single',
  escalateBeforeFlagging: true,
};

describe('detectQualityPreset', () => {
  it('detects "standard" for the default form shape', () => {
    expect(detectQualityPreset(standardForm)).toBe('standard');
  });

  it('detects "precision" when scale/unlimitedBaseSize match the precision preset', () => {
    expect(detectQualityPreset({ ...standardForm, scale: '2.0', unlimitedBaseSize: '2048' })).toBe('precision');
  });
});

describe('QualityPresetPicker', () => {
  it('renders both preset options with the exact labels from the redesign plan', () => {
    render(<QualityPresetPicker form={standardForm} updateForm={vi.fn()} />);
    expect(screen.getByText('Standard Mode (Recommended)')).toBeInTheDocument();
    expect(screen.getByText('High Precision + Grounding')).toBeInTheDocument();
  });

  it('marks the preset matching the current form values as active', () => {
    render(<QualityPresetPicker form={standardForm} updateForm={vi.fn()} />);
    expect(screen.getByTestId('quality-preset-standard')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('quality-preset-precision')).toHaveAttribute('aria-pressed', 'false');
  });

  it('clicking "High Precision + Grounding" applies its full value mapping via updateForm', () => {
    const updateForm = vi.fn();
    render(<QualityPresetPicker form={standardForm} updateForm={updateForm} />);
    screen.getByTestId('quality-preset-precision').click();
    expect(updateForm).toHaveBeenCalledWith(QUALITY_PRESETS.precision.values);
  });

  it('clicking "Standard Mode" applies its full value mapping via updateForm', () => {
    const updateForm = vi.fn();
    const precisionForm = { ...standardForm, scale: '2.0', unlimitedBaseSize: '2048' };
    render(<QualityPresetPicker form={precisionForm} updateForm={updateForm} />);
    screen.getByTestId('quality-preset-standard').click();
    expect(updateForm).toHaveBeenCalledWith(QUALITY_PRESETS.standard.values);
  });
});
