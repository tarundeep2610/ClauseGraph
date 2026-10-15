import UserMenu from './UserMenu';
import { APP_NAME } from '../utils/appInfo';

// The single top bar used for LibraryHome (StudioView uses StudioAppBar) —
// per the mockup, there is only one flat .top-file-bar per screen.
//
// The logomark/Settings icons are drawn inline (not FontAwesome glyphs) to
// match the research-backed "sophisticated tool" header pass: a unicode/icon-
// font glyph floating next to a refined type system reads as a placeholder,
// a drawn mark reads as considered (see plan: Header "Sophisticated Tool"
// Overhaul, round 5b).
export default function Header({ onOpenSettings }) {
  return (
    <header className="top-file-bar app-header">
      <div className="bar-left">
        <a
          href="/"
          className="app-header-wordmark"
          style={{ textDecoration: 'none' }}
          title="Return to Library Home"
        >
          <span className="app-header-logo" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="5" y="3" width="14" height="18" rx="2" />
              <line x1="8.5" y1="8" x2="15.5" y2="8" />
              <line x1="8.5" y1="12" x2="15.5" y2="12" />
              <line x1="8.5" y1="16" x2="13" y2="16" />
            </svg>
          </span>
          {APP_NAME}
        </a>
      </div>
      <div className="bar-right">
        <button type="button" className="btn-header-ghost" onClick={onOpenSettings} data-testid="open-settings-btn">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          Settings
        </button>
        <UserMenu />
      </div>
    </header>
  );
}
