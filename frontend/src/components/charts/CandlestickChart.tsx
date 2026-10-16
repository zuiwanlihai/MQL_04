import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, CrosshairMode, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { aggregateCandles, type Candle, type Timeframe } from '@/lib/syntheticData';

interface Props {
  /** M1 粒度的原始数据（组件内部会按选定周期聚合） */
  data: Candle[];
  height?: number;
  /** 是否显示周期切换按钮（默认 true） */
  showTimeframeButtons?: boolean;
}

const TF_BUTTONS: { tf: Timeframe; label: string }[] = [
  { tf: 'M1', label: 'M1' },
  { tf: 'M5', label: 'M5' },
  { tf: 'M15', label: 'M15' },
  { tf: 'M30', label: 'M30' },
  { tf: 'H1', label: 'H1' },
  { tf: 'H2', label: 'H2' },
  { tf: 'H4', label: 'H4' },
  { tf: 'D1', label: 'D1' },
  { tf: 'W1', label: 'W1' },
  { tf: 'MN1', label: 'MN1' },
  { tf: 'Y1', label: 'Y1' },
];
export default function CandlestickChart({ data, height = 420, showTimeframeButtons = true }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  // 周期切换 state
  const [tf, setTf] = useState<Timeframe>('M1');

  // 按当前周期聚合（M1 直接返回副本，其他周期聚合）
  const displayData = useMemo(() => aggregateCandles(data, tf), [data, tf]);

  // 初始化 chart（只做一次）
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      height,
      layout: {
        background: { color: '#131722' },    // MT5 深蓝灰背景
        textColor: '#d1d4dc',                 // MT5 浅色文字
        fontFamily: '"Segoe UI", ui-monospace, SFMono-Regular, Menlo, monospace',
      },
      grid: {
        vertLines: { color: '#2a2e39' },      // MT5 淡灰蓝网格
        horzLines: { color: '#2a2e39' },
      },
      crosshair: {
        mode: CrosshairMode.Magnet,            // MT5 磁吸十字线
        vertLine: { color: '#758696', width: 1, style: 2, labelBackgroundColor: '#363a4e' },
        horzLine: { color: '#758696', width: 1, style: 2, labelBackgroundColor: '#363a4e' },
      },
      rightPriceScale: {
        borderColor: '#2a2e39',
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: {
        borderColor: '#2a2e39',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // A 股配色：红涨绿跌 + 影线与实体同色，MT5 深蓝灰背景保留
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#ef5350',        // 上涨红
      downColor: '#26a69a',       // 下跌绿
      borderUpColor: '#ef5350',
      borderDownColor: '#26a69a',
      wickUpColor: '#ef5350',
      wickDownColor: '#26a69a',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;

    // ResizeObserver 自适应
    const ro = new ResizeObserver((entries) => {
      const { width } = entries[0].contentRect;
      chart.applyOptions({ width: Math.max(200, width) });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, [height]);

  // 数据更新时 setData
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const chart = chartRef.current;
    if (!cs || !chart) return;
    if (displayData.length === 0) {
      cs.setData([]);
      return;
    }

    const csData = displayData.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    cs.setData(csData);
    chart.timeScale().fitContent();
  }, [displayData]);

  return (
    <div className="w-full rounded-md overflow-hidden border border-gray-700/50 bg-[#131722]">
      {showTimeframeButtons && (
        <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[#2a2e39] flex-wrap">
          {TF_BUTTONS.map((b) => (
            <button
              key={b.tf}
              type="button"
              onClick={() => setTf(b.tf)}
              className={`px-2 py-0.5 text-xs rounded transition-colors font-mono ${
                tf === b.tf
                  ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-700/40 border border-transparent'
              }`}
              title={`切换到 ${b.label} 周期`}
            >
              {b.label}
            </button>
          ))}
          <span className="ml-auto text-xs text-gray-500">
            {displayData.length} 根 · {tf}
          </span>
        </div>
      )}
      <div ref={containerRef} style={{ height }} />
    </div>
  );
}
