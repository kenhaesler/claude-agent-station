type MessageHandler = (data: string) => void;

export class LogWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage: MessageHandler;
  private onStatusChange: (connected: boolean) => void;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private paused = false;
  private shouldReconnect = true;

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
    this.ws = new WebSocket(`${protocol}//${host}${this.url}`);

    this.ws.onopen = () => {
      this.onStatusChange(true);
    };

    this.ws.onmessage = (event) => {
      if (!this.paused) {
        this.onMessage(event.data);
      }
    };

    this.ws.onclose = () => {
      this.onStatusChange(false);
      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => this.doConnect(), 3000);
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
