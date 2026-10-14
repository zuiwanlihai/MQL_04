import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Download,
  FileSearch,
  ShieldCheck,
  ShieldX,
} from 'lucide-react';
import { useAuditList, useExportAudit, useVerifyAuditChain } from '@/hooks/useAudit';
import { apiError } from '@/lib/api';
import { relativeTime } from '@/lib/market';
import { useAuthStore } from '@/store/useAuthStore';
import type { AuditVerifyResult } from '@/types/api';

const RESULT_STYLES: Record<string, string> = {
  success: 'bg-profit/15 text-profit border-profit/40',
  failure: 'bg-loss/15 text-loss border-loss/40',
  blocked: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
};

const RESULT_LABELS: Record<string, string> = {
  success: '成功',
  failure: '失败',
  blocked: '拦截',
};

const ACTION_LABELS: Record<string, string> = {
  login: '登录',
  logout: '登出',
  account_bind: '账户绑定',
  account_unbind: '账户解绑',
  strategy_create: '策略创建',
  strategy_update: '策略编辑',
  version_freeze: '版本冻结',
  backtest_submit: '回测提交',
  paper_start: '模拟启动',
  paper_reset: '模拟重置',
  admission_pass: '准入放行',
  live_deploy: '实盘部署',
  live_pause: '实盘暂停',
  live_resume: '实盘恢复',
  live_stop: '实盘停止',
  live_reset: '实盘重置',
  kill_switch: '一键熔断',
  risk_trigger: '风控触发',
  demote: '自动降级',
  re_admit: '复核恢复',
  reconcile_mismatch: '对账不一致',
  reconcile_unfreeze: '对账解冻',
  rule_change: '规则变更',
  flag_change: '权限开关变更',
  sandbox_violation: '沙箱越界',
  audit_verify: '审计链校验',
  audit_export: '审计导出',
};

// 校验结果分三档：分叉是并发写入的旧缺陷留下的痕迹（内容完好），
// 和篡改共用一个红色「失败」的话，真正的篡改会被历史遗留磨掉敏感度。
const VERDICT_STYLES = {
  ok: { card: 'border-profit/40 bg-profit/5', text: 'text-profit' },
  fork: { card: 'border-yellow-500/40 bg-yellow-500/10', text: 'text-yellow-400' },
  critical: { card: 'border-loss/40 bg-loss/10', text: 'text-loss' },
} as const;

function verdictText(v: AuditVerifyResult): string {
  if (v.verdict === 'ok') {
    return `审计链完好（共 ${v.total} 条，校验于 ${relativeTime(v.checked_at)}）`;
  }
  if (v.verdict === 'fork') {
    return `审计链有 ${v.findings_total} 处分叉（首个在第 ${v.broken_index + 1} 条，共 ${v.total} 条）：内容未被改动，来自并发写入的旧缺陷，已记入告警`;
  }
  return `审计链校验失败：${v.findings_total} 处异常，首个在第 ${v.broken_index + 1} 条（共 ${v.total} 条），已产生严重告警`;
}

