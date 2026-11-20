export default function IdleBanner({ visible, expired, message, onKeepAlive }) {
  if (!visible) return null;
  return (
    <div id="idleBanner" className={expired ? 'expired' : ''}>
      <span>{message}</span>
      <button className="btn btn-sm btn-warn" onClick={onKeepAlive}>
        <i className="fa-solid fa-rotate-right" /> Keep Alive
      </button>
    </div>
  );
}
