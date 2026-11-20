import { useState } from 'react';
import { useAuth } from '../utils/AuthContext';

export default function AdvancedSettingsPanel({
  form,
  updateForm,
  prompts,
  defaults,
  onSavePrompt,
  onResetPrompt,
  settings,
  onSaveOutputPath,
  onSaveIdleTimeout,
}) {
  const { isAdmin } = useAuth();
  const [open, setOpen] = useState(true);
  const [promptSavedMsg, setPromptSavedMsg] = useState('');
  const [outputSaved, setOutputSaved] = useState(false);
  const [idleSaved, setIdleSaved] = useState(false);

  function flash(setter) {
    setter(true);
    setTimeout(() => setter(false), 3000);
  }

  async function handleSavePrompt() {
    const text = form.customPrompt.trim();
    if (!text) return;
    await onSavePrompt(form.promptMode, text);
    setPromptSavedMsg('Saved');
    setTimeout(() => setPromptSavedMsg(''), 3000);
  }

  async function handleResetPrompt() {
    const text = await onResetPrompt(form.promptMode);
    updateForm({ customPrompt: text });
    setPromptSavedMsg('Reset to default');
    setTimeout(() => setPromptSavedMsg(''), 3000);
  }

  function onPromptModeChange(mode) {
    updateForm({ promptMode: mode, customPrompt: prompts[mode] || '' });
  }

  return (
    <div className="panel">
      <div className="settings-toggle" onClick={() => setOpen(!open)}>
        <span className="toggle-title"><i className="fa-solid fa-sliders" /> Advanced Settings</span>
        <i className="fa-solid fa-chevron-down" style={{ transform: open ? 'rotate(180deg)' : '', transition: 'transform 0.25s' }} />
      </div>

      {open && (
        <div className="settings-body">
          <p className="section-label">Engine</p>
          <div className="form-group">
            <label>OCR Engine</label>
            <select value={form.engine} onChange={(e) => updateForm({ engine: e.target.value })} data-testid="engine-select">
              <option value="unlimited">🧠 Unlimited-OCR — Baidu GPU</option>
              <option value="glm">🤖 GLM-OCR — Ollama Vision</option>
            </select>
          </div>

          {form.engine === 'glm' && (
            <div data-testid="glm-params">
              <div className="params-grid">
                <div className="form-group">
                  <label>Temperature</label>
                  <div className="slider-row">
                    <input type="range" min="0" max="2" step="0.1" value={form.glmTemperature}
                      onChange={(e) => updateForm({ glmTemperature: parseFloat(e.target.value) })} />
                    <span className="slider-val">{form.glmTemperature.toFixed(1)}</span>
                  </div>
                </div>
                <div className="form-group">
                  <label>Repeat Penalty</label>
                  <div className="slider-row">
                    <input type="range" min="1.0" max="2.0" step="0.1" value={form.glmRepeatPenalty}
                      onChange={(e) => updateForm({ glmRepeatPenalty: parseFloat(e.target.value) })} />
                    <span className="slider-val">{form.glmRepeatPenalty.toFixed(1)}</span>
                  </div>
                </div>
              </div>
              <div className="params-grid">
                <div className="form-group">
                  <label>Context Window</label>
                  <select value={form.glmNumCtx} onChange={(e) => updateForm({ glmNumCtx: e.target.value })}>
                    <option value="2048">2 048</option>
                    <option value="4096">4 096</option>
                    <option value="8192">8 192</option>
                    <option value="16384">16 384</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Max Output Tokens</label>
                  <select value={form.glmNumPredict} onChange={(e) => updateForm({ glmNumPredict: e.target.value })}>
                    <option value="256">256</option>
                    <option value="512">512</option>
                    <option value="1024">1 024</option>
                    <option value="2048">2 048</option>
                    <option value="4096">4 096</option>
                  </select>
                </div>
              </div>
              <div className="form-group">
                <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                  <input type="checkbox" checked={form.glmTiling} onChange={(e) => updateForm({ glmTiling: e.target.checked })} />
                  Enable tiling (splits page into overlapping sections)
                </label>
              </div>
            </div>
          )}

          {form.engine === 'unlimited' && (
            <div className="form-group" data-testid="unlimited-params">
              <label>Base Size (inference resolution)</label>
              <select value={form.unlimitedBaseSize} onChange={(e) => updateForm({ unlimitedBaseSize: e.target.value })}>
                <option value="512">512 — Fastest</option>
                <option value="768">768</option>
                <option value="1024">1024 — Recommended</option>
                <option value="2048">2048 — High Detail (very slow)</option>
              </select>
            </div>
          )}

          <div className="form-group">
            <label>PDF Render Scale</label>
            <select value={form.scale} onChange={(e) => updateForm({ scale: e.target.value })}>
              <option value="1.0">1.0× Standard</option>
              <option value="1.5">1.5× High Detail</option>
              <option value="2.0">2.0× Ultra Detail</option>
            </select>
          </div>

          <hr className="section-divider" />

          <p className="section-label">Instructions / Prompt</p>
          <div className="form-group">
            <label>Prompt Mode</label>
            <select value={form.promptMode} onChange={(e) => onPromptModeChange(e.target.value)}>
              <option value="document">Document (Default)</option>
              <option value="generic">Generic OCR</option>
              <option value="captcha">Captcha</option>
            </select>
          </div>
          <div className="form-group">
            <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', textTransform: 'none', letterSpacing: 'normal' }}>
              <span style={{ fontSize: '0.77rem', fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Meta-Prompt</span>
              {promptSavedMsg && <span className="flash-saved">{promptSavedMsg}</span>}
            </label>
            <textarea
              rows={6}
              value={form.customPrompt}
              readOnly={!isAdmin}
              onChange={(e) => updateForm({ customPrompt: e.target.value })}
            />
            {isAdmin && (
              <div style={{ display: 'flex', gap: 7, marginTop: 8 }}>
                <button className="btn btn-sm btn-secondary" style={{ flex: 1 }} onClick={handleSavePrompt}>
                  <i className="fa-solid fa-floppy-disk" /> Save
                </button>
                <button className="btn btn-sm btn-outline" onClick={handleResetPrompt}>
                  <i className="fa-solid fa-rotate-left" /> Reset
                </button>
              </div>
            )}
          </div>

          {isAdmin && (
            <>
              <hr className="section-divider" />
              <p className="section-label">Output</p>
              <div className="form-group">
                <label>Auto-Export Path</label>
                <div style={{ display: 'flex', gap: 7 }}>
                  <input
                    type="text"
                    style={{ flex: 1 }}
                    defaultValue={settings.output_path}
                    id="outputPathInput"
                    placeholder="e.g. C:\Users\you\Documents\OCR"
                  />
                  <button
                    className="btn btn-sm btn-secondary"
                    title="Save path"
                    onClick={() => {
                      const val = document.getElementById('outputPathInput').value.trim();
                      onSaveOutputPath(val);
                      flash(setOutputSaved);
                    }}
                  >
                    <i className="fa-solid fa-floppy-disk" />
                  </button>
                </div>
                <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                  Results auto-export as .md to this folder on job completion.
                </p>
                {outputSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
              </div>

              <hr className="section-divider" />
              <p className="section-label">Session</p>
              <div className="form-group">
                <label>Idle Shutdown (minutes, 0 = disabled)</label>
                <div style={{ display: 'flex', gap: 7 }}>
                  <input
                    type="number"
                    min="0"
                    max="120"
                    style={{ flex: 1 }}
                    defaultValue={settings.idle_timeout_minutes}
                    id="idleTimeoutInputEl"
                  />
                  <button
                    className="btn btn-sm btn-secondary"
                    title="Save"
                    onClick={() => {
                      const val = parseInt(document.getElementById('idleTimeoutInputEl').value, 10) || 0;
                      onSaveIdleTimeout(val);
                      flash(setIdleSaved);
                    }}
                  >
                    <i className="fa-solid fa-floppy-disk" />
                  </button>
                </div>
                {idleSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
