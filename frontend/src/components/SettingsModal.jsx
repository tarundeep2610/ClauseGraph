import { useState } from 'react';
import { useAuth } from '../utils/AuthContext';
import { useTheme } from '../utils/ThemeContext';

// Replaces the old header pills (idle/GPU/exit/theme) + AdvancedSettingsPanel
// sidebar card with a single consolidated "Settings" surface, reachable from
// the unified top bar on every route (see Header.jsx) instead of only from
// Library. Role-gated per-field, per the UX-redesign round-4 decisions:
// theme is the only thing every role sees; idle-timeout status/config, GPU
// status, Exit & Free Memory, and all engine/prompt/output/indexing config
// stay admin-only, the same boundary AdvancedSettingsPanel already drew for
// everything except theme. QualityPresetPicker is NOT here — it's a per-job
// choice made at upload time, not a persistent setting, and lives next to
// the Library upload dropzone instead (see LibraryHome.jsx).
//
// Laid out as an Adobe/Acrobat-style preferences dialog — a left category
// rail, one panel visible at a time on the right — instead of one long
// scrolling column, so "where do I change X" is always "pick the category",
// never "scroll and hope." Appearance (the only everyone-sees control) is
// deliberately both the first rail item and the default-open panel.
function gpuStatusDisplay(gpuStatus) {
  if (!gpuStatus) return null;
  if (gpuStatus.gpu_ocr_service_connection !== 'online') {
    return { className: 'offline', text: 'GPU: Offline' };
  }
  if (!gpuStatus.gpu_model_loaded) {
    return { className: 'warn', text: 'GPU: Idle' };
  }
  return { className: '', text: 'GPU: Ready' };
}

const CATEGORIES = [
  { id: 'appearance', label: 'Appearance', icon: 'fa-sun', adminOnly: false },
  { id: 'session', label: 'Session & GPU', icon: 'fa-server', adminOnly: true },
  { id: 'engine', label: 'Engine', icon: 'fa-microchip', adminOnly: true },
  { id: 'prompt', label: 'Prompt', icon: 'fa-quote-right', adminOnly: true },
  { id: 'output', label: 'Output', icon: 'fa-folder-open', adminOnly: true },
  { id: 'unlimited', label: 'Unlimited-OCR', icon: 'fa-bolt', adminOnly: true },
  { id: 'ai-indexing', label: 'AI Indexing', icon: 'fa-diagram-project', adminOnly: true },
];

