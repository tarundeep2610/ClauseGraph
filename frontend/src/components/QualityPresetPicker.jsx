// Per the UX redesign plan (§8.1.4 "Zero Engine Knobs for Users" / §7 Feature
// 7), regular (non-admin) users never see raw engine/model/scale/prompt-mode
// controls. Instead they pick one of two quality presets, and this component
// maps that choice onto the same `form` fields SettingsModal's admin-only
// controls edit directly (and AppShell's handleSubmit already sends to the
// API) — so a preset pick behaves exactly like an admin setting those fields
// by hand, with no backend changes required. Rendered on LibraryHome next to
// the upload dropzone (a per-job choice at upload time), not inside
// SettingsModal (a persistent setting) — see LibraryHome.jsx.
export const QUALITY_PRESETS = {
  standard: {
    key: 'standard',
    label: 'Standard Mode (Recommended)',
    description: "Balanced speed and accuracy — today's default pipeline. Best for most documents.",
    values: {
      engine: 'unlimited',
      scale: '1.5',
      promptMode: 'document',
      unlimitedBaseSize: '1024',
      processingMode: 'single',
      escalateBeforeFlagging: true,
    },
  },
  precision: {
    key: 'precision',
    label: 'High Precision + Grounding',
    description: 'Higher-resolution rendering and inference for dense, small-print, or detail-critical pages — slower, more accurate.',
    values: {
      engine: 'unlimited',
      scale: '2.0',
      promptMode: 'document',
      unlimitedBaseSize: '2048',
      processingMode: 'single',
      escalateBeforeFlagging: true,
    },
  },
};

// Derived from `form` rather than stored as its own field, since the task
// shouldn't require adding a new field to AppShell's DEFAULT_FORM — the
// preset is just a label for a particular combination of existing fields.
export function detectQualityPreset(form) {
  const precision = QUALITY_PRESETS.precision.values;
  if (form.scale === precision.scale && form.unlimitedBaseSize === precision.unlimitedBaseSize) {
    return 'precision';
  }
  return 'standard';
}

export default function QualityPresetPicker({ form, updateForm }) {
  const active = detectQualityPreset(form);
  return (
    <div className="form-group quality-preset-picker" data-testid="quality-preset-picker">
      <label>Quality Preset</label>
      <div className="quality-preset-options">
        {Object.values(QUALITY_PRESETS).map((preset) => (
          <button
            type="button"
            key={preset.key}
            className={`quality-preset-option${active === preset.key ? ' active' : ''}`}
            data-testid={`quality-preset-${preset.key}`}
            aria-pressed={active === preset.key}
            onClick={() => updateForm(preset.values)}
          >
            <span className="quality-preset-name">{preset.label}</span>
            <span className="quality-preset-desc">{preset.description}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
