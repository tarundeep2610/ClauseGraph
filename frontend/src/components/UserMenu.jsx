import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../utils/AuthContext';

export default function UserMenu() {
  const { user, logout } = useAuth();
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

  if (!user) return null;

  const accountLabel = user?.profile?.email ?? user?.profile?.sub ?? 'Account';
  const accountInitial = (accountLabel.trim()[0] || 'A').toUpperCase();

  return (
    <>
      <div className="bar-divider" />
      <div className="user-menu" ref={menuRef}>
        <button
          id="userMenuBtn"
          aria-label={accountLabel}
          title={accountLabel}
          onClick={() => setMenuOpen((v) => !v)}
        >
          <span className="user-avatar-badge">{accountInitial}</span>
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--muted)' }} aria-hidden="true">
            <polyline points="6 9 12 15 18 9" />
          </svg>
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
    </>
  );
}
