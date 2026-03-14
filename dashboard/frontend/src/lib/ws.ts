import { getStoredApiKey } from './api';

type MessageHandler = (data: string) => void;

export class LogWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage: MessageHandler;
  private onStatusChange: (connected: boolean) => void;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private paused = false;
  private shouldReconnect = true;
  private reconnectDelay = 1000;
  private static readonly MAX_RECONNECT_DELAY = 30000;

  constructor(url: string, onMessage: MessageHandler, onStatusChange: (connected: boolean) => void) {
    this.url = url;
    this.onMessage = onMessage;
    this.onStatusChange = onStatusChange;
  }

  connect() {
    this.shouldReconnect = true;
    this.doConnect();
  }

  private doConnect() {
    if (this.ws) {
      this.ws.close();
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = import.meta.env.VITE_API_URL
      ? new URL(import.meta.env.VITE_API_URL).host
      : window.location.host;

    // Build WebSocket URL with auth token (WebSocket can't use Bearer headers)
    const wsUrl = new URL(`${protocol}//${host}${this.url}`);
    // Preserve any existing query params from this.url
    const existingParams = new URL(this.url, window.location.href).searchParams;
    existingParams.forEach((value, key) => wsUrl.searchParams.set(key, value));
    // Add API key for authentication
    const apiKey = getStoredApiKey();
    if (apiKey) {
      wsUrl.searchParams.set('token', apiKey);
    }
    this.ws = new WebSocket(wsUrl.toString());

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.onStatusChange(true);
    };

    this.ws.onmessage = (event) => {
      if (!this.paused) {
        this.onMessage(event.data);
      }
    };

    this.ws.onclose = (event) => {
      this.onStatusChange(false);
      if (event.code === 1008) {
        // Auth failure — stop retrying and prompt for API key
        this.shouldReconnect = false;
        window.dispatchEvent(new CustomEvent('station-auth-required'));
        return;
      }
      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => this.doConnect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, LogWebSocket.MAX_RECONNECT_DELAY);
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  pause() {
    this.paused = true;
  }

  resume() {
    this.paused = false;
  }

  get isPaused() {
    return this.paused;
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
