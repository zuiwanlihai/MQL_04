import { useState } from 'react';
import { KeyRound, ShieldCheck, ShieldX } from 'lucide-react';
import { use2FAStatus, useDisable2FA, useEnable2FA, useSetup2FA } from '@/hooks/useAuth2fa';
import { apiError } from '@/lib/api';
import type { TwoFASetupOut } from '@/types/api';

type Stage = 'idle' | 'setup' | 'codes';

export default function TwoFACard() {
  const status = use2FAStatus();
  const setup = useSetup2FA();
  const enable = useEnable2FA();
  const disable = useDisable2FA();

  const [stage, setStage] = useState<Stage>('idle');
  const [setupData, setSetupData] = useState<TwoFASetupOut | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const [disablePassword, setDisablePassword] = useState('');
  const [disableMode, setDisableMode] = useState(false);

  const enabled = status.data?.enabled ?? false;
  const mutationError = setup.error ?? enable.error ?? disable.error;

  function startSetup() {
    setStage('setup');
    setCode('');
    setup.mutate(undefined, {
      onSuccess: (data) => setSetupData(data),
      onError: () => setStage('idle'),
    });
  }

  function confirmEnable() {
    enable.mutate(code.trim(), {
      onSuccess: (out) => {
        setCodes(out.recovery_codes);
        setStage('codes');
        void status.refetch();
      },
    });
  }

  function copyCodes() {
    void navigator.clipboard.writeText(codes.join('\n')).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  function doDisable() {
    disable.mutate(disablePassword, {
      onSuccess: () => {
        setDisableMode(false);
        setDisablePassword('');
        setStage('idle');
        void status.refetch();
      },
    });
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-gray-500" /> 两步验证（2FA）
        </h2>
        {enabled ? (
          <span className="px-2 py-0.5 rounded border text-xs bg-profit/15 text-profit border-profit/40">
            已启用
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded border text-xs bg-surface-700 text-gray-400 border-gray-600">
            未启用
          </span>
        )}
      </div>
      <div className="card-body space-y-4">

        {mutationError && (
          <div className="text-sm text-loss bg-loss/10 border border-loss/30 rounded-md px-3 py-2">
            {apiError(mutationError)}
          </div>
        )}

        {/* 已启用：状态 + 停用 */}
        {enabled && stage === 'idle' && (
          <>
            <p className="text-sm text-gray-400">
              登录时需在密码之外输入验证器动态码。剩余恢复码：
              <span className="font-mono text-gray-200 ml-1">{status.data?.recovery_codes_remaining ?? 0}</span> 个。
            </p>
            {disableMode ? (
              <div className="space-y-2">
                <input
                  type="password"
                  className="input-dark"
                  placeholder="输入账号密码以确认停用"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn-danger"
                    disabled={disable.isPending || disablePassword.length === 0}
                    onClick={doDisable}
                  >
                    确认停用 2FA
                  </button>
                  <button type="button" className="btn-ghost" onClick={() => setDisableMode(false)}>取消</button>
                </div>
              </div>
            ) : (
              <button type="button" className="btn-secondary" onClick={() => setDisableMode(true)}>
                <ShieldX className="w-4 h-4" /> 停用两步验证
              </button>
            )}
          </>
        )}

        {/* 未启用：发起设置 */}
        {!enabled && stage === 'idle' && (
          <>
            <p className="text-sm text-gray-400">
              为账号增加一道登录保护：使用任意 TOTP 验证器 App（Google Authenticator、Microsoft Authenticator 等）扫码启用。
            </p>
            <button type="button" className="btn-primary" disabled={setup.isPending} onClick={startSetup}>
              <ShieldCheck className="w-4 h-4" /> 启用两步验证
            </button>
          </>
        )}

        {/* 设置中：二维码 + 输入确认码 */}
        {stage === 'setup' && setupData && (
          <div className="space-y-4">
            <p className="text-sm text-gray-300">第 1 步：用验证器 App 扫描下方二维码（或手动录入密钥）</p>
            <div className="flex items-start gap-4 flex-wrap">
              <img
                src={setupData.qr_svg_base64}
                alt="2FA 二维码"
                className="w-44 h-44 bg-white rounded-md p-2"
              />
              <div className="space-y-1 min-w-0">
                <p className="text-xs text-gray-500">手动密钥（Base32）</p>
                <p className="text-sm font-mono text-gray-200 break-all select-all">{setupData.secret}</p>
                <p className="text-xs text-gray-600">密钥仅显示一次，落库为加密存储。</p>
              </div>
            </div>
            <p className="text-sm text-gray-300">第 2 步：输入验证器当前显示的 6 位动态码</p>
            <div className="flex items-center gap-2">
              <input
                className="input-dark w-40 font-mono tracking-widest text-center"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                maxLength={6}
                inputMode="numeric"
              />
              <button
                type="button"
                className="btn-primary"
                disabled={enable.isPending || code.trim().length !== 6}
                onClick={confirmEnable}
              >
                验证并启用
              </button>
              <button type="button" className="btn-ghost" onClick={() => { setStage('idle'); setSetupData(null); enable.reset(); }}>
                取消
              </button>
            </div>
          </div>
        )}

        {/* 启用成功：一次性恢复码 */}
        {stage === 'codes' && (
          <div className="space-y-3">
            <div className="text-sm text-profit flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" /> 两步验证已启用
            </div>
            <p className="text-sm text-gray-400">
              以下 <span className="text-gray-200">10 个恢复码</span> 仅显示这一次，请立即保存到安全位置。
              每个恢复码只能使用一次（验证器不可用时在登录第二步输入）。
            </p>
            <div className="grid grid-cols-2 gap-2 font-mono text-sm bg-surface-900 border border-gray-700/50 rounded-md p-3">
              {codes.map((c) => (
                <span key={c} className="text-gray-200">{c}</span>
              ))}
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn-secondary" onClick={copyCodes}>
                {copied ? '已复制' : '复制全部恢复码'}
              </button>
              <button type="button" className="btn-ghost" onClick={() => { setStage('idle'); setCodes([]); }}>
                我已保存
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
