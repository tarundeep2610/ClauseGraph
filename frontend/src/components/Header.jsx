import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../utils/AuthContext';

export default function Header({ idleState, onExit }) {
  const { isAdmin, user, logout } = useAuth();
  const { label, className, text } = idleState;

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
      <h1>GLM-OCR &amp; Baidu Unlimited Engine</h1>
      <p className="tagline">Survives browser refresh, tab closes &amp; connection drops.</p>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div id="idlePill" className={className}>
          <i className="fa-solid fa-circle" style={{ fontSize: '0.5rem' }} /> {text ?? label}
        </div>
        {isAdmin && (
          <button id="exitBtn" onClick={onExit}>
            <i className="fa-solid fa-power-off" /> Exit &amp; Free Memory
          </button>
        )}
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
