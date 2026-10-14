import { useState } from 'react';
import { BellRing, Check, Plus, Send, Trash2, X } from 'lucide-react';
import {
  useCreateChannel,
  useDeleteChannel,
  useNotifyChannels,
  useTestChannel,
  useUpdateChannel,
} from '@/hooks/useNotify';
import { apiError } from '@/lib/api';
import type { AlertEvent, ChannelKind } from '@/types/api';

const KIND_LABELS: Record<ChannelKind, string> = {
  email: '邮件',
  telegram: 'Telegram',
  dingtalk: '钉钉',
  wecom: '企业微信',
};

const KIND_TARGET_HINT: Record<ChannelKind, string> = {
  email: '接收邮箱地址',
  telegram: 'bot_token:chat_id',
  dingtalk: '机器人 Webhook URL',
  wecom: '机器人 Webhook URL',
};

const KIND_TARGET_PLACEHOLDER: Record<ChannelKind, string> = {
  email: 'trader@example.com',
  telegram: '123456:9876543210',
  dingtalk: 'https://oapi.dingtalk.com/robot/send?access_token=***',
  wecom: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=***',
};

const LEVEL_LABELS: Record<AlertEvent['level'], string> = {
  info: '全部（含提示）',
  warn: '警告及以上',
  error: '错误及以上',
  critical: '仅严重',
};

const LEVELS: AlertEvent['level'][] = ['info', 'warn', 'error', 'critical'];

export default function NotifyChannelsCard() {
  const channels = useNotifyChannels();
  const create = useCreateChannel();
  const update = useUpdateChannel();
  const remove = useDeleteChannel();
  const test = useTestChannel();

  const [formOpen, setFormOpen] = useState(false);
  const [kind, setKind] = useState<ChannelKind>('email');
  const [target, setTarget] = useState('');
  const [minLevel, setMinLevel] = useState<AlertEvent['level']>('warn');
  const [testResult, setTestResult] = useState<{ id: string; ok: boolean; detail: string } | null>(null);

  const items = channels.data ?? [];
  const mutationError = create.error ?? update.error ?? remove.error;

  function submit() {
    if (target.trim().length < 3) return;
    create.mutate(
      { kind, target: target.trim(), min_level: minLevel },
      {
        onSuccess: () => {
          setFormOpen(false);
          setTarget('');
        },
      },
    );
  }

  function runTest(id: string) {
    setTestResult(null);
    test.mutate(id, {
      onSuccess: (res) => setTestResult({ id, ...res }),
    });
  }

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <BellRing className="w-4 h-4 text-blue-400" /> 通知渠道
        </h2>
        <button type="button" className="btn-secondary" onClick={() => setFormOpen((v) => !v)}>
          <Plus className="w-4 h-4" /> 添加渠道
        </button>
      </div>
      <div className="card-body space-y-4">
        <p className="text-xs text-gray-600">
          站内告警之外的推送渠道（邮件 / Telegram / 钉钉 / 企业微信）。达到所选级别的告警才会推送；渠道密钥仅发送时解密，列表始终脱敏。
        </p>

        {formOpen && (
          <div className="rounded-md border border-gray-700/50 bg-surface-900 p-4 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <p className="text-xs text-gray-500 mb-1">渠道类型</p>
                <select
                  className="input-dark"
                  value={kind}
                  onChange={(e) => setKind(e.target.value as ChannelKind)}
                >
                  {(Object.keys(KIND_LABELS) as ChannelKind[]).map((k) => (
                    <option key={k} value={k}>{KIND_LABELS[k]}</option>
                  ))}
                </select>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">最低推送级别</p>
                <select
                  className="input-dark"
                  value={minLevel}
                  onChange={(e) => setMinLevel(e.target.value as AlertEvent['level'])}
                >
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>{LEVEL_LABELS[l]}</option>
                  ))}
                </select>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">目标（{KIND_TARGET_HINT[kind]}）</p>
                <input
                  className="input-dark font-mono"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder={KIND_TARGET_PLACEHOLDER[kind]}
                />
              </div>
            </div>
            {create.isError && (
              <p className="text-xs text-loss">{apiError(create.error)}</p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-primary"
                disabled={create.isPending || target.trim().length < 3}
                onClick={submit}
              >
                保存
              </button>
              <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>取消</button>
            </div>
          </div>
        )}

        {mutationError && (
          <div className="text-sm text-loss bg-loss/10 border border-loss/30 rounded-md px-3 py-2">
            {apiError(mutationError)}
          </div>
        )}

        {channels.isError && (
          <div className="text-sm text-loss">{apiError(channels.error)}</div>
        )}

        {items.length === 0 && !channels.isLoading ? (
          <p className="text-sm text-gray-600 py-4 text-center">尚未配置任何推送渠道。</p>
        ) : (
          <ul className="divide-y divide-gray-700/50">
            {items.map((ch) => (
              <li key={ch.id} className="py-3 flex items-center gap-3 flex-wrap">
                <span className="px-2 py-0.5 rounded border text-xs bg-surface-700 text-gray-300 border-gray-600 shrink-0">
                  {KIND_LABELS[ch.kind]}
                </span>
                <span className="text-sm font-mono text-gray-300 flex-1 min-w-40 truncate" title={ch.target_masked}>
                  {ch.target_masked}
                </span>
                {ch.verified_at && (
                  <span className="text-2xs text-profit inline-flex items-center gap-0.5">
                    <Check className="w-3 h-3" /> 已验证
                  </span>
                )}
                <select
                  className="input-dark w-32 text-xs py-1"
                  value={ch.min_level}
                  onChange={(e) =>
                    update.mutate({ id: ch.id, min_level: e.target.value as AlertEvent['level'] })
                  }
                >
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>{LEVEL_LABELS[l]}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className={`text-xs px-2 py-1 rounded border transition-colors ${
                    ch.enabled
                      ? 'text-profit border-profit/40 hover:bg-profit/10'
                      : 'text-gray-500 border-gray-600 hover:bg-surface-700'
                  }`}
                  disabled={update.isPending}
                  title={ch.enabled ? '点击停用' : '点击启用'}
                  onClick={() => update.mutate({ id: ch.id, enabled: !ch.enabled })}
                >
                  {ch.enabled ? '启用中' : '已停用'}
                </button>
                <button
                  type="button"
                  className="text-blue-400 hover:text-blue-300 text-xs disabled:opacity-50 inline-flex items-center gap-1"
                  disabled={test.isPending}
                  onClick={() => runTest(ch.id)}
                  title="发送一条测试消息"
                >
                  <Send className="w-3.5 h-3.5" /> 测试
                </button>
                <button
                  type="button"
                  className="text-loss hover:text-loss-light text-xs disabled:opacity-50"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm('删除该通知渠道？')) remove.mutate(ch.id);
                  }}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
                {testResult?.id === ch.id && (
                  <span
                    className={`w-full text-xs flex items-center gap-1 ${
                      testResult.ok ? 'text-profit' : 'text-loss'
                    }`}
                  >
                    {testResult.ok ? <Check className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
                    {testResult.detail}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
