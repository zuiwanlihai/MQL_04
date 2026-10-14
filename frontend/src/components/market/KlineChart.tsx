import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColorType,
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Logical,
  type UTCTimestamp,
} from 'lightweight-charts';
import { Eraser, GripVertical, Minus, TrendingUp } from 'lucide-react';
import FloatingToolbar from './FloatingToolbar';
import { upDownColors, useThemeStore } from '@/store/useThemeStore';
import { boll, kdj, macd, rsi, sma, type IndicatorPoint } from '@/lib/indicators';
import type { BrokerOrder, BrokerPosition, PriceBar } from '@/types/api';

interface Props {
  bars: PriceBar[];
  /** WS 聚合的实时尾根蜡烛 — 到达即 series.update()，不触发全量 setData/fitContent */
  liveBar?: PriceBar;
  /** 渲染错误态（503 等）— 非空时显示中文错误而非空图 */
  error?: string | null;
  loading?: boolean;
  /** 画线持久化键（如 "XAUUSD:H1"），缺省时画线仅存内存 */
  storageKey?: string;
  /** 价格小数位（画线价格标签用） */
  digits?: number;
  /** 当前账户在当前品种的持仓 — 左边缘→开仓时间点画线（盈利蓝/亏损红，标签居中） */
  positions?: BrokerPosition[];
  /** 当前账户在当前品种的挂单 — 画委托价虚线 */
  orders?: BrokerOrder[];
}

type OverlayKey = 'ma5' | 'ma10' | 'ma20' | 'ma60' | 'bollU' | 'bollM' | 'bollL';
type SubIndicator = 'none' | 'MACD' | 'RSI' | 'KDJ';
type DrawTool = 'none' | 'trend' | 'hline';

interface Drawing {
  id: string;
  type: 'trend' | 'hline';
  t1: number; // bar unix time（秒）
  p1: number;
  t2: number | null;
  p2: number | null;
  color: string;
}

interface HistPoint {
  time: UTCTimestamp;
  value: number;
  color: string;
}

type SubData =
  | { kind: 'MACD'; dif: IndicatorPoint[]; dea: IndicatorPoint[]; hist: HistPoint[]; offset: number }
  | { kind: 'RSI'; rsi: IndicatorPoint[]; offset: number }
  | { kind: 'KDJ'; k: IndicatorPoint[]; d: IndicatorPoint[]; j: IndicatorPoint[]; offset: number };

const OVERLAY_COLORS: Record<OverlayKey, string> = {
  ma5: '#e5e7eb',
  ma10: '#eab308',
  ma20: '#c084fc',
  ma60: '#4ade80',
  bollU: '#38bdf8',
  bollM: '#94a3b8',
  bollL: '#38bdf8',
};
const OVERLAY_LABELS: Record<OverlayKey, string> = {
  ma5: 'MA5',
  ma10: 'MA10',
  ma20: 'MA20',
  ma60: 'MA60',
  bollU: 'BOLL上',
  bollM: 'BOLL中',
  bollL: 'BOLL下',
};
const SUB_LABELS: Record<SubIndicator, string> = {
  none: '',
  MACD: 'MACD(12,26,9)',
  RSI: 'RSI(14)',
  KDJ: 'KDJ(9,3,3)',
};
const DRAW_PALETTE = ['#f59e0b', '#38bdf8', '#a78bfa', '#f472b6', '#34d399'];
const DRAW_KEY = 'mt5quant.drawings.v1';

// 挂单类型 → 图上线条标签（0/1 为市价单，不会出现在挂单列表）
const PENDING_LABELS: Record<number, string> = {
  2: 'BuyLimit', 3: 'SellLimit', 4: 'BuyStop', 5: 'SellStop',
};

// ── 右边距（最后一根 K 线与右边缘的空隙，单位：根）────────────
const RIGHT_MARGIN_DEFAULT = 5;
const RIGHT_MARGIN_MAX = 200;

/** 把「最后一根 bar 右侧留 m 根空隙」应用到可见区间（保持缩放不变，仅平移）。 */
function applyRightMargin(
  ts: ReturnType<IChartApi['timeScale']>,
  lastIndex: number,
  margin: number,
) {
  const range = ts.getVisibleLogicalRange();
  ts.applyOptions({ rightOffset: margin });
  if (!range || range.to <= range.from) return;
  const span = range.to - range.from;
  ts.setVisibleLogicalRange({ from: lastIndex + margin - span, to: lastIndex + margin });
}

const TIME_FMT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function loadDrawingMap(): Record<string, Drawing[]> {
  try {
    return JSON.parse(localStorage.getItem(DRAW_KEY) ?? '{}') as Record<string, Drawing[]>;
  } catch {
    return {};
  }
}

function persistDrawings(key: string, list: Drawing[]) {
  const map = loadDrawingMap();
  if (list.length > 0) map[key] = list;
  else delete map[key];
  try {
    localStorage.setItem(DRAW_KEY, JSON.stringify(map));
  } catch {
    // 存储满/禁用：画线仅存内存
  }
}

/** 二分查找 bar 下标；未命中时返回最近邻。 */
function findBarIndex(bars: PriceBar[], time: number): number {
  let lo = 0;
  let hi = bars.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time === time) return mid;
    if (bars[mid].time < time) lo = mid + 1;
    else hi = mid - 1;
  }
  return Math.min(Math.max(lo, 0), bars.length - 1);
}

