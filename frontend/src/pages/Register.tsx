import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/useAuthStore';
import { apiError } from '@/lib/api';

export default function Register() {
  const navigate = useNavigate();
  const { register } = useAuthStore();

  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError('两次输入的密码不一致');
      return;
    }
    setSubmitting(true);
    try {
      await register({ email, username, password, full_name: fullName });
      navigate('/mt5-login', { replace: true });
    } catch (err) {
      setError(apiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-surface-950 flex items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-white">创建账户</h1>
          <p className="text-gray-400 text-sm mt-2">
            首个注册账户将自动成为平台管理员
          </p>
        </div>

        <div className="card p-8">
          <form onSubmit={onSubmit} className="space-y-4">
            {error && (
              <div className="text-sm text-loss-light bg-loss/10 border border-loss/30 rounded-md px-3 py-2">
                {error}
              </div>
            )}

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

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="username" className="block text-sm text-gray-400 mb-1">
                  用户名
                </label>
                <input
                  id="username"
                  type="text"
                  required
                  minLength={3}
                  pattern="[A-Za-z0-9_.]+"
                  title="仅限字母、数字、下划线和点"
                  className="input-dark w-full"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="trader1"
                />
              </div>
              <div>
                <label htmlFor="fullName" className="block text-sm text-gray-400 mb-1">
                  姓名
                </label>
                <input
                  id="fullName"
                  type="text"
                  required
                  className="input-dark w-full"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="张三"
                />
              </div>
            </div>

            <div>
              <label htmlFor="password" className="block text-sm text-gray-400 mb-1">
                密码
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                className="input-dark w-full"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="至少 8 位字符"
              />
            </div>

            <div>
              <label htmlFor="confirm" className="block text-sm text-gray-400 mb-1">
                确认密码
              </label>
              <input
                id="confirm"
                type="password"
                required
                autoComplete="new-password"
                className="input-dark w-full"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="再次输入密码"
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="btn-primary w-full disabled:opacity-50"
            >
              {submitting ? '创建中…' : '创建账户'}
            </button>
          </form>

          <p className="text-sm text-gray-500 text-center mt-6">
            已有账户？{' '}
            <Link to="/login" className="text-blue-400 hover:text-blue-300">
              去登录
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
