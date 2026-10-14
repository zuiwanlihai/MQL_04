import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api, apiError, clearTokens, getRefreshToken, setTokens } from '@/lib/api';
import type {
  LoginRequest,
  LoginResult,
  RegisterRequest,
  Session,
  TwoFAChallenge,
  User,
} from '@/types/api';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;

  setSession: (session: Session) => void;
  /** Password step. 2FA accounts resolve to a challenge WITHOUT logging in. */
  login: (req: LoginRequest) => Promise<LoginResult>;
  /** Code step for 2FA accounts — issues the real session on success. */
  loginWith2FA: (preAuthToken: string, code: string) => Promise<void>;
  register: (req: RegisterRequest) => Promise<void>;
  checkSession: () => Promise<void>;
  logout: () => Promise<void>;
}

function isChallenge(r: LoginResult): r is TwoFAChallenge {
  return (r as TwoFAChallenge).requires_2fa === true;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      isLoading: true,
      isAuthenticated: false,

      setSession: (session) => {
        setTokens(session.access_token, session.refresh_token);
        set({
          user: session.user,
          accessToken: session.access_token,
          refreshToken: session.refresh_token,
          isAuthenticated: true,
          isLoading: false,
        });
      },

      login: async (req) => {
        const { data } = await api.post<LoginResult>('/auth/login', req);
        if (!isChallenge(data)) get().setSession(data);
        return data;
      },

      loginWith2FA: async (preAuthToken, code) => {
        const { data } = await api.post<Session>('/auth/login/2fa', {
          pre_auth_token: preAuthToken,
          totp_code: code,
        });
        get().setSession(data);
      },

      register: async (req) => {
        const { data } = await api.post<Session>('/auth/register', req);
        get().setSession(data);
      },

      checkSession: async () => {
        // No refresh token → definitively logged out.
        if (!getRefreshToken()) {
          clearTokens();
          set({
            user: null,
            accessToken: null,
            refreshToken: null,
            isAuthenticated: false,
            isLoading: false,
          });
          return;
        }
        try {
          // POST /auth/refresh with the stored refresh token.
          const { data } = await api.post<Session>('/auth/refresh', {
            refresh_token: getRefreshToken(),
          });
          get().setSession(data);
        } catch (err) {
          void apiError(err); // error already handled by interceptor fallback
          clearTokens();
          set({
            user: null,
            accessToken: null,
            refreshToken: null,
            isAuthenticated: false,
            isLoading: false,
          });
        }
      },

      logout: async () => {
        const refreshToken = get().refreshToken;
        if (refreshToken) {
          try {
            await api.post('/auth/logout', { refresh_token: refreshToken });
          } catch {
            // Best effort — revoke locally regardless.
          }
        }
        clearTokens();
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          isLoading: false,
        });
      },
    }),
    {
      name: 'mt5quant.auth',
      // Only persist the raw tokens; user/isAuthenticated are re-derived
      // from the refresh response on every app load (checkSession).
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
      }),
      onRehydrateStorage: () => (state) => {
        // Sync module-local token helpers with what was rehydrated.
        if (state?.accessToken && state?.refreshToken) {
          setTokens(state.accessToken, state.refreshToken);
        }
      },
    },
  ),
);

export { apiError };
