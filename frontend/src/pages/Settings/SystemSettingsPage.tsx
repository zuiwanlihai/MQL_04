import { Link } from 'react-router-dom';
import { Info, Palette, Settings2, User as UserIcon } from 'lucide-react';
import TwoFACard from '@/components/settings/TwoFACard';
import { useAuthStore } from '@/store/useAuthStore';
import { useThemeStore } from '@/store/useThemeStore';

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  trader: '交易员',
  analyst: '分析师',
  viewer: '访客',
};

const PLATFORM_VERSION = 'v0.1';

export default function SystemSettingsPage() {
  const user = useAuthStore((s) => s.user);
  const { redUpGreenDown, setRedUpGreenDown } = useThemeStore();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Settings2 className="w-5 h-5 text-gray-400" /> 系统设置
          </h1>
          <p className="text-sm text-gray-500 mt-1">个人资料、交易外观与平台信息</p>
        </div>
        <nav className="flex items-center gap-1 text-sm">
          <span className="px-3 py-1.5 rounded-md bg-surface-750 text-white">系统</span>
          <Link
            to="/settings/gateway"
            className="px-3 py-1.5 rounded-md text-gray-400 hover:bg-surface-800 hover:text-gray-200 transition-colors"
          >
            网关
          </Link>
        </nav>
      </div>

      {/* 个人资料 */}
      <section className="card">
        <div className="card-header">
          <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <UserIcon className="w-4 h-4 text-gray-500" /> 个人资料
          </h2>
        </div>
        <div className="card-body">
          {user ? (
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4 text-sm">
              <div>
                <dt className="text-gray-500">用户名</dt>
                <dd className="text-gray-100 mt-0.5">{user.username}</dd>
              </div>
              <div>
                <dt className="text-gray-500">全名</dt>
                <dd className="text-gray-100 mt-0.5">{user.full_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-gray-500">邮箱</dt>
                <dd className="text-gray-100 mt-0.5 font-mono">{user.email}</dd>
              </div>
              <div>
                <dt className="text-gray-500">角色</dt>
                <dd className="mt-0.5">
                  <span className="px-2 py-0.5 rounded-full text-xs bg-blue-500/10 text-blue-400 border border-blue-500/30">
                    {ROLE_LABELS[user.role] ?? user.role}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">注册时间</dt>
                <dd className="text-gray-100 mt-0.5">
                  {new Date(user.created_at).toLocaleString('zh-CN', { hour12: false })}
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">两步验证</dt>
                <dd className="mt-0.5">
                  <span className={`px-2 py-0.5 rounded-full text-xs border ${
                    user.totp_enabled
                      ? 'bg-profit/10 text-profit border-profit/30'
                      : 'bg-surface-700 text-gray-400 border-gray-600'
                  }`}>
                    {user.totp_enabled ? '已启用' : '未启用'}
                  </span>
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-gray-500">正在读取个人资料…</p>
          )}
          <p className="text-xs text-gray-600 mt-4">
            资料由认证系统管理，如需修改请联系管理员。
          </p>
        </div>
      </section>

      {/* 两步验证 */}
      <TwoFACard />

      {/* 交易外观 */}
      <section className="card">
        <div className="card-header">
          <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Palette className="w-4 h-4 text-gray-500" /> 交易外观
          </h2>
        </div>
        <div className="card-body space-y-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm text-gray-200">红涨绿跌</p>
              <p className="text-xs text-gray-500 mt-0.5">
                行情涨跌幅与 K 线蜡烛的涨跌方向配色（中国市场惯例）。关闭则切换为国际惯例绿涨红跌。
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={redUpGreenDown}
              onClick={() => setRedUpGreenDown(!redUpGreenDown)}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                redUpGreenDown ? 'bg-loss' : 'bg-profit'
              }`}
              title={redUpGreenDown ? '当前：红涨绿跌' : '当前：绿涨红跌'}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  redUpGreenDown ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* 实时预览 */}
          <div className="rounded-md border border-gray-700/50 bg-surface-900 p-4">
            <p className="text-xs text-gray-500 mb-2">预览</p>
            <div className="flex items-center gap-8 font-mono text-lg">
              <div>
                <p className="text-2xs text-gray-600 mb-0.5">上涨</p>
                <p className={redUpGreenDown ? 'text-loss' : 'text-profit'}>+1.25%</p>
              </div>
              <div>
                <p className="text-2xs text-gray-600 mb-0.5">下跌</p>
                <p className={redUpGreenDown ? 'text-profit' : 'text-loss'}>-0.86%</p>
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-3">
              注：账户盈亏的赚/亏配色为语义色（不受此开关影响），此处仅切换行情涨跌方向色。
            </p>
          </div>
        </div>
      </section>

      {/* 平台信息 */}
      <section className="card">
        <div className="card-header">
          <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Info className="w-4 h-4 text-gray-500" /> 平台信息
          </h2>
        </div>
        <div className="card-body">
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-gray-500">平台</dt>
              <dd className="text-gray-100 mt-0.5">MT5 Web 量化交易平台</dd>
            </div>
            <div>
              <dt className="text-gray-500">版本</dt>
              <dd className="text-gray-100 mt-0.5 font-mono">{PLATFORM_VERSION}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-gray-500">已交付模块</dt>
              <dd className="text-gray-400 mt-1 text-xs leading-relaxed">
                认证 · 行情数据 · 策略研发 · 回测 · 虚拟下单 · 实盘部署与风控 · 监控与告警 · 设置中心
              </dd>
            </div>
          </dl>
        </div>
      </section>
    </div>
  );
}
