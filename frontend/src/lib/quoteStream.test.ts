import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  connectQuoteStream,
  type QuoteStreamCallbacks,
} from './quoteStream';

/**
 * Minimal fake WebSocket: records sends, exposes server-side triggers.
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.closed = true;
  }
  serverOpen(): void {
    this.onopen?.();
  }
  serverFrame(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  serverClose(code = 1000): void {
    this.onclose?.({ code });
  }
}

/** Flush the microtask chain inside connect() (ticket fetch → new WebSocket). */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function makeCallbacks() {
  const cb: QuoteStreamCallbacks = {
    onStatus: vi.fn(),
    onQuotes: vi.fn(),
    onSubscribed: vi.fn(),
    onDegraded: vi.fn(),
  };
  return cb;
}

describe('connectQuoteStream', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function connect(callbacks = makeCallbacks(), extra = {}) {
    return connectQuoteStream({
      symbols: ['EURUSD', 'XAUUSD'],
      fetchTicket: async () => 'TICKET-1',
      getSocketUrl: (t) => `ws://host/ws/quotes?ticket=${t}`,
      WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
      ...extra,
      callbacks,
    });
  }

  it('opens with ticket URL, subscribes and goes live', async () => {
    const cb = makeCallbacks();
    connect(cb);
    await flush();

    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toBe('ws://host/ws/quotes?ticket=TICKET-1');
    expect(cb.onStatus).toHaveBeenCalledWith('connecting');
    ws.serverOpen();
    expect(ws.sent).toEqual([
      JSON.stringify({ action: 'subscribe', symbols: ['EURUSD', 'XAUUSD'] }),
    ]);
    expect(cb.onStatus).toHaveBeenCalledWith('live');
  });

  it('dispatches quotes / subscribed / gateway_error frames', async () => {
    const cb = makeCallbacks();
    connect(cb);
    await flush();
    const ws = FakeWebSocket.instances[0];
    ws.serverOpen();

    ws.serverFrame({ type: 'quotes', items: [{ symbol_id: 'EURUSD', bid: 1.1 }] });
    expect(cb.onQuotes).toHaveBeenCalledWith([{ symbol_id: 'EURUSD', bid: 1.1 }]);

    ws.serverFrame({ type: 'subscribed', symbols: ['EURUSD'] });
    expect(cb.onSubscribed).toHaveBeenCalledWith(['EURUSD']);

    ws.serverFrame({ type: 'gateway_error', detail: '行情网关不可达' });
    expect(cb.onDegraded).toHaveBeenCalled();

    // 未知帧不抛错
    ws.serverFrame({ type: 'ping' });
    ws.serverFrame('not-an-object');
    expect(cb.onQuotes).toHaveBeenCalledTimes(1);
  });

  it('reconnects with backoff after a normal close', async () => {
    const cb = makeCallbacks();
    connect(cb);
    await flush();
    FakeWebSocket.instances[0].serverClose(1000);
    expect(cb.onStatus).toHaveBeenLastCalledWith('polling');

    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeWebSocket.instances).toHaveLength(2); // 1s 后重连

    FakeWebSocket.instances[1].serverClose(1000);
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeWebSocket.instances).toHaveLength(2); // 退避翻倍 → 2s 内未重连
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it('refreshes auth once on 4401 and retries immediately', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const cb = makeCallbacks();
    connect(cb, { onRefreshAuth: refresh });
    await flush();

    FakeWebSocket.instances[0].serverClose(4401);
    await flush();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances).toHaveLength(2); // 立即重连（无退避计时）

    // 第二次 4401 不再刷新 → 走退避
    FakeWebSocket.instances[1].serverClose(4401);
    await flush();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(cb.onStatus).toHaveBeenLastCalledWith('polling');
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it('falls back to polling when the ticket fetch fails', async () => {
    const cb = makeCallbacks();
    connectQuoteStream({
      symbols: ['EURUSD'],
      fetchTicket: async () => {
        throw new Error('401');
      },
      getSocketUrl: (t) => `ws://host/ws/quotes?ticket=${t}`,
      WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
      callbacks: cb,
    });
    await flush();
    expect(cb.onStatus).toHaveBeenLastCalledWith('polling');
    expect(FakeWebSocket.instances).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    // 重连触发（connecting），ticket 仍失败 → 再次回到 polling
    expect(cb.onStatus).toHaveBeenCalledWith('connecting');
    expect(cb.onStatus).toHaveBeenLastCalledWith('polling');
  });

  it('close() stops reconnects', async () => {
    const cb = makeCallbacks();
    const handle = connect(cb);
    await flush();
    handle.close();
    FakeWebSocket.instances[0].serverClose(1000);
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
