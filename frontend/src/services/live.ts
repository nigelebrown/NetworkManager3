// Live invalidation channel (WebSocket /ws/v1/events).
// The server sends compact "this changed" notices; pages re-query REST, which applies normal RBAC and scope.
// Reconnects with backoff and resumes from the last cursor; after any reconnect every page re-queries ("*"),
// because the server is authoritative. While disconnected, useLoad falls back to its normal interval polling.
import { getSession } from "./api";

export type LiveState = "connecting" | "live" | "offline" | "stopped";
type TopicListener = (topics: string[]) => void;
type StateListener = (s: LiveState) => void;

const CURSOR_KEY = "inm_ws_cursor";

class LiveChannel {
  private ws: WebSocket | null = null;
  private retry = 0;
  private timer: number | undefined;
  private everConnected = false;
  private topicListeners = new Set<TopicListener>();
  private stateListeners = new Set<StateListener>();
  state: LiveState = "stopped";

  private get cursor(): number | null {
    const v = sessionStorage.getItem(CURSOR_KEY);
    return v ? Number(v) : null;
  }
  private set cursor(v: number | null) {
    if (v == null) sessionStorage.removeItem(CURSOR_KEY); else sessionStorage.setItem(CURSOR_KEY, String(v));
  }

  private setState(s: LiveState) {
    this.state = s;
    this.stateListeners.forEach((l) => l(s));
  }

  private emit(topics: string[]) {
    this.topicListeners.forEach((l) => l(topics));
  }

  start() {
    if (this.ws || !getSession()) return;
    this.connect();
  }

  stop() {
    window.clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    this.everConnected = false;
    this.cursor = null;
    if (ws) ws.close(1000, "signed out");
    this.setState("stopped");
  }

  private connect() {
    const s = getSession();
    if (!s) { this.setState("stopped"); return; }
    this.setState("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/v1/events`);
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: s.access_token, since: this.cursor }));
    ws.onmessage = (ev) => {
      let m: { type: string; seq?: number; cursor?: number; topics?: string[] };
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "hello" || m.type === "resync") {
        this.retry = 0;
        const reconnect = this.everConnected;
        this.everConnected = true;
        if (typeof m.cursor === "number" && (m.type === "resync" || this.cursor == null)) this.cursor = m.cursor;
        this.setState("live");
        if (reconnect || m.type === "resync") this.emit(["*"]);   // re-query everything after a gap
      } else if (m.type === "invalidate") {
        if (typeof m.seq === "number") this.cursor = m.seq;
        this.emit(m.topics ?? []);
      } else if (m.type === "ping" && typeof m.cursor === "number" && this.cursor != null && m.cursor > this.cursor) {
        // missed notices that were filtered for this user: just advance
        this.cursor = m.cursor;
      }
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;           // superseded or stopped
      this.ws = null;
      if (ev.code === 4401 || ev.code === 4403) { this.setState("stopped"); return; }  // session invalid: do not hammer
      this.setState("offline");
      const delay = Math.min(15000, 1000 * 2 ** this.retry++);
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }

  onTopics(l: TopicListener) { this.topicListeners.add(l); return () => { this.topicListeners.delete(l); }; }
  onState(l: StateListener) { this.stateListeners.add(l); l(this.state); return () => { this.stateListeners.delete(l); }; }
}

export const live = new LiveChannel();

// Topics that legacy data pages care about when they do not name their own.
export const DATA_TOPICS = ["dashboard", "aps", "clients", "alerts", "experience", "sites", "devices"];
