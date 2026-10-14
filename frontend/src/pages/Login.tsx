import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, ShieldCheck } from 'lucide-react';
import { useAuthStore } from '@/store/useAuthStore';
import { apiError } from '@/lib/api';

/**
 * 第一步：平台邮箱登录。登录成功后进入 /mt5-login 选择/绑定 MT5 交易账户
 * （账户类型驱动全局互斥：模拟 → 实盘禁用；实盘 → 虚拟下单禁用）。
 */
export default function Login() {
  const navigate = useNavigate();
  const { login, loginWith2FA } = useAuthStore();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 2FA step state
  const [preAuthToken, setPreAuthToken] = useState<string | null>(null);
  const [code, setCode] = useState('');

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await login({ email, password });
      if ('requires_2fa' in result && result.requires_2fa) {
        setPreAuthToken(result.pre_auth_token);
        setCode('');
      } else {
        navigate('/mt5-login', { replace: true });
      }
    } catch (err) {
      setError(apiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onVerify(e: FormEvent) {
    e.preventDefault();
    if (!preAuthToken) return;
    setError(null);
    setSubmitting(true);
    try {
      await loginWith2FA(preAuthToken, code);
      navigate('/mt5-login', { replace: true });
    } catch (err) {
      setError(apiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  function backToPassword() {
    setPreAuthToken(null);
    setCode('');
    setError(null);
  }

  return (
    <div className="min-h-screen bg-surface-950 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-white">MT5 量化交易平台</h1>
          <p className="text-gray-400 text-sm mt-2">
            {preAuthToken ? '两步验证' : '登录您的账户'}
          </p>
        </div>

        <div className="card p-8">
          {error && (
            <div className="text-sm text-loss-light bg-loss/10 border border-loss/30 rounded-md px-3 py-2 mb-4">
              {error}
            </div>
          )}

          {preAuthToken ? (
            <form onSubmit={onVerify} className="space-y-4">
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <ShieldCheck className="w-4 h-4 text-blue-400" />
                请输入验证器 App 的 6 位动态码，或一次性恢复码
              </div>
              <div>
                <label htmlFor="code" className="block text-sm text-gray-400 mb-1">
                  验证码
                </label>
                <input
                  id="code"
                  inputMode="text"
                  autoComplete="one-time-code"
                  autoFocus
                  required
                  className="input-dark w-full tracking-widest font-mono text-center"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="123456 或 XXXX-XXXX"
                />
              </div>
              <button
                type="submit"
                disabled={submitting || code.trim().length < 6}
                className="btn-primary w-full disabled:opacity-50"
              >
                {submitting ? '验证中…' : '验 证'}
              </button>
              <button
                type="button"
                className="btn-ghost w-full"
                onClick={backToPassword}
                disabled={submitting}
              >
                <ArrowLeft className="w-4 h-4" /> 返回上一步
              </button>
            </form>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm text-gray-400 mb-1">
                  邮箱
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  autoComplete="email"
                  className="input-dark w-full"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>

              <div>
                <label htmlFor="password" className="block text-sm text-gray-400 mb-1">
                  密码
                </label>
                <input
                  id="password"
                  type="password"
                  required
                  autoComplete="current-password"
                  className="input-dark w-full"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="btn-primary w-full disabled:opacity-50"
              >
                {submitting ? '登录中…' : '登 录'}
              </button>

              <p className="text-sm text-gray-500 text-center">
                还没有账户？{' '}
                <Link to="/register" className="text-blue-400 hover:text-blue-300">
                  立即注册
                </Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