export default function AuditPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';

  const [page, setPage] = useState(1);
  const [action, setAction] = useState('');
  const [result, setResult] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');

  const list = useAuditList({ page, pageSize: 50, action, result, start, end });
  const verify = useVerifyAuditChain();
  const exportCsv = useExportAudit();

  const data = list.data;

  if (!isAdmin) {
    return (
      <div className="card">
        <div className="card-body text-center py-16">
          <ShieldX className="w-10 h-10 text-gray-600 mx-auto mb-3" />
          <h1 className="text-lg font-semibold text-gray-300">审计日志</h1>
          <p className="text-sm text-gray-500 mt-2">审计取证功能仅管理员可用，请联系平台管理员。</p>
        </div>
      </div>
    );
  }

  function doExport(format: 'csv' | 'json') {
    exportCsv.mutate({ format, action, result, start, end });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <FileSearch className="w-5 h-5 text-blue-400" /> 风控审计日志
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            资金 / 权限 / 系统行为变更的唯一取证依据 · 哈希链防篡改（只追加，无修改删除接口）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={verify.isPending}
            onClick={() => verify.mutate()}
            title="重算全链 sha256 并校验 prev_hash 衔接"
          >
            <ShieldCheck className="w-4 h-4" /> 链完整性校验
          </button>
          <button type="button" className="btn-ghost" disabled={exportCsv.isPending} onClick={() => doExport('csv')}>
            <Download className="w-4 h-4" /> CSV
          </button>
          <button type="button" className="btn-ghost" disabled={exportCsv.isPending} onClick={() => doExport('json')}>
            <Download className="w-4 h-4" /> JSON
          </button>
        </div>
      </div>

      {/* 校验结果徽章 */}
      {verify.data && (
        <div className={`card ${VERDICT_STYLES[verify.data.verdict].card}`}>
          <div className="card-body flex items-center gap-3 text-sm">
            {verify.data.verdict === 'ok' ? (
              <ShieldCheck className="w-5 h-5 text-profit shrink-0" />
            ) : verify.data.verdict === 'fork' ? (
              <AlertTriangle className="w-5 h-5 text-yellow-400 shrink-0" />
            ) : (
              <ShieldX className="w-5 h-5 text-loss shrink-0" />
            )}
            <span className={VERDICT_STYLES[verify.data.verdict].text}>
              {verdictText(verify.data)}
            </span>
          </div>
          {verify.data.reason && verify.data.verdict !== 'ok' && (
            <div className="card-body pt-0 text-xs text-gray-400">
              {verify.data.reason}
            </div>
          )}
        </div>
      )}
      {(verify.isError || exportCsv.isError || list.isError) && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">
            {apiError(verify.error ?? exportCsv.error ?? list.error)}
          </div>
        </div>
      )}

      {/* 过滤栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          className="input-dark w-44"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部动作</option>
          {Object.entries(ACTION_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}（{k}）
            </option>
          ))}
        </select>
        <select
          className="input-dark w-32"
          value={result}
          onChange={(e) => {
            setResult(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部结果</option>
          {Object.entries(RESULT_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <input
          type="datetime-local"
          className="input-dark w-48"
          value={start}
          onChange={(e) => {
            setStart(e.target.value);
            setPage(1);
          }}
        />
        <span className="text-xs text-gray-600">至</span>
        <input
          type="datetime-local"
          className="input-dark w-48"
          value={end}
          onChange={(e) => {
            setEnd(e.target.value);
            setPage(1);
          }}
        />
        {data && (
          <span className="text-xs text-gray-500">共 {data.total} 条 · 第 {data.page}/{data.total_pages} 页</span>
        )}
      </div>

      {/* 审计表 */}
      <div className="card overflow-hidden">
        <table className="table-dark">
          <thead>
            <tr>
              <th className="w-36">时间</th>
              <th className="w-20">主体</th>
              <th>动作</th>
              <th>目标</th>
              <th className="w-16">结果</th>
              <th className="w-20">风控判定</th>
              <th className="w-40">参数摘要</th>
              <th className="w-20">哈希</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((r) => (
              <tr key={r.id}>
                <td className="text-xs text-gray-400 whitespace-nowrap" title={r.created_at}>
                  {new Date(r.created_at).toLocaleString('zh-CN', { hour12: false })}
                </td>
                <td className="text-xs text-gray-400">
                  {r.actor_type === 'user' ? '用户' : r.actor_type === 'system' ? '系统' : '策略'}
                  <p className="text-gray-600 truncate max-w-28" title={r.actor_label}>{r.actor_label || '—'}</p>
                </td>
                <td className="text-sm text-gray-200">
                  {ACTION_LABELS[r.action] ?? r.action}
                  <p className="text-xs text-gray-600 font-mono">{r.action}</p>
                </td>
                <td className="text-xs text-gray-400">
                  {r.target_type || '—'}
                  {r.target_type === 'live_run' && r.target_id ? (
                    <Link to={`/trading/live/${r.target_id}`} className="text-blue-400 hover:text-blue-300 ml-1">
                      跳转
                    </Link>
                  ) : (
                    <p className="text-gray-600 font-mono truncate max-w-40" title={r.target_id}>{r.target_id || '—'}</p>
                  )}
                </td>
                <td>
                  <span className={`px-1.5 py-0.5 rounded border text-2xs whitespace-nowrap ${RESULT_STYLES[r.result] ?? 'bg-surface-700 text-gray-300 border-gray-600'}`}>
                    {RESULT_LABELS[r.result] ?? r.result}
                  </span>
                </td>
                <td className="text-xs text-gray-400">{r.risk_decision || '—'}</td>
                <td>
                  <p className="text-xs text-gray-500 font-mono truncate max-w-40" title={JSON.stringify(r.params)}>
                    {JSON.stringify(r.params)}
                  </p>
                </td>
                <td className="text-xs font-mono text-gray-600" title={`${r.hash}\nprev: ${r.prev_hash}`}>
                  {r.hash.slice(0, 8)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(data?.items ?? []).length === 0 && !list.isLoading && (
          <div className="card-body text-center text-gray-500 py-10">没有符合条件的审计记录。</div>
        )}
      </div>

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">{page} / {data.total_pages}</span>
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}
