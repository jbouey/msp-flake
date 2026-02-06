/**
 * WebSocket hook for real-time event push from backend.
 * Invalidates React Query caches when relevant events arrive.
 * Exposes connection state so polling hooks can disable when WS is active.
 */

import { useEffect, useRef, useCallback, useState, createContext, useContext } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface WSEvent {
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

// Map event types to query keys that should be invalidated
// Use specific keys where possible (e.g. ['site', siteId]) to avoid blanket invalidation
function getKeysToInvalidate(event: WSEvent): string[][] {
  const siteId = event.payload?.site_id as string | undefined;

  switch (event.type) {
    case 'appliance_checkin':
      return [
        ['fleet'],
        ['stats'],
        // Scope to specific site if provided, otherwise invalidate all
        ...(siteId ? [['sites'], ['site', siteId]] : [['sites'], ['site']]),
      ];
    case 'incident_created':
      return [['incidents'], ['stats'], ['notifications']];
    case 'incident_resolved':
      return [['incidents'], ['stats']];
    case 'notification_created':
      return [['notifications']];
    case 'pattern_promoted':
      return [['learning']];
    case 'compliance_drift':
      return [['fleet'], ['events'], ['stats']];
    case 'order_status_changed':
      return [
        ...(siteId ? [['orders', siteId], ['site', siteId]] : [['orders'], ['site']]),
      ];
    default:
      return [];
  }
}

const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];
const PING_INTERVAL = 30000;

// Context to share WS connection state with polling hooks
export const WebSocketContext = createContext<{ connected: boolean }>({ connected: false });

export function useWebSocketStatus() {
  return useContext(WebSocketContext);
}

export function useWebSocket() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const pingTimer = useRef<ReturnType<typeof setInterval>>();
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    // Build WebSocket URL from current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/events`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempt.current = 0;
        setConnected(true);
        // Start ping keepalive
        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, PING_INTERVAL);
      };

      ws.onmessage = (event) => {
        try {
          const data: WSEvent = JSON.parse(event.data);
          if (data.type === 'pong') return;

          // Invalidate scoped query caches
          const keys = getKeysToInvalidate(data);
          keys.forEach((queryKey) => {
            queryClient.invalidateQueries({ queryKey });
          });
        } catch (err) {
          console.warn('[WS] Failed to parse message:', err);
        }
      };

      ws.onclose = () => {
        cleanupTimers();
        setConnected(false);
        scheduleReconnect();
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      scheduleReconnect();
    }
  }, [queryClient]);

  const cleanupTimers = useCallback(() => {
    if (pingTimer.current) {
      clearInterval(pingTimer.current);
      pingTimer.current = undefined;
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    const delay = RECONNECT_DELAYS[
      Math.min(reconnectAttempt.current, RECONNECT_DELAYS.length - 1)
    ];
    reconnectAttempt.current += 1;
    reconnectTimer.current = setTimeout(connect, delay);
  }, [connect]);

  useEffect(() => {
    connect();

    return () => {
      cleanupTimers();
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
    };
  }, [connect, cleanupTimers]);

  return { connected };
}
