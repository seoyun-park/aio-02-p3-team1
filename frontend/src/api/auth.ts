export interface AuthSession {
  authenticated: boolean;
}

export class AuthApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'AuthApiError';
  }
}

async function requestAuth<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = '인증 요청을 처리하지 못했습니다.';
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the generic message when the server did not return JSON.
    }
    throw new AuthApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export function getAuthSession(): Promise<AuthSession> {
  return requestAuth<AuthSession>('/api/auth/session', { method: 'GET' });
}

export function login(password: string): Promise<AuthSession> {
  return requestAuth<AuthSession>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export function logout(): Promise<AuthSession> {
  return requestAuth<AuthSession>('/api/auth/logout', { method: 'POST' });
}