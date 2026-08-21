import { useAuth } from '../utils/AuthContext';

export default function Header({ idleState, onExit }) {
  const { isAdmin } = useAuth();
  const { label, className, text } = idleState;

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
      </div>
    </header>
  );
}
