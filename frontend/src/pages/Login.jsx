import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DEV_MODE, getDevPersonas, devLogin, login } from '../utils/authService';
import { useAuth } from '../utils/AuthContext';

export default function Login() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [personas, setPersonas] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (DEV_MODE) {
      getDevPersonas().then(setPersonas);
    } else {
      login();
    }
  }, []);

  async function handlePersonaLogin(userId) {
    try {
      await devLogin(userId);
      await refresh();
      navigate('/');
    } catch (e) {
      setError(e.message || 'Login failed');
    }
  }

  if (!DEV_MODE) {
    return (
      <div className="login-screen">
        <div className="spinner" style={{ width: 28, height: 28 }} />
        <p>Redirecting to sign-in&hellip;</p>
      </div>
    );
  }

  return (
    <div className="login-screen">
      <h1 style={{ fontSize: '1.8rem' }}>Dev Login</h1>
      <p className="tagline">DEV_MODE is enabled — pick a test persona.</p>
      {error && <p style={{ color: 'var(--error)' }}>{error}</p>}
      {personas.map((p) => (
        <button
          key={p.user_id}
          className="btn btn-secondary dev-persona-btn"
          onClick={() => handlePersonaLogin(p.user_id)}
        >
          {p.name} ({p.roles.join(', ')})
        </button>
      ))}
    </div>
  );
}
