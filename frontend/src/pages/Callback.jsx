import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { userManager } from '../utils/authService';
import { useAuth } from '../utils/AuthContext';

export default function Callback() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [error, setError] = useState('');

  useEffect(() => {
    userManager
      .signinRedirectCallback()
      .then(() => refresh())
      .then(() => navigate('/'))
      .catch((e) => setError(e.message || 'Sign-in failed'));
  }, []);

  return (
    <div className="login-screen">
      {error ? (
        <p style={{ color: 'var(--error)' }}>{error}</p>
      ) : (
        <>
          <div className="spinner" style={{ width: 28, height: 28 }} />
          <p>Completing sign-in&hellip;</p>
        </>
      )}
    </div>
  );
}