/** 二分查找时间 t 所在的 K 线下标（最后一根 time ≤ t；早于首根时落在 0）。 */
function findBarIndexFloor(bars: PriceBar[], time: number): number {
  let lo = 0;
  let hi = bars.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= time) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/** 点到线段的最短距离。 */
function segDist(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - x1, py - y1);
  const t = Math.min(Math.max(((px - x1) * dx + (py - y1) * dy) / lenSq, 0), 1);
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function newId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

export default function KlineChart({
  bars,
  liveBar,
  error,
  loading,
  storageKey,
  digits = 5,
  positions,
  orders,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const subContainerRef = useRef<HTMLDivElement>(null);
  const subChartRef = useRef<IChartApi | null>(null);
  const subSeriesRefs = useRef<Record<string, ISeriesApi<'Line' | 'Histogram'>>>({});
  const overlayRefs = useRef<Partial<Record<OverlayKey, ISeriesApi<'Line'>>>>({});

  const barsRef = useRef<PriceBar[]>(bars);
  const redrawRef = useRef<() => void>(() => {});
  const drawingsRef = useRef<Drawing[]>([]);
  const selectedRef = useRef<string | null>(null);
  const toolRef = useRef<DrawTool>('none');
  const pendingRef = useRef<{ t1: number; p1: number; x1: number; y1: number } | null>(null);
  const previewRef = useRef<{ x: number; y: number } | null>(null);
  const mouseDownRef = useRef<{ x: number; y: number } | null>(null);
  const storageKeyRef = useRef<string | undefined>(storageKey);
  const subDataRef = useRef<SubData | null>(null);
  const tradeLinesRef = useRef<IPriceLine[]>([]);
  const positionsRef = useRef<BrokerPosition[]>([]);
  const ordersRef = useRef<BrokerOrder[]>([]);
  const prevBarsLenRef = useRef(0);

  const redUpGreenDown = useThemeStore((s) => s.redUpGreenDown);

  const [show, setShow] = useState<Record<'ma5' | 'ma10' | 'ma20' | 'ma60' | 'boll', boolean>>({
    ma5: false,
    ma10: true,
    ma20: true,
    ma60: false,
    boll: false,
  });
  const [subIndicator, setSubIndicator] = useState<SubIndicator>('MACD');
  const [tool, setTool] = useState<DrawTool>('none');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  // 右边距：最后一根 K 线与图表右边缘的空隙（根），可拖调节块调整。
  // 切换品种/周期一律重置为默认 5 根（不读跨会话记忆）。
  const [rightMargin, setRightMargin] = useState<number>(RIGHT_MARGIN_DEFAULT);
  const [draggingMargin, setDraggingMargin] = useState(false);
  const rightMarginRef = useRef(rightMargin);

  // 切换图表（品种/周期）→ 重置为默认右边距（数据到达后由 historyExtended 分支定位）
  useEffect(() => {
    rightMarginRef.current = RIGHT_MARGIN_DEFAULT;
    setRightMargin(RIGHT_MARGIN_DEFAULT);
  }, [storageKey]);

  toolRef.current = tool;
  storageKeyRef.current = storageKey;

  // ── 指标计算（纯函数，memo 化）──────────────────────────────

  const overlayData = useMemo(() => {
    const out: Partial<Record<OverlayKey, IndicatorPoint[]>> = {};
    if (bars.length === 0) return out;
    if (show.ma5) out.ma5 = sma(bars, 5);
    if (show.ma10) out.ma10 = sma(bars, 10);
    if (show.ma20) out.ma20 = sma(bars, 20);
    if (show.ma60) out.ma60 = sma(bars, 60);
    if (show.boll) {
      const b = boll(bars, 20, 2);
      out.bollU = b.upper;
      out.bollM = b.mid;
      out.bollL = b.lower;
    }
    return out;
  }, [bars, show]);

  const subData = useMemo<SubData | null>(() => {
    if (subIndicator === 'none' || bars.length < 2) return null;
    const toPts = (arr: IndicatorPoint[]) =>
      arr.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
    switch (subIndicator) {
      case 'MACD': {
        const m = macd(bars);
        if (m.dif.length === 0) return null;
        const { up, down } = upDownColors(redUpGreenDown);
        const hist: HistPoint[] = m.hist.map((p) => ({
          time: p.time as UTCTimestamp,
          value: p.value,
          color: p.value >= 0 ? hexToRgba(up, 0.75) : hexToRgba(down, 0.75),
        }));
        return { kind: 'MACD', dif: toPts(m.dif), dea: toPts(m.dea), hist, offset: bars.length - m.dif.length };
      }
      case 'RSI': {
        const r = rsi(bars, 14);
        if (r.length === 0) return null;
        return { kind: 'RSI', rsi: toPts(r), offset: bars.length - r.length };
      }
      case 'KDJ': {
        const k3 = kdj(bars);
        if (k3.k.length === 0) return null;
        return {
          kind: 'KDJ',
          k: toPts(k3.k),
          d: toPts(k3.d),
          j: toPts(k3.j),
          offset: bars.length - k3.k.length,
        };
      }
    }
  }, [bars, subIndicator, redUpGreenDown]);
  subDataRef.current = subData;

  // ── 画线：坐标换算与重绘（命令式，走 ref 不走 state）─────────

  const xToTime = (x: number): number | null => {
    const chart = chartRef.current;
    if (!chart || barsRef.current.length === 0) return null;
    const logical = chart.timeScale().coordinateToLogical(x);
    if (logical === null) return null;
    const idx = Math.min(Math.max(Math.round(logical), 0), barsRef.current.length - 1);
    return barsRef.current[idx].time;
  };

  const timeToX = (time: number): number | null => {
    const chart = chartRef.current;
    if (!chart || barsRef.current.length === 0) return null;
    return chart.timeScale().logicalToCoordinate(findBarIndex(barsRef.current, time) as Logical);
  };

  // 开仓时间 → 所属 K 线的 x（floor：包含该时间的那根，而非最近邻）
  const openTimeToX = (time: number): number | null => {
    const chart = chartRef.current;
    if (!chart || barsRef.current.length === 0) return null;
    const idx = findBarIndexFloor(barsRef.current, time);
    return chart.timeScale().logicalToCoordinate(idx as Logical);
  };

  const eventPos = (e: MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    return { x, y, time: xToTime(x), price: seriesRef.current?.coordinateToPrice(y) ?? null };
  };

  const hitTest = (x: number, y: number): string | null => {
    const series = seriesRef.current;
    if (!series) return null;
    const list = drawingsRef.current;
    for (let i = list.length - 1; i >= 0; i--) {
      const d = list[i];
      if (d.type === 'hline') {
        const ly = series.priceToCoordinate(d.p1);
        if (ly !== null && Math.abs(y - ly) <= 6) return d.id;
      } else if (d.t2 !== null && d.p2 !== null) {
        const x1 = timeToX(d.t1);
        const y1 = series.priceToCoordinate(d.p1);
        const x2 = timeToX(d.t2);
        const y2 = series.priceToCoordinate(d.p2);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null && segDist(x, y, x1, y1, x2, y2) <= 6) {
          return d.id;
        }
      }
    }
    return null;
  };

  const redraw = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    const series = seriesRef.current;
    if (!canvas || !ctx || !series) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    ctx.clearRect(0, 0, w, h);

    const drawDot = (x: number, y: number, big: boolean) => {
      ctx.beginPath();
      ctx.arc(x, y, big ? 4 : 2.5, 0, Math.PI * 2);
      ctx.fillStyle = '#111827';
      ctx.fill();
      ctx.stroke();
    };

    for (const d of drawingsRef.current) {
      const selected = d.id === selectedRef.current;
      ctx.strokeStyle = d.color;
      ctx.lineWidth = selected ? 2.5 : 1.5;
      ctx.shadowBlur = selected ? 4 : 0;
      ctx.shadowColor = 'rgba(255,255,255,0.8)';
      ctx.setLineDash([]);

      if (d.type === 'hline') {
        const y = series.priceToCoordinate(d.p1);
        if (y === null) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        // 右侧价格标签
        ctx.shadowBlur = 0;
        ctx.font = '11px ui-monospace, SFMono-Regular, monospace';
        const label = d.p1.toFixed(digits);
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = 'rgba(17,24,39,0.85)';
        ctx.fillRect(w - tw - 18, y - 9, tw + 10, 18);
        ctx.fillStyle = d.color;
        ctx.fillText(label, w - tw - 13, y + 4);
      } else if (d.t2 !== null && d.p2 !== null) {
        const x1 = timeToX(d.t1);
        const y1 = series.priceToCoordinate(d.p1);
        const x2 = timeToX(d.t2);
        const y2 = series.priceToCoordinate(d.p2);
        if (x1 === null || y1 === null || x2 === null || y2 === null) continue;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.shadowBlur = 0;
        drawDot(x1, y1, selected);
        drawDot(x2, y2, selected);
      }
    }
    ctx.shadowBlur = 0;

    // 持仓开仓线：左边缘 → 开仓 K 线处；标签在线段中间（买在线上方、卖在线下方）；
    // 盈利蓝、亏损红。开仓 K 线不在可视区内（含已滚出左边缘）→ 不画超出的部分。
    for (const p of positionsRef.current) {
      const y = series.priceToCoordinate(p.price_open);
      if (y === null || y < -20 || y > h + 20) continue;
      const xOpen = openTimeToX(p.time);
      if (xOpen === null || xOpen <= 0) continue;
      const color = p.profit >= 0 ? '#3b82f6' : '#ef4444';
      const xEnd = Math.min(xOpen, w - 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(Math.max(1, xEnd), y);
      ctx.stroke();

      const label =
        `${p.type === 'buy' ? '买' : '卖'} ${p.volume}手 @${p.price_open.toFixed(digits)}` +
        ` · ${p.profit >= 0 ? '+' : ''}${p.profit.toFixed(2)}`;
      ctx.font = '11px ui-monospace, SFMono-Regular, monospace';
      const tw = ctx.measureText(label).width;
      const cx = Math.max(tw / 2 + 4, xEnd / 2);
      const buy = p.type === 'buy';
      const textY = buy ? y - 5 : y + 5;
      ctx.fillStyle = 'rgba(17,24,39,0.85)';
      ctx.fillRect(cx - tw / 2 - 4, buy ? textY - 14 : textY - 1, tw + 8, 15);
      ctx.fillStyle = color;
      ctx.textBaseline = buy ? 'bottom' : 'top';
      ctx.fillText(label, cx - tw / 2, textY);
      ctx.textBaseline = 'alphabetic';
    }

    // 挂单标签：黄色文字画在图表左边缘（priceLine 的 title 只能贴右轴，故 here）
    ctx.font = '11px ui-monospace, SFMono-Regular, monospace';
    for (const o of ordersRef.current) {
      const y = series.priceToCoordinate(o.price_open);
      if (y === null || y < -14 || y > h + 14) continue;
      const label = `${PENDING_LABELS[o.type] ?? `type ${o.type}`} ${o.volume}手 @${o.price_open.toFixed(digits)}`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(17,24,39,0.85)';
      ctx.fillRect(2, y - 8, tw + 10, 16);
      ctx.fillStyle = '#eab308';
      ctx.fillText(label, 7, y + 4);
    }

    // 绘制中的预览
    const pending = pendingRef.current;
    if (pending) {
      ctx.strokeStyle = '#e5e7eb';
      ctx.lineWidth = 1.5;
      ctx.fillStyle = '#e5e7eb';
      drawDot(pending.x1, pending.y1, false);
      const pv = previewRef.current;
      if (pv) {
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(pending.x1, pending.y1);
        ctx.lineTo(pv.x, pv.y);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
    updatePriceTag();
  };

  useEffect(() => {
    redrawRef.current = redraw;
  });

  // ── 主图初始化（一次性）────────────────────────────────────

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(55, 65, 81, 0.4)' },
        horzLines: { color: 'rgba(55, 65, 81, 0.4)' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        // v4 无 timezone 选项 → 用 formatter 固定上海时区
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000);
          return TIME_FMT.format(d).replace(/^\d{4}\/\d{2}\/\d{2} /, '');
        },
      },
      localization: {
        timeFormatter: (time: number) => TIME_FMT.format(new Date(time * 1000)),
      },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addCandlestickSeries({
      // 当前价横线 + 右轴价格标签（颜色自动跟最后一根蜡烛涨跌色）
      priceLineVisible: true,
      lastValueVisible: true,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // 画线画布尺寸随容器（DPR 适配）
    const sizeCanvas = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      // canvas 是替换元素：inset-0 不会拉伸它，CSS 尺寸必须显式设为容器尺寸，
      // 否则按属性尺寸（rect×dpr）显示，与 K 线 pane 相差 dpr 倍 → 线的位置偏移
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    // StrictMode 双跑：cleanup 必须销毁图表并断开 observer
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
      sizeCanvas();
      redrawRef.current();
    });
    observer.observe(container);
    sizeCanvas();

    // 可见区间变化（滚动/缩放/fitContent）→ 重绘画线
    chart.timeScale().subscribeVisibleTimeRangeChange(() => redrawRef.current());

    // 画线模式：canvas 拦截鼠标（tool!=='none' 时 pointer-events 生效）
    const onCanvasMouseDown = (e: MouseEvent) => {
      const t = toolRef.current;
      if (t === 'none') return;
      const pos = eventPos(e);
      if (pos.time === null || pos.price === null) return;
      if (t === 'hline') {
        addDrawing({ type: 'hline', t1: pos.time, p1: pos.price, t2: null, p2: null });
        return;
      }
      pendingRef.current = { t1: pos.time, p1: pos.price, x1: pos.x, y1: pos.y };
      previewRef.current = null;
    };
    const onCanvasMouseMove = (e: MouseEvent) => {
      if (!pendingRef.current) return;
      previewRef.current = { x: e.clientX - canvasRef.current!.getBoundingClientRect().left, y: e.clientY - canvasRef.current!.getBoundingClientRect().top };
      redrawRef.current();
    };
    const onCanvasMouseUp = (e: MouseEvent) => {
      const pending = pendingRef.current;
      if (!pending || toolRef.current !== 'trend') return;
      pendingRef.current = null;
      previewRef.current = null;
      const pos = eventPos(e);
      const x = e.clientX - canvasRef.current!.getBoundingClientRect().left;
      const y = e.clientY - canvasRef.current!.getBoundingClientRect().top;
      if (pos.time !== null && pos.price !== null && Math.hypot(x - pending.x1, y - pending.y1) > 5) {
        addDrawing({ type: 'trend', t1: pending.t1, p1: pending.p1, t2: pos.time, p2: pos.price });
      }
      redrawRef.current();
    };
    const canvas = canvasRef.current!;
    canvas.addEventListener('mousedown', onCanvasMouseDown);
    canvas.addEventListener('mousemove', onCanvasMouseMove);
    canvas.addEventListener('mouseup', onCanvasMouseUp);

    // 非画线模式：点击选择已有画线（平移拖拽 > 4px 不算点击）
    const onContainerMouseDown = (e: MouseEvent) => {
      mouseDownRef.current = { x: e.clientX, y: e.clientY };
    };
    const onContainerClick = (e: MouseEvent) => {
      if (toolRef.current !== 'none') return;
      const down = mouseDownRef.current;
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      const rect = canvasRef.current!.getBoundingClientRect();
      const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      setSelectedId(hit);
    };
    container.addEventListener('mousedown', onContainerMouseDown);
    container.addEventListener('click', onContainerClick);

    return () => {
      observer.disconnect();
      canvas.removeEventListener('mousedown', onCanvasMouseDown);
      canvas.removeEventListener('mousemove', onCanvasMouseMove);
      canvas.removeEventListener('mouseup', onCanvasMouseUp);
      container.removeEventListener('mousedown', onContainerMouseDown);
      container.removeEventListener('click', onContainerClick);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      overlayRefs.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 键盘：ESC 退出画线/取消选择；Delete 删除所选 ─────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === 'Escape') {
        setTool('none');
        setSelectedId(null);
        pendingRef.current = null;
        previewRef.current = null;
        redrawRef.current();
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedRef.current) {
        removeDrawing(selectedRef.current);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
     
  }, []);

  // ── 画线持久化：按 storageKey 加载 ──────────────────────────

  useEffect(() => {
    const list = storageKey ? loadDrawingMap()[storageKey] ?? [] : [];
    drawingsRef.current = list;
    setDrawings(list);
    setSelectedId(null);
    pendingRef.current = null;
    previewRef.current = null;
  }, [storageKey]);

  // ── 画线：状态 → ref 同步 + 重绘 ────────────────────────────

  useEffect(() => {
    drawingsRef.current = drawings;
    redrawRef.current();
  }, [drawings]);

  useEffect(() => {
    selectedRef.current = selectedId;
    redrawRef.current();
  }, [selectedId]);

  const addDrawing = (d: Omit<Drawing, 'id' | 'color'>) => {
    const drawing: Drawing = {
      ...d,
      id: newId(),
      color: DRAW_PALETTE[drawingsRef.current.length % DRAW_PALETTE.length],
    };
    const next = [...drawingsRef.current, drawing];
    drawingsRef.current = next;
    setDrawings(next);
    if (storageKeyRef.current) persistDrawings(storageKeyRef.current, next);
  };

  const removeDrawing = (id: string) => {
    const next = drawingsRef.current.filter((d) => d.id !== id);
    drawingsRef.current = next;
    setSelectedId(null);
    setDrawings(next);
    if (storageKeyRef.current) persistDrawings(storageKeyRef.current, next);
  };

  const clearDrawings = () => {
    drawingsRef.current = [];
    setSelectedId(null);
    setDrawings([]);
    if (storageKeyRef.current) persistDrawings(storageKeyRef.current, []);
  };

  // ── 右边距调节块：拖动调整最后一根 K 线与右边缘的距离（MT5 图表平移）──

  const onMarginDragDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    const chart = chartRef.current;
    const ts = chart?.timeScale();
    if (!chart || !ts) return;
    e.preventDefault();
    const startX = e.clientX;
    const startMargin = rightMarginRef.current;
    setDraggingMargin(true);
    const onMove = (ev: PointerEvent) => {
      const last = barsRef.current.length - 1;
      if (last < 0) return;
      const range = ts.getVisibleLogicalRange();
      if (!range || range.to <= range.from) return;
      const width = containerRef.current?.clientWidth ?? 0;
      const barPx = width / (range.to - range.from);
      if (barPx <= 0) return;
      const m = Math.round(
        Math.min(RIGHT_MARGIN_MAX, Math.max(0, startMargin + (ev.clientX - startX) / barPx)),
      );
      if (m === rightMarginRef.current) return;
      rightMarginRef.current = m;
      setRightMargin(m);
      applyRightMargin(ts, last, m);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      setDraggingMargin(false);
      };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const resetRightMargin = () => {
    const chart = chartRef.current;
    rightMarginRef.current = RIGHT_MARGIN_DEFAULT;
    setRightMargin(RIGHT_MARGIN_DEFAULT);
    const last = barsRef.current.length - 1;
    if (chart && last >= 0) applyRightMargin(chart.timeScale(), last, RIGHT_MARGIN_DEFAULT);
  };

  // ── 主图数据更新（图表实例复用）─────────────────────────────

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    barsRef.current = bars;
    const prevLen = prevBarsLenRef.current;
    prevBarsLenRef.current = bars.length;
    if (!bars || bars.length === 0) {
      series.setData([]);
      updatePriceTag();
      return;
    }
    series.setData(
      bars.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    const ts = chart.timeScale();
    const last = bars.length - 1;
    const m = rightMarginRef.current;
    // 首次载入 / 历史扩量（为覆盖开仓时间多加载了根数）→ 重设初始视图；
    // 普通增量（收盘新 K 线，长度 +1）→ 保持用户当前视图不被拽走
    const historyExtended = prevLen === 0 || bars.length - prevLen > 1;
    if (historyExtended) {
      const earliest = positionsRef.current.reduce(
        (min, p) => Math.min(min, p.time),
        Infinity,
      );
      const idx = Number.isFinite(earliest) ? findBarIndex(bars, earliest) : -1;
      // 最早开仓 K 线在默认 500 根窗口之外 → 初始视图定位到它附近（左边留 10 根）
      if (idx >= 0 && idx < last - 500) {
        ts.setVisibleLogicalRange({ from: Math.max(0, idx - 10), to: last + m });
      } else {
        ts.fitContent();
        if (m > 0) applyRightMargin(ts, last, m);
      }
    } else {
      ts.applyOptions({ rightOffset: m });
    }
    updatePriceTag();
  }, [bars]);

  // ── 当前价标签（图内左侧，与右轴价格标签对称，MT5 风格）──────

  const lastPriceTagRef = useRef<HTMLDivElement>(null);
  const updatePriceTag = () => {
    const series = seriesRef.current;
    const el = lastPriceTagRef.current;
    if (!series || !el) return;
    const ref = barsRef.current;
    const last = ref.length > 0 ? ref[ref.length - 1] : null;
    if (!last) {
      el.style.display = 'none';
      return;
    }
    const y = series.priceToCoordinate(last.close);
    if (y === null) {
      el.style.display = 'none';
      return;
    }
    const { up, down } = upDownColors(redUpGreenDown);
    el.style.display = 'block';
    el.style.top = `${y}px`;
    el.style.background = last.close >= last.open ? up : down;
    el.textContent = last.close.toFixed(digits);
  };

  // ── 实时尾根：WS 聚合蜡烛增量 update（tick 级）───────────────

  useEffect(() => {
    const series = seriesRef.current;
    if (!liveBar || !series) return;
    series.update({
      time: liveBar.time as UTCTimestamp,
      open: liveBar.open,
      high: liveBar.high,
      low: liveBar.low,
      close: liveBar.close,
    });
    // 同步 barsRef 尾根，保持画线 timeToX / 最近邻查找的一致性
    const ref = barsRef.current;
    if (ref.length > 0) {
      const last = ref[ref.length - 1];
      if (liveBar.time === last.time) {
        ref[ref.length - 1] = liveBar;
      } else if (liveBar.time > last.time) {
        barsRef.current = [...ref, liveBar];
      }
    }
    updatePriceTag();
  }, [liveBar, redUpGreenDown]);

  // ── 持仓/挂单标记 ──────────────────────────────────────────
  // 挂单：委托价黄色虚线（createPriceLine 全宽）；持仓：canvas 画线（左边缘→开仓时间点）

  useEffect(() => {
    positionsRef.current = positions ?? [];
    redrawRef.current();
  }, [positions]);

  useEffect(() => {
    ordersRef.current = orders ?? [];
    const series = seriesRef.current;
    if (!series) return;
    for (const line of tradeLinesRef.current) series.removePriceLine(line);
    const lines: IPriceLine[] = [];
    for (const o of orders ?? []) {
      // 标签（BuyLimit …）不在 priceLine title 上画（title 只能贴右侧价格轴），
      // 改由 redraw 的 canvas 画在图表左边缘 — 见下方挂单标签循环。
      // 轴上价格标签关闭（价格并入左边缘文字标签）— 见 redraw 挂单标签循环。
      lines.push(
        series.createPriceLine({
          price: o.price_open,
          color: '#eab308',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
        }),
      );
    }
    tradeLinesRef.current = lines;
    redrawRef.current();
  }, [orders]);

  // ── 主图叠加指标（MA / BOLL）：增删 + setData ────────────────

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const refs = overlayRefs.current;
    // 移除已关闭的
    for (const key of Object.keys(refs) as OverlayKey[]) {
      if (!overlayData[key]) {
        chart.removeSeries(refs[key]!);
        delete refs[key];
      }
    }
    // 新增/更新开启的
    for (const key of Object.keys(overlayData) as OverlayKey[]) {
      const points = overlayData[key]!;
      let s = refs[key];
      if (!s) {
        s = chart.addLineSeries({
          color: OVERLAY_COLORS[key],
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        refs[key] = s;
      }
      s.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    }
    // 指标线可能撑大价格刻度 → 左侧当前价标签 y 需重定位
    updatePriceTag();
  }, [overlayData]);

  // ── 涨跌配色切换：即时重涂，不销毁重建 ───────────────────────

  useEffect(() => {
    const { up, down } = upDownColors(redUpGreenDown);
    seriesRef.current?.applyOptions({
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
  }, [redUpGreenDown]);

  // ── 副图图表：随指标切换创建/销毁 ────────────────────────────

  useEffect(() => {
    if (subIndicator === 'none') return;
    const container = subContainerRef.current;
    const mainChart = chartRef.current;
    if (!container || !mainChart) return;

    const subChart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontSize: 11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: 'rgba(55, 65, 81, 0.4)' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000);
          return TIME_FMT.format(d).replace(/^\d{4}\/\d{2}\/\d{2} /, '');
        },
      },
      localization: {
        timeFormatter: (time: number) => TIME_FMT.format(new Date(time * 1000)),
      },
      handleScroll: true,
      handleScale: true,
    });
    subChartRef.current = subChart;
    const refs: Record<string, ISeriesApi<'Line' | 'Histogram'>> = {};

    const line = (color: string) =>
      subChart.addLineSeries({
        color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });

    if (subIndicator === 'MACD') {
      refs.hist = subChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      refs.hist.createPriceLine({
        price: 0,
        color: '#4b5563',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: '',
      });
      refs.dif = line('#38bdf8');
      refs.dea = line('#f97316');
    } else if (subIndicator === 'RSI') {
      refs.rsi = line('#a78bfa');
      for (const level of [30, 70]) {
        refs.rsi.createPriceLine({
          price: level,
          color: 'rgba(148,163,184,0.45)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: '',
        });
      }
    } else if (subIndicator === 'KDJ') {
      refs.k = line('#facc15');
      refs.d = line('#38bdf8');
      refs.j = line('#f472b6');
      for (const level of [20, 80]) {
        refs.k.createPriceLine({
          price: level,
          color: 'rgba(148,163,184,0.45)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: '',
        });
      }
    }
    subSeriesRefs.current = refs;

    // 时间轴双向同步：副图数据比主图短（指标预热期），logical 索引空间不同，
    // 用 offset 补偿对齐（offset = 主图 bar 数 - 指标首序列点数）
    const mainTs = mainChart.timeScale();
    const subTs = subChart.timeScale();
    const offsetOf = () => subDataRef.current?.offset ?? 0;
    const applyRange = (
      from: ReturnType<typeof mainTs.getVisibleLogicalRange>,
      shift: number,
      to: ReturnType<typeof mainTs.getVisibleLogicalRange>,
    ) => {
      if (!from) return;
      const target = { from: from.from - shift, to: from.to - shift };
      if (to && Math.abs(to.from - target.from) < 0.5 && Math.abs(to.to - target.to) < 0.5) return;
      // shift = 主图偏移：main→sub 为 +offset（作用于 subTs），sub→main 为 -offset（作用于 mainTs）
      (shift >= 0 ? subTs : mainTs).setVisibleLogicalRange(target);
    };
    const onMainRange = () => applyRange(mainTs.getVisibleLogicalRange(), offsetOf(), subTs.getVisibleLogicalRange());
    const onSubRange = () => applyRange(subTs.getVisibleLogicalRange(), -offsetOf(), mainTs.getVisibleLogicalRange());
    mainTs.subscribeVisibleLogicalRangeChange(onMainRange);
    subTs.subscribeVisibleLogicalRangeChange(onSubRange);
    // 初始对齐
    onMainRange();

    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      subChart.applyOptions({ width, height });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      mainTs.unsubscribeVisibleLogicalRangeChange(onMainRange);
      subTs.unsubscribeVisibleLogicalRangeChange(onSubRange);
      subChart.remove();
      subChartRef.current = null;
      subSeriesRefs.current = {};
    };
     
  }, [subIndicator]);

  // ── 副图数据填充 ────────────────────────────────────────────

  useEffect(() => {
    const refs = subSeriesRefs.current;
    const data = subDataRef.current;
    if (!data || !refs.k && !refs.rsi && !refs.hist) return;
    switch (data.kind) {
      case 'MACD':
        refs.hist?.setData(data.hist);
        refs.dif?.setData(data.dif.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        refs.dea?.setData(data.dea.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        break;
      case 'RSI':
        refs.rsi?.setData(data.rsi.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        break;
      case 'KDJ':
        refs.k?.setData(data.k.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        refs.d?.setData(data.d.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        refs.j?.setData(data.j.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        break;
    }
    // 数据就绪后按真实 offset 重新对齐副图可见区间
    const mainChart = chartRef.current;
    const subChart = subChartRef.current;
    if (mainChart && subChart) {
      const r = mainChart.timeScale().getVisibleLogicalRange();
      if (r) subChart.timeScale().setVisibleLogicalRange({ from: r.from - data.offset, to: r.to - data.offset });
    }
  }, [subData]);

  // ── 渲染 ────────────────────────────────────────────────────

  if (error) {
    return (
      <div className="flex items-center justify-center bg-surface-900 rounded-lg border border-gray-700/50 h-96">
        <div className="text-center space-y-2">
          <p className="text-loss text-sm">{error}</p>
          <p className="text-xs text-gray-500">
            请在「行情 · 数据管理」页确认网关服务已启动
          </p>
        </div>
      </div>
    );
  }

  const overlayKeys = Object.keys(overlayData) as OverlayKey[];

  return (
    <div className="space-y-2">
      {/* 主图 + 画线 overlay（工具条悬浮在图表上，可拖动） */}
      <div className="relative bg-surface-900 rounded-lg border border-gray-700/50 overflow-hidden">
        <FloatingToolbar storageKey={`ind:${storageKey ?? 'default'}`} defaultX={8} defaultY={54}>
          <span className="px-1.5 text-gray-500">主图</span>
          {(['ma5', 'ma10', 'ma20', 'ma60'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setShow((s) => ({ ...s, [k]: !s[k] }))}
              className={`px-2 py-1 rounded-md font-mono flex items-center gap-1 transition-colors ${
                show[k] ? 'bg-surface-700 text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
              }`}
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: OVERLAY_COLORS[k] }} />
              {OVERLAY_LABELS[k]}
            </button>
          ))}
          <button
            onClick={() => setShow((s) => ({ ...s, boll: !s.boll }))}
            className={`px-2 py-1 rounded-md font-mono flex items-center gap-1 transition-colors ${
              show.boll ? 'bg-surface-700 text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: OVERLAY_COLORS.bollU }} />
            BOLL(20,2)
          </button>

          <span className="px-1.5 text-gray-500 border-l border-gray-700/60 ml-1">副图</span>
          {(['MACD', 'RSI', 'KDJ'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setSubIndicator(k)}
              className={`px-2 py-1 rounded-md font-mono transition-colors ${
                subIndicator === k
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
              }`}
            >
              {k}
            </button>
          ))}
          <button
            onClick={() => setSubIndicator('none')}
            className={`px-2 py-1 rounded-md transition-colors ${
              subIndicator === 'none'
                ? 'bg-blue-600 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
            }`}
          >
            关
          </button>

          <span className="px-1.5 text-gray-500 border-l border-gray-700/60 ml-1">画线</span>
          <button
            onClick={() => setTool((t) => (t === 'trend' ? 'none' : 'trend'))}
            title="趋势线：按住拖拽绘制"
            className={`px-2 py-1 rounded-md flex items-center gap-1 transition-colors ${
              tool === 'trend'
                ? 'bg-blue-600 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
            }`}
          >
            <TrendingUp className="w-3.5 h-3.5" />
            趋势线
          </button>
          <button
            onClick={() => setTool((t) => (t === 'hline' ? 'none' : 'hline'))}
            title="水平线：点击绘制"
            className={`px-2 py-1 rounded-md flex items-center gap-1 transition-colors ${
              tool === 'hline'
                ? 'bg-blue-600 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
            }`}
          >
            <Minus className="w-3.5 h-3.5" />
            水平线
          </button>
          {selectedId && (
            <button
              onClick={() => removeDrawing(selectedId)}
              className="px-2 py-1 rounded-md text-loss hover:bg-surface-800 transition-colors"
            >
              删除所选
            </button>
          )}
          <button
            onClick={clearDrawings}
            title="清除全部画线"
            className="px-2 py-1 rounded-md flex items-center gap-1 text-gray-400 hover:text-gray-200 hover:bg-surface-800 transition-colors"
          >
            <Eraser className="w-3.5 h-3.5" />
            清除
          </button>
        </FloatingToolbar>
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface-900/60 text-sm text-gray-400">
            加载 K 线…
          </div>
        )}
        <div ref={containerRef} className="h-96 w-full" />
        <div
          ref={lastPriceTagRef}
          className="pointer-events-none absolute left-0 z-[6] -translate-y-1/2 rounded px-1.5 py-0.5 text-[11px] font-semibold text-white"
          style={{ display: 'none', fontVariantNumeric: 'tabular-nums' }}
        />
        <canvas
          ref={canvasRef}
          className="absolute inset-0 z-[5]"
          style={{ pointerEvents: tool === 'none' ? 'none' : 'auto', cursor: tool === 'none' ? undefined : 'crosshair' }}
        />
        {/* 右边距调节块：按住横向拖动，调整最后一根 K 线与右边缘的距离 */}
        <button
          type="button"
          onPointerDown={onMarginDragDown}
          onDoubleClick={resetRightMargin}
          title="按住左右拖动：调节最后一根K线与右边缘的距离 · 双击复位"
          className="absolute right-0 bottom-0 z-[7] w-5 h-7 flex items-center justify-center bg-surface-800/80 border-t border-l border-gray-700/60 rounded-tl-md cursor-ew-resize active:cursor-ew-resize touch-none text-gray-500 hover:text-gray-200 transition-colors"
        >
          <GripVertical className="w-3.5 h-3.5" />
        </button>
        {draggingMargin && (
          <div className="absolute right-7 bottom-1 z-[7] px-2 py-0.5 rounded bg-surface-800/90 border border-gray-700/60 text-[11px] text-gray-300 pointer-events-none whitespace-nowrap">
            右边距 {rightMargin} 根
          </div>
        )}
        {overlayKeys.length > 0 && (
          <div className="absolute bottom-2 left-2 z-[6] flex gap-2.5 text-[11px] font-mono pointer-events-none">
            {overlayKeys.map((k) => (
              <span key={k} style={{ color: OVERLAY_COLORS[k] }}>
                {OVERLAY_LABELS[k]}
              </span>
            ))}
          </div>
        )}
        {(positions?.length ?? 0) + (orders?.length ?? 0) > 0 && (
          <div className="absolute top-2 right-2 z-[6] flex gap-1.5 text-[11px] pointer-events-none">
            {(positions?.length ?? 0) > 0 && (
              <span className="px-1.5 py-0.5 rounded bg-surface-800/80 text-sky-300">
                持仓 {positions!.length}
              </span>
            )}
            {(orders?.length ?? 0) > 0 && (
              <span className="px-1.5 py-0.5 rounded bg-surface-800/80 text-yellow-300">
                挂单 {orders!.length}
              </span>
            )}
          </div>
        )}
        {tool !== 'none' && (
          <div className="absolute bottom-2 left-2 z-[6] text-[11px] text-gray-400 bg-surface-900/80 px-2 py-1 rounded pointer-events-none">
            {tool === 'trend' ? '趋势线：按住拖拽绘制' : '水平线：点击绘制'} · ESC 退出 · 画线已自动保存
          </div>
        )}
        {!loading && bars.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-500 pointer-events-none">
            暂无该周期数据（可能处于休市时段）
          </div>
        )}
      </div>

      {/* 副图 */}
      {subIndicator !== 'none' && (
        <div className="relative bg-surface-900 rounded-lg border border-gray-700/50 overflow-hidden">
          <div ref={subContainerRef} className="h-44 w-full" />
          <span className="absolute top-1.5 left-2 z-[6] text-[11px] font-mono text-gray-500 pointer-events-none">
            {SUB_LABELS[subIndicator]}
          </span>
        </div>
      )}
    </div>
  );
}