export default function SettingsModal({
  open,
  onClose,
  idleState,
  gpuStatus,
  onExit,
  form,
  updateForm,
  prompts,
  defaults,
  onSavePrompt,
  onResetPrompt,
  settings,
  onSaveOutputPath,
  onSaveIdleTimeout,
  onSaveLongPrompts,
  onSaveGrounding,
  onSaveAiIndexing,
  onSaveAiChunkPages,
  onSaveAiReviewRulePrior,
  onSaveAiChunkOverlapPages,
  aiIndexStatus,
  onTestAiConnection,
}) {
  const { isAdmin } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [activeCategory, setActiveCategory] = useState('appearance');
  const [promptSavedMsg, setPromptSavedMsg] = useState('');
  const [outputSaved, setOutputSaved] = useState(false);
  const [idleSaved, setIdleSaved] = useState(false);
  const [longPromptsSaved, setLongPromptsSaved] = useState(false);
  const [groundingSaved, setGroundingSaved] = useState(false);
  const [aiIndexingSaved, setAiIndexingSaved] = useState(false);
  const [aiReviewRulePriorSaved, setAiReviewRulePriorSaved] = useState(false);
  const [aiChunkOverlapSaved, setAiChunkOverlapSaved] = useState(false);
  const [aiTestResult, setAiTestResult] = useState(null);
  const [aiTesting, setAiTesting] = useState(false);

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

  async function handleTestAiConnection() {
    setAiTesting(true);
    setAiTestResult(null);
    const result = await onTestAiConnection();
    setAiTestResult(result);
    setAiTesting(false);
  }

  if (!open) return null;

  const gpuDisplay = gpuStatusDisplay(gpuStatus);
  const visibleCategories = CATEGORIES.filter((c) => !c.adminOnly || isAdmin);
  const current = activeCategory === 'appearance' || isAdmin ? activeCategory : 'appearance';

  return (
    <div className="modal-overlay" onClick={onClose} data-testid="settings-modal-overlay">
      <div className="modal-panel settings-modal-panel" onClick={(e) => e.stopPropagation()} data-testid="settings-modal">
        <div className="settings-modal-header">
          <span className="modal-title"><i className="fa-solid fa-gear" /> Settings</span>
          <button type="button" className="btn-icon-close" onClick={onClose} aria-label="Close settings" data-testid="settings-modal-close-btn">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        <div className="settings-modal-layout">
          <nav className="settings-modal-nav" aria-label="Settings categories">
            {visibleCategories.map((cat) => (
              <button
                key={cat.id}
                type="button"
                className={`settings-nav-item${current === cat.id ? ' active' : ''}`}
                onClick={() => setActiveCategory(cat.id)}
                data-testid={`settings-nav-${cat.id}`}
                aria-current={current === cat.id ? 'true' : undefined}
              >
                <i className={`fa-solid ${cat.icon}`} /> {cat.label}
              </button>
            ))}
          </nav>

          <div className="settings-modal-content" data-testid="settings-modal-content">
            {current === 'appearance' && (
              <div className="form-group">
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={toggleTheme}
                  data-testid="theme-toggle-btn"
                >
                  <i className={`fa-solid ${theme === 'light' ? 'fa-moon' : 'fa-sun'}`} />
                  {theme === 'light' ? ' Switch to dark mode' : ' Switch to light mode'}
                </button>
              </div>
            )}

            {isAdmin && current === 'session' && (
              <>
                <div className="form-group settings-status-row">
                  <div id="idlePill" className={idleState.className}>
                    <i className="fa-solid fa-circle" style={{ fontSize: '0.5rem' }} /> {idleState.text ?? idleState.label}
                  </div>
                  {gpuDisplay && (
                    <div className={`gpu-status-pill ${gpuDisplay.className}`} data-testid="gpu-status-pill">
                      <i className="fa-solid fa-circle" style={{ fontSize: '0.5rem' }} /> {gpuDisplay.text}
                    </div>
                  )}
                  <button id="exitBtn" className="btn btn-sm btn-danger" onClick={onExit}>
                    <i className="fa-solid fa-power-off" /> Exit &amp; Free Memory
                  </button>
                </div>

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

            {isAdmin && current === 'engine' && (
              <>
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

                {form.engine === 'unlimited' && (
                  <div data-testid="batch-mode-params">
                    <div className="form-group">
                      <label>Page Processing</label>
                      <select
                        value={form.processingMode || 'single'}
                        onChange={(e) => updateForm({ processingMode: e.target.value })}
                      >
                        <option value="single">Single page at a time (default, proven)</option>
                        <option value="batch">Batch (multi-page, shares model context per chunk)</option>
                      </select>
                      <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                        Batch mode processes several pages together so the model retains context
                        across them — trades some per-page resolution for that context. Still
                        being validated; single-page is the recommended default.
                      </p>
                    </div>
                    {form.processingMode === 'batch' && (
                      <div className="form-group">
                        <label>Batch / Chunk Size (pages)</label>
                        <input
                          type="number"
                          min="2"
                          max="20"
                          value={form.batchSize || '5'}
                          onChange={(e) => updateForm({ batchSize: e.target.value })}
                          data-testid="batch-size-input"
                        />
                        <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                          Pages per batch call; consecutive batches share one page for context continuity.
                        </p>
                      </div>
                    )}
                    {form.processingMode === 'batch' && (
                      <div className="form-group">
                        <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                          <input
                            type="checkbox"
                            checked={form.enableOverlap !== false}
                            onChange={(e) => updateForm({ enableOverlap: e.target.checked })}
                            data-testid="enable-overlap-checkbox"
                          />
                          Overlap pages between batches
                        </label>
                        <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                          Shares one boundary page between consecutive batches for context continuity, and
                          enables table-continuation merging on export. Turning this off also disables
                          export-time table stitching, since there's no shared page to compare.
                        </p>
                      </div>
                    )}
                  </div>
                )}

                <div className="form-group">
                  <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                    <input
                      type="checkbox"
                      checked={form.escalateBeforeFlagging !== false}
                      onChange={(e) => updateForm({ escalateBeforeFlagging: e.target.checked })}
                      data-testid="escalate-before-flagging-checkbox"
                    />
                    Auto-escalate low-confidence pages before flagging for review
                  </label>
                  <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                    When a page scores low confidence, retry it once with stronger settings before
                    marking it for human review. Applies to both engines.
                  </p>
                </div>

                <div className="form-group">
                  <label>PDF Render Scale</label>
                  <select value={form.scale} onChange={(e) => updateForm({ scale: e.target.value })}>
                    <option value="1.0">1.0× Standard</option>
                    <option value="1.5">1.5× High Detail</option>
                    <option value="2.0">2.0× Ultra Detail</option>
                  </select>
                </div>
              </>
            )}

            {isAdmin && current === 'prompt' && (
              <>
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
                    onChange={(e) => updateForm({ customPrompt: e.target.value })}
                  />
                  <div style={{ display: 'flex', gap: 7, marginTop: 8 }}>
                    <button className="btn btn-sm btn-secondary" style={{ flex: 1 }} onClick={handleSavePrompt}>
                      <i className="fa-solid fa-floppy-disk" /> Save
                    </button>
                    <button className="btn btn-sm btn-outline" onClick={handleResetPrompt}>
                      <i className="fa-solid fa-rotate-left" /> Reset
                    </button>
                  </div>
                </div>
              </>
            )}

            {isAdmin && current === 'output' && (
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
            )}

            {isAdmin && current === 'unlimited' && (
              <>
                <div className="form-group">
                  <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                    <input
                      type="checkbox"
                      checked={!!settings.enable_unlimited_long_prompts}
                      onChange={(e) => {
                        onSaveLongPrompts(e.target.checked);
                        flash(setLongPromptsSaved);
                      }}
                      data-testid="enable-long-prompts-checkbox"
                    />
                    Send the full Document prompt to Unlimited-OCR
                  </label>
                  <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                    When off (default), any prompt over 80 characters — including the full
                    "Document" prompt above — is silently replaced with a short "document
                    parsing." label before being sent to Unlimited-OCR. Turn this on to send
                    the full prompt text unmodified. Applies to new jobs and future retries
                    created after this is turned on — jobs already in progress keep whatever
                    was frozen in at their own creation time.
                  </p>
                  {longPromptsSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
                </div>

                <div className="form-group">
                  <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                    <input
                      type="checkbox"
                      checked={!!settings.enable_unlimited_grounding}
                      onChange={(e) => {
                        onSaveGrounding(e.target.checked);
                        flash(setGroundingSaved);
                      }}
                      data-testid="enable-grounding-checkbox"
                    />
                    Ask Unlimited-OCR for layout/region tags (grounding)
                  </label>
                  <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                    On by default. Unlimited-OCR runs in its "gundam" config
                    (cropping the page into regions) instead of one global view —
                    confirmed, via direct tests against the model, to make it tag
                    each block with its type and bounding box, feeding both the
                    Structure tree's layout-confidence signal and the formatted
                    Markdown output (headings, tables, columns). Uncheck this for
                    a specific job to fall back to the faster single-pass mode
                    (no per-block tags, so that job's output stays plain text).
                    Jobs already in progress keep whatever was frozen in at their
                    own creation time.
                  </p>
                  {groundingSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
                </div>
              </>
            )}

            {isAdmin && current === 'ai-indexing' && (
              <>
                <div className="form-group">
                  <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                    <input
                      type="checkbox"
                      checked={!!settings.enable_ai_indexing}
                      onChange={(e) => {
                        onSaveAiIndexing(e.target.checked);
                        flash(setAiIndexingSaved);
                      }}
                      data-testid="enable-ai-indexing-checkbox"
                    />
                    Use AI to detect document structure (Gemini / RokkaAI)
                  </label>
                  <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                    When on, new jobs run an AI pass (in page batches) to find headings the
                    rule-based detector's fixed keyword/number vocabulary can't — e.g. a
                    heading with no family keyword and no number. The rule-based detector
                    still runs automatically as a per-batch fallback if the AI call fails or
                    returns something unusable, and requires an active credential below.
                    Frozen per job at creation time — a change here only affects new jobs.
                  </p>
                  {aiIndexingSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
                </div>

                {!!settings.enable_ai_indexing && (
                  <div className="form-group">
                    <label>Pages per AI call</label>
                    <input
                      type="number" min="1" max="50"
                      defaultValue={settings.ai_index_chunk_pages || 3}
                      onBlur={(e) => onSaveAiChunkPages(parseInt(e.target.value, 10) || 3)}
                    />
                  </div>
                )}

                {!!settings.enable_ai_indexing && (
                  <div className="form-group">
                    <label>Chunk overlap (pages)</label>
                    <input
                      type="number" min="0" max={Math.max((settings.ai_index_chunk_pages || 3) - 1, 0)}
                      defaultValue={settings.ai_index_chunk_overlap_pages ?? 0}
                      onBlur={(e) => onSaveAiChunkOverlapPages(parseInt(e.target.value, 10) || 0)}
                      data-testid="ai-chunk-overlap-input"
                    />
                    <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                      Re-sends this many boundary pages to the next chunk so a
                      missed/truncated heading gets a second look. Measured
                      (qwen3:8b, 3 trials/arm, see
                      AI_INDEXING_REVIEW_OVERLAP_MEASUREMENT.md): overlap 0/1/2
                      scored within the same band on precision/recall — no
                      accuracy benefit found — while higher overlap costs real
                      extra calls (~28-39% more chunks at overlap 2 vs 0 on a
                      larger document). Default changed to 0 accordingly.
                      Frozen per job at creation time; "Rebuild Index &
                      Reference Links" always uses the current value instead.
                    </p>
                    {aiChunkOverlapSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
                  </div>
                )}

                {!!settings.enable_ai_indexing && (
                  <div className="form-group">
                    <label className="check-row" style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.82rem' }}>
                      <input
                        type="checkbox"
                        checked={!!settings.ai_index_review_rule_prior}
                        onChange={(e) => {
                          onSaveAiReviewRulePrior(e.target.checked);
                          flash(setAiReviewRulePriorSaved);
                        }}
                        data-testid="enable-ai-review-rule-prior-checkbox"
                      />
                      Review rule-based detector's draft headings with AI (measured worse — off by default, kept as an opt-in experiment)
                    </label>
                    <p style={{ fontSize: '0.71rem', color: 'var(--muted)', marginTop: 5 }}>
                      When on, the rule-based detector's own headings are shown
                      to the AI as a draft to confirm, reject (with a verbatim
                      quote), or correct — rather than the AI judging headings
                      from scratch. Measured (qwen3:8b, 3 trials/arm, see
                      AI_INDEXING_REVIEW_OVERLAP_MEASUREMENT.md): precision
                      dropped sharply with this on (means 0.39-0.63 vs 0.94-0.98
                      off) and real structural errors (family-drift) appeared
                      that never happened with it off — on this model, this
                      made results worse, not better. Left as a per-job toggle
                      for further experimentation, not a recommended setting.
                      Frozen per job at creation time; "Rebuild Index &
                      Reference Links" always uses the current value instead.
                    </p>
                    {aiReviewRulePriorSaved && <span className="flash-saved"><i className="fa-solid fa-check" /> Saved</span>}
                  </div>
                )}

                <div className="form-group">
                  <label style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: '0.77rem', fontWeight: 600, color: 'var(--muted)' }}>
                    Provider
                  </label>
                  <p style={{ fontSize: '0.71rem', color: 'var(--muted)' }}>
                    The provider, model, and API key are configured on the server via environment
                    variables (<code>AI_INDEX_PROVIDER</code>, <code>AI_INDEX_MODEL</code>,{' '}
                    <code>AI_INDEX_BASE_URL</code>, <code>AI_INDEX_API_KEY</code>) — never entered
                    here, and never stored in the database.
                  </p>
                  {aiIndexStatus?.config ? (
                    <p style={{ fontSize: '0.78rem' }}>
                      <i className="fa-solid fa-check" style={{ color: 'var(--success)', marginRight: 4 }} />
                      Configured: <strong>{aiIndexStatus.config.provider}</strong> / {aiIndexStatus.config.model}
                      {aiIndexStatus.config.base_url && ` (${aiIndexStatus.config.base_url})`} — key ending •••{aiIndexStatus.config.key_hint}
                    </p>
                  ) : (
                    <p style={{ fontSize: '0.78rem', color: 'var(--warning)' }}>
                      <i className="fa-solid fa-triangle-exclamation" style={{ marginRight: 4 }} />
                      AI_INDEX_API_KEY is not set on the server — new jobs will silently fall back
                      to the rule-based detector even with the checkbox above on.
                    </p>
                  )}
                  <button className="btn btn-sm btn-outline" onClick={handleTestAiConnection} disabled={aiTesting} style={{ marginTop: 6 }}>
                    {aiTesting ? 'Testing…' : 'Test Connection'}
                  </button>
                  {aiTestResult && (
                    <span style={{ marginLeft: 8, fontSize: '0.78rem', color: aiTestResult.ok ? 'var(--success)' : 'var(--danger)' }}>
                      {aiTestResult.ok ? 'OK' : (aiTestResult.detail || 'Failed')}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
