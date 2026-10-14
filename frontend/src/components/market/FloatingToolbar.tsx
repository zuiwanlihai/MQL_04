import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { GripHorizontal } from 'lucide-react';

interface Props {
  /** 位置持久化键（每个图表独立记忆） */
  storageKey: string;
  defaultX?: number;
  defaultY?: number;
  className?: string;
  children: ReactNode;
}

/**
 * 图表浮动工具条：悬浮于图表之上（absolute），通过左侧手柄拖动调节位置，
 * 位置按 storageKey 存 localStorage，刷新/切品种后保持。
 */
export default function FloatingToolbar({
  storageKey,
  defaultX = 8,
  defaultY = 8,
  className = '',
  children,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const lsKey = `ktoolbar:${storageKey}`;
  const [pos, setPos] = useState<{ x: number; y: number }>(() => {
    try {
      const raw = localStorage.getItem(lsKey);
      if (raw) {
        const p = JSON.parse(raw) as { x: number; y: number };
        if (Number.isFinite(p.x) && Number.isFinite(p.y)) return p;
      }
    } catch {
      /* ignore */
    }
    return { x: defaultX, y: defaultY };
  });

  // 同步跟踪最新位置（不依赖 React 渲染提交时机），pointerup 时据此持久化
  const posRef = useRef(pos);

  // 挂载/切换图表时钳制回父容器可视范围（窗口缩小后不丢）
  useLayoutEffect(() => {
    const el = ref.current;
    const parent = el?.offsetParent as HTMLElement | null;
    if (!el || !parent) return;
    setPos((p) => {
      const np = {
        x: Math.min(Math.max(0, p.x), Math.max(0, parent.clientWidth - el.offsetWidth)),
        y: Math.min(Math.max(0, p.y), Math.max(0, parent.clientHeight - el.offsetHeight)),
      };
      posRef.current = np;
      return np;
    });
  }, [storageKey]);

  function onGripDown(e: React.PointerEvent<HTMLButtonElement>) {
    const el = ref.current;
    const parent = el?.offsetParent as HTMLElement | null;
    if (!el || !parent) return;
    e.preventDefault();
    const start = { px: e.clientX, py: e.clientY, ox: el.offsetLeft, oy: el.offsetTop };
    const onMove = (ev: PointerEvent) => {
      const nx = Math.min(
        Math.max(0, start.ox + ev.clientX - start.px),
        Math.max(0, parent.clientWidth - el.offsetWidth),
      );
      const ny = Math.min(
        Math.max(0, start.oy + ev.clientY - start.py),
        Math.max(0, parent.clientHeight - el.offsetHeight),
      );
      posRef.current = { x: nx, y: ny };
      setPos({ x: nx, y: ny });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      try {
        localStorage.setItem(lsKey, JSON.stringify(posRef.current));
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  return (
    <div
      ref={ref}
      className={`absolute z-20 flex items-center gap-1 flex-wrap text-xs bg-surface-900/90 backdrop-blur-sm border border-gray-700/60 rounded-lg p-1 shadow-lg ${className}`}
      style={{ left: pos.x, top: pos.y }}
    >
      <button
        type="button"
        onPointerDown={onGripDown}
        title="按住拖动调整位置"
        className="px-1 py-0.5 cursor-grab active:cursor-grabbing text-gray-500 hover:text-gray-300 flex-shrink-0 touch-none"
      >
        <GripHorizontal className="w-3.5 h-3.5" />
      </button>
      {children}
    </div>
  );
}
