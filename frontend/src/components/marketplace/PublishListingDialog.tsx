import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { usePublishListing } from '@/hooks/useMarketplace';
import { apiError } from '@/lib/api';
import { MARKET_DISCLAIMER } from '@/lib/marketplace';
import type { Strategy } from '@/types/api';

interface Props {
  strategy: Strategy | null;
  onClose: () => void;
}

/** 发布策略到市场 — 免责确认是硬闸（不勾选禁用提交，后端二次校验 422）。 */
export default function PublishListingDialog({ strategy, onClose }: Props) {
  const navigate = useNavigate();
  const publish = usePublishListing();
  const [acked, setAcked] = useState(false);

  useEffect(() => {
    setAcked(false);
    publish.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy?.id]);

  if (!strategy) return null;

  const submit = () => {
    if (!acked) return;
    publish.mutate(
      { strategy_id: strategy.id, disclaimer_ack: true },
      {
        onSuccess: (listing) => {
          onClose();
          navigate(`/marketplace/${listing.id}`);
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-lg">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">发布到策略市场</h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
          <div className="text-sm text-gray-300">
            将发布策略 <span className="text-white font-medium">{strategy.name}</span>
            （品种 {strategy.symbol_ids.join(', ') || '—'} / {strategy.timeframe}）。
            发布时冻结当前源码快照，之后的修改不会同步到市场条目。
          </div>
          <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-xs text-gray-300">
            {MARKET_DISCLAIMER}
          </div>
          <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5 accent-blue-500"
              checked={acked}
              onChange={(e) => setAcked(e.target.checked)}
            />
            <span>我已阅读并同意上述声明，确认发布。</span>
          </label>

          {publish.isError && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              发布失败：{apiError(publish.error)}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              取消
            </button>
            <button type="button" className="btn-primary" disabled={!acked || publish.isPending} onClick={submit}>
              {publish.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              确认发布
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
