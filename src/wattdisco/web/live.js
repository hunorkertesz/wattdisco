// WebSocket client. Keeps one store object up to date and calls render(store) on every change.

export function connectLive(render, { onOpen } = {}) {
  const store = { connected: false, workout: null, settings: null, speed: 1, history: [], state: null, compare: null };
  let ws = null;
  let retry = 0;

  function addPoint(point) {
    const last = store.history[store.history.length - 1];
    if (point && (!last || point[0] > last[0])) store.history.push(point);
  }

  function open() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${scheme}://${location.host}/ws`);
    ws.onopen = () => {
      retry = 0;
      store.connected = true;
      onOpen?.(api); // e.g. ask for test bench data again after a reconnect
    };
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "hello") {
        store.workout = msg.workout;
        store.speed = msg.speed;
        store.settings = msg.settings;
        store.history = msg.history.slice();
        store.state = msg.state;
      } else if (msg.type === "state") {
        store.state = msg;
        addPoint(msg.point);
      } else if (msg.type === "compare") {
        store.compare = msg;
      } else if (msg.type === "error") {
        console.warn("server:", msg.message);
        return;
      }
      render(store);
    };
    ws.onclose = () => {
      store.connected = false;
      render(store);
      setTimeout(open, Math.min(5000, 500 * 2 ** retry++));
    };
  }

  const api = {
    store,
    send(cmd) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
    },
  };
  open();
  return api;
}
