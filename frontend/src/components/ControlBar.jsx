import { useAuth } from '../utils/AuthContext';

export default function ControlBar({ visible, onPauseCurrent, onStopAll }) {
  const { isAdmin } = useAuth();
  if (!visible) return null;

  return (
    <div id="controlBar" className="visible">
      <span className="control-bar-label"><div className="spinner" /> OCR extraction in progress</span>
      <div className="control-bar-actions">
        <button className="btn btn-sm btn-warn" onClick={onPauseCurrent}>
          <i className="fa-solid fa-pause" /> Pause Current
        </button>
        {isAdmin && (
          <button className="btn btn-sm btn-danger" onClick={onStopAll}>
            <i className="fa-solid fa-stop" /> Stop All
          </button>
        )}
      </div>
    </div>
  );
}
