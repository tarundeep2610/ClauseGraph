import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../utils/AuthContext';
import { useTheme } from '../utils/ThemeContext';
import { APP_NAME } from '../utils/appInfo';

// Presentational only - the actual health check lives on the backend
// (gpu_ocr_client.check_health(), surfaced via GET /health); this just maps
// the already-computed connection/model state to a label/style, the same
// way ViewerPanel's confidence badge maps backend-computed tiers.
function gpuStatusDisplay(gpuStatus) {
  if (!gpuStatus) return null; // no poll has resolved yet - render nothing rather than guess
  if (gpuStatus.gpu_ocr_service_connection !== 'online') {
    return { className: 'offline', text: 'GPU: Offline' };
  }
  if (!gpuStatus.gpu_model_loaded) {
    return { className: 'warn', text: 'GPU: Idle' };
  }
  return { className: '', text: 'GPU: Ready' };
}

export default function Header({ idleState, gpuStatus, onExit }) {
  const { isAdmin, user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { label, className, text } = idleState;
  const gpuDisplay = gpuStatusDisplay(gpuStatus);

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return;
    function handleOutsideClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [menuOpen]);

  const accountLabel = user?.profile?.email ?? user?.profile?.sub ?? 'Account';

  return (
    <header>
      <div className="badge"><i className="fa-solid fa-shield-halved" /> Fail-Safe Persistent Service</div>
      <h1>{APP_NAME}</h1>
      <p className="tagline">Turns scanned documents into searchable, cross-referenced structure.</p>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div id="idlePill" className={className}>
          <i className="fa-solid fa-circle" style={{ fontSize: '0.5rem' }} /> {text ?? label}
        </div>
        {gpuDisplay && (
          <div className={`gpu-status-pill ${gpuDisplay.className}`} data-testid="gpu-status-pill">
            <i className="fa-solid fa-circle" style={{ fontSize: '0.5rem' }} /> {gpuDisplay.text}
          </div>
        )}
        {isAdmin && (
          <button id="exitBtn" onClick={onExit}>
            <i className="fa-solid fa-power-off" /> Exit &amp; Free Memory
          </button>
        )}
        <button
          type="button"
          className="theme-toggle-btn"
          onClick={toggleTheme}
          title={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          data-testid="theme-toggle-btn"
        >
          <i className={`fa-solid ${theme === 'light' ? 'fa-moon' : 'fa-sun'}`} />
        </button>
        {user && (
          <div className="user-menu" ref={menuRef}>
            <button id="userMenuBtn" onClick={() => setMenuOpen((v) => !v)}>
              <i className="fa-solid fa-circle-user" /> {accountLabel}
              <i className="fa-solid fa-chevron-down" style={{ fontSize: '0.65rem' }} />
            </button>
            {menuOpen && (
              <div className="user-menu-dropdown">
                <div className="user-menu-email">{accountLabel}</div>
                <button className="user-menu-item" onClick={logout}>
                  <i className="fa-solid fa-right-from-bracket" /> Logout
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
