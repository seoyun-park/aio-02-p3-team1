import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { AuthApiError, getAuthSession, login, logout } from '../api/auth';

interface AuthGateProps {
  children: (onLogout: () => Promise<void>) => ReactNode;
}

type GateState = 'checking' | 'locked' | 'authenticated' | 'unavailable';

export function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<GateState>('checking');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function refreshSession() {
    setState('checking');
    setMessage('');
    try {
      const session = await getAuthSession();
      setState(session.authenticated ? 'authenticated' : 'locked');
    } catch {
      setState('unavailable');
      setMessage('인증 서버에 연결할 수 없습니다. 서버 상태를 확인해 주세요.');
    }
  }

  useEffect(() => {
    let active = true;
    getAuthSession()
      .then((session) => {
        if (active) setState(session.authenticated ? 'authenticated' : 'locked');
      })
      .catch(() => {
        if (active) {
          setState('unavailable');
          setMessage('인증 서버에 연결할 수 없습니다. 서버 상태를 확인해 주세요.');
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || password.length === 0) return;

    setBusy(true);
    setMessage('');
    try {
      await login(password);
      setPassword('');
      setState('authenticated');
    } catch (error) {
      if (error instanceof AuthApiError && error.status === 401) {
        setMessage('비밀번호가 올바르지 않습니다. 다시 입력해 주세요.');
      } else if (error instanceof AuthApiError && error.status === 503) {
        setMessage('사이트 접속 설정이 완료되지 않았습니다. 담당자에게 문의해 주세요.');
      } else {
        setMessage('로그인 요청에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    await logout();
    setPassword('');
    setState('locked');
  }

  if (state === 'authenticated') return children(handleLogout);

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand" aria-label="SEOUL WAVE">
          <span className="auth-brand-mark" aria-hidden="true">≈</span>
          <span>SEOUL WAVE</span>
        </div>
        <div className="auth-divider" />
        <p className="auth-eyebrow">한강버스 문화행사 추천 서비스</p>
        <h1 id="auth-title">
          {state === 'checking' ? '접속 상태를 확인하고 있습니다' : '서비스 접속'}
        </h1>
        {state === 'checking' ? (
          <p className="auth-description">잠시만 기다려 주세요.</p>
        ) : state === 'unavailable' ? (
          <>
            <p className="auth-description" role="alert">{message}</p>
            <button className="auth-submit" type="button" onClick={() => void refreshSession()}>
              다시 시도
            </button>
          </>
        ) : (
          <>
            <p className="auth-description">비밀번호를 입력해 주세요.</p>
            <form className="auth-form" onSubmit={submit}>
              <label htmlFor="site-password">비밀번호</label>
              <input
                id="site-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                autoFocus
                required
                aria-describedby={message ? 'auth-error' : undefined}
              />
              {message && <p id="auth-error" className="auth-error" role="alert">{message}</p>}
              <button className="auth-submit" type="submit" disabled={busy || !password}>
                {busy ? '확인 중…' : '입장하기'}
              </button>
            </form>
          </>
        )}
        <p className="auth-footnote">서울의 문화행사와 한강버스 여정을 연결합니다.</p>
      </section>
    </main>
  );
}