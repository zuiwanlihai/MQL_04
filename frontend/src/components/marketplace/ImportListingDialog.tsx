import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useImportListing } from '@/hooks/useMarketplace';
import { apiError } from '@/lib/api';
import type { MarketListing } from '@/types/api';

interface Props {
  listing: MarketListing | null;
  onClose: () => void;
}

/** 导入市场条目为本人草稿策略 — 409 同名冲突时改名重试。 */
export default function ImportListingDialog({ listing, onClose }: Props) {
  const navigate = useNavigate();
  const importMut = useImportListing();
  const [name, setName] = useState('');

  useEffect(() => {
    setName(listing?.strategy_name ?? '');
    importMut.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing?.id]);

  if (!listing) return null;

  const submit = () => {
    if (!name.trim()) return;
    importMut.mutate(
      { id: listing.id, name: name.trim() },
      {
        onSuccess: (s) => {
          onClose();
          navigate(`/strategies/${s.id}/edit`);
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-md">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">导入到我的策略</h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
          <div className="text-sm text-gray-300">
            将把市场条目 <span className="text-white font-medium">{listing.strategy_name}</span>
            （作者 {listing.author_username || '未知'}）的冻结源码复制为你的新草稿策略。
          </div>
          <label className="block">
            <span className="text-xs text-gray-400">新策略名称 *</span>
            <input
              className="input-dark mt-1"
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </label>

          {importMut.isError && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              导入失败：{apiError(importMut.error)}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!name.trim() || importMut.isPending}
              onClick={submit}
            >
              {importMut.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              导入
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
