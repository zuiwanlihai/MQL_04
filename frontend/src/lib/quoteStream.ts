import type { Quote } from '@/types/api';

/**
 * WS quote-stream controller (行情页实时化) — framework-free so it can be
 * unit-tested in a node environment with a fake WebSocket.
 *
 * Lifecycle: fetch a one-time ticket → open `WS /ws/quotes?ticket=` →
 * subscribe → live.  On close: exponential-backoff reconnect (1s→30s) with
 * status 'polling' in between (the hook falls back to REST polling).  A 4401
 * close means the ticket/JWT expired → refresh auth once and retry
 * immediately.  `gateway_error` frames degrade gracefully without dropping
 * the socket.
 */

export type QuoteStreamStatus = 'connecting' | 'live' | 'polling' | 'error';

export interface QuoteStreamCallbacks {
  onStatus: (status: QuoteStreamStatus) => void;
  onQuotes: (items: Quote[]) => void;
  onSubscribed?: (symbols: string[]) => void;
  /** Gateway-side failure frame — socket stays open. */
  onDegraded?: () => void;
}

export interface QuoteStreamDeps {
  symbols: string[];
  fetchTicket: () => Promise<string>;
  /** Build the final WS URL (browser provides location; tests fake it). */
  getSocketUrl: (ticket: string) => string;
  WebSocketImpl: typeof WebSocket;
  /** Called once when a 4401 close suggests an expired JWT. */
  onRefreshAuth?: () => Promise<unknown>;
  callbacks: QuoteStreamCallbacks;
}

export interface QuoteStreamHandle {
  close(): void;
}

const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;

export function connectQuoteStream(deps: QuoteStreamDeps): QuoteStreamHandle {
  const { symbols, fetchTicket, getSocketUrl, WebSocketImpl, onRefreshAuth, callbacks } =
    deps;
  let disposed = false;
  let ws: WebSocket | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let delay = RETRY_BASE_MS;
  let reticketed = false;

  const scheduleRetry = () => {
    if (disposed) return;
    callbacks.onStatus('polling');
    retryTimer = setTimeout(connect, delay);
    delay = Math.min(delay * 2, RETRY_MAX_MS);
  };

  const connect = () => {
    if (disposed) return;
    callbacks.onStatus('connecting');
    void (async () => {
      let ticket: string;
      try {
        ticket = await fetchTicket();
      } catch {
        scheduleRetry();
        return;
      }
      if (disposed) return;
      ws = new WebSocketImpl(getSocketUrl(ticket));
      ws.onopen = () => {
        delay = RETRY_BASE_MS;
        ws!.send(JSON.stringify({ action: 'subscribe', symbols }));
        callbacks.onStatus('live');
      };
      ws.onmessage = (ev: MessageEvent) => {
        let frame: { type?: string; items?: Quote[]; symbols?: string[] };
        try {
          frame = JSON.parse(String(ev.data));
        } catch {
          return;
        }
        if (frame.type === 'quotes' && Array.isArray(frame.items)) {
          callbacks.onQuotes(frame.items);
        } else if (frame.type === 'subscribed' && Array.isArray(frame.symbols)) {
          callbacks.onSubscribed?.(frame.symbols);
        } else if (frame.type === 'gateway_error') {
          callbacks.onDegraded?.();
        }
        // 'ping' / unknown frames are keepalive only — status stays 'live'.
      };
      ws.onclose = (ev: CloseEvent) => {
        ws = null;
        if (disposed) return;
        if (ev.code === 4401 && !reticketed && onRefreshAuth) {
          reticketed = true;
          void onRefreshAuth()
            .then(() => {
              if (!disposed) connect();
            })
            .catch(() => scheduleRetry());
          return;
        }
        scheduleRetry();
      };
      ws.onerror = () => {
        /* onclose always follows an error */
      };
    })();
  };

  connect();
  return {
    close() {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    },
  };
}
