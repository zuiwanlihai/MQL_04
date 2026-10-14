import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Bell, CheckCheck, ChevronLeft, ChevronRight } from 'lucide-react';
import NotifyChannelsCard from '@/components/alerts/NotifyChannelsCard';
import AlertRulesCard from '@/components/alerts/AlertRulesCard';
import { useAccounts } from '@/hooks/useAccounts';
import { useAlertsPaged, useLiveRuns, useMarkAlertRead, useMarkAllAlertsRead } from '@/hooks/useLive';
import { apiError } from '@/lib/api';
import { relativeTime } from '@/lib/market';
import { useAuthStore } from '@/store/useAuthStore';
import type { AlertEvent } from '@/types/api';

const LEVEL_META: Record<AlertEvent['level'], { label: string; cls: string; dot: string }> = {
  info: { label: '提示', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/40', dot: 'bg-blue-400' },
  warn: { label: '警告', cls: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40', dot: 'bg-yellow-400' },
  error: { label: '错误', cls: 'bg-orange-500/15 text-orange-400 border-orange-500/40', dot: 'bg-orange-500' },
  critical: { label: '严重', cls: 'bg-loss/15 text-loss border-loss/40', dot: 'bg-loss' },
};

const SOURCE_LABELS: Record<string, string> = {
  reconcile: '对账',
  risk_vacuum: '风险真空',
  demote: '自动降级',
  re_admit: '复核恢复',
  kill_switch: '熔断',
  risk: '风控',
  runner: '策略运行',
  audit_chain: '审计链',
  alert_rule: '自定义规则',
  gateway: '网关',
};

export default function AlertsPage() {
  const [page, setPage] = useState(1);
  const [unreadOnly, setUnreadOnly] = useState(false);

  const list = useAlertsPaged({ page, unreadOnly });
  const markRead = useMarkAlertRead();
  const markAll = useMarkAllAlertsRead();
  const data = list.data;

  // 告警列表是平台级的（运维信号藏起来比露出来更危险），但**标记已读**只对自己
  // 的告警生效：is_read 是共享的一个位，标了就是替全体标了。所以按钮要按归属
  // 显隐，别让用户点出一句「告警不存在」。平台级告警（网关异常等）归管理员。
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const accounts = useAccounts();
  const runs = useLiveRuns({ page: 1, pageSize: 100 });
  const myAccountIds = new Set((accounts.data?.items ?? []).map((a) => a.id));
  const myRunIds = new Set((runs.data?.items ?? []).map((r) => r.id));
  const canMarkRead = (a: AlertEvent) =>
    isAdmin
    || (a.account_id != null && myAccountIds.has(a.account_id))
    || (a.run_id != null && myRunIds.has(a.run_id));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Bell className="w-5 h-5 text-yellow-400" /> 告警中心
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            成交 / 风控触发 / 网关断线 / 审计链校验 / 自定义规则 — 站内信 + 邮件 / Telegram / 钉钉 / 企业微信
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          disabled={markAll.isPending || (data?.unread ?? 0) === 0}
          title={isAdmin ? '标记全平台告警为已读' : '只标记与你账户 / 任务相关的告警；平台级告警由管理员处理'}
          onClick={() => markAll.mutate()}
        >
          <CheckCheck className="w-4 h-4" /> 全部标记已读
        </button>
      </div>

      {markAll.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(markAll.error)}</div>
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            className="accent-blue-500"
            checked={unreadOnly}
            onChange={(e) => {
              setUnreadOnly(e.target.checked);
              setPage(1);
            }}
          />
          仅看未读
        </label>
        {data && (
          <span className="text-xs text-gray-500">
            共 {data.total} 条 · 未读 {data.unread}
          </span>
        )}
      </div>

      {list.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(list.error)}</div>
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="table-dark">
          <thead>
            <tr>
              <th className="w-16">级别</th>
              <th className="w-24">来源</th>
              <th>标题 / 内容</th>
              <th className="w-32">时间</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((a) => {
              const meta = LEVEL_META[a.level] ?? LEVEL_META.warn;
              return (
                <tr key={a.id} className={a.is_read ? 'opacity-60' : ''}>
                  <td>
                    <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${meta.cls}`}>
                      {meta.label}
                    </span>
                  </td>
                  <td className="text-xs text-gray-400">{SOURCE_LABELS[a.source] ?? a.source}</td>
                  <td>
                    <p className="text-sm text-gray-200 flex items-center gap-2">
                      {!a.is_read && <span className={`w-2 h-2 rounded-full ${meta.dot} shrink-0`} />}
                      {a.title}
                    </p>
                    {a.message && (
                      <p className="text-xs text-gray-500 mt-1 break-all line-clamp-2">{a.message}</p>
                    )}
                  </td>
                  <td className="text-xs text-gray-500 whitespace-nowrap" title={a.created_at}>
                    {relativeTime(a.created_at)}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {a.run_id && (
                      <Link to={`/trading/live/${a.run_id}`} className="text-blue-400 hover:text-blue-300 text-xs mr-3">
                        任务
                      </Link>
                    )}
                    {!a.is_read && canMarkRead(a) && (
                      <button
                        type="button"
                        className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-50"
                        disabled={markRead.isPending}
                        onClick={() => markRead.mutate(a.id)}
                      >
                        标记已读
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {(data?.items ?? []).length === 0 && (
          <div className="card-body text-center text-gray-500 py-10">
            {unreadOnly ? '没有未读告警。' : '暂无告警事件。'}
          </div>
        )}
      </div>

      {data && data.total > 50 && (
        <div className="flex items-center justify-center gap-3">
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">{page} / {Math.ceil(data.total / 50)}</span>
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page >= Math.ceil(data.total / 50)} onClick={() => setPage((p) => p + 1)}>
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      <AlertRulesCard />

      <NotifyChannelsCard />
    </div>
  );
}
