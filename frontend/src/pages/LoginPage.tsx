import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Sparkles } from 'lucide-react';

import { ApiError, useAuthSafe } from '@/hooks/useAuthSafe';
import { Button, Card, Input } from '@/components/ui';
import { theme } from '@/lib/theme';

export default function LoginPage() {
  const { login, register } = useAuthSafe();
  const navigate = useNavigate();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(username, password);
      } else {
        await register({
          email,
          username,
          password,
          full_name: fullName || undefined,
        });
      }
      toast.success(mode === 'login' ? 'Signed in' : 'Account created');
      navigate('/');
    } catch (error) {
      toast.error(error instanceof ApiError ? error.detail : 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: theme.space(6),
        background: `radial-gradient(1000px 520px at 50% -10%, ${theme.color.primarySoft}, ${theme.color.bg})`,
      }}
    >
      <div style={{ width: '100%', maxWidth: 420 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: theme.space(6), justifyContent: 'center' }}>
          <div
            style={{
              width: 38,
              height: 38,
              borderRadius: 10,
              background: `linear-gradient(135deg, ${theme.color.primary}, #a855f7)`,
              display: 'grid',
              placeItems: 'center',
            }}
            aria-hidden
          >
            <Sparkles size={19} color="#fff" />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 19, fontWeight: 700, letterSpacing: '-0.02em' }}>
              DevInsight
            </h1>
            <p style={{ margin: 0, fontSize: 11.5, color: theme.color.textMuted }}>
              Engineering analytics & predictive intelligence
            </p>
          </div>
        </div>

        <Card>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 3,
              padding: 4,
              background: theme.color.bgElevated,
              borderRadius: theme.radius.md,
              marginBottom: theme.space(5),
            }}
          >
            {(['login', 'register'] as const).map((value) => (
              <button
                key={value}
                onClick={() => setMode(value)}
                aria-pressed={mode === value}
                style={{
                  padding: '8px 12px',
                  fontSize: 13,
                  fontWeight: 620,
                  borderRadius: theme.radius.sm,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  background: mode === value ? theme.color.surfaceHover : 'transparent',
                  color: mode === value ? theme.color.text : theme.color.textMuted,
                  border: '1px solid transparent',
                }}
              >
                {value === 'login' ? 'Sign in' : 'Create account'}
              </button>
            ))}
          </div>

          <form onSubmit={submit} style={{ display: 'grid', gap: theme.space(4) }}>
            {mode === 'register' && (
              <>
                <Input
                  label="Email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
                <Input
                  label="Full name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                />
              </>
            )}
            <Input
              label="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
              hint={mode === 'login' ? 'Email also works' : '3–64 characters'}
            />
            <Input
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              hint={mode === 'register' ? 'At least 8 characters with a letter and a digit' : undefined}
            />
            <Button type="submit" loading={busy} full>
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </Button>
          </form>

          {mode === 'register' && (
            <p style={{ margin: `${theme.space(4)} 0 0`, fontSize: 11.5, color: theme.color.textFaint, lineHeight: 1.6 }}>
              The first account created becomes an administrator. Everyone after that is an
              analyst. Passwords are hashed with bcrypt and are never stored in plaintext.
            </p>
          )}
        </Card>

        <p style={{ marginTop: theme.space(5), textAlign: 'center', fontSize: 11.5, color: theme.color.textFaint }}>
          GitHub credentials are configured server-side. This client never sees a GitHub token.
        </p>
      </div>
    </div>
  );
}
