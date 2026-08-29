/*
 * COURTSIDE API transport.
 *
 * The visual demo remains useful without a running API, but when the FastAPI
 * service is reachable this tiny client switches the same reducer to the
 * real POST-SSE and highlights contracts.  It deliberately exposes no raw
 * provider response or arbitrary URL fetching surface to the page.
 */
(function () {
  "use strict";

  function trimBase(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function defaultBase() {
    if (window.COURTSIDE_API_BASE) return trimBase(window.COURTSIDE_API_BASE);
    // A same-origin API is convenient for a mounted deployment.  The local
    // static demo is normally served on 4173 while uvicorn runs on 8000.
    if (window.location.port === "8000") return window.location.origin;
    if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
      return "http://127.0.0.1:8000";
    }
    return "";
  }

  const baseUrl = defaultBase();

  // A proxy or an upstream failure can leave a fetch stream open without
  // delivering a terminal SSE event.  Without a client-side deadline the
  // composer remains locked in its loading state forever.  The API itself has
  // bounded provider timeouts, so this is deliberately generous while still
  // guaranteeing that the UI eventually recovers to the offline demo/error
  // branch.
  // Live SiliconFlow generation can take 10–20 seconds on a cold request.
  // Keep the client deadline above the server's 20s model budget so a valid
  // model answer is not discarded just as it completes.
  const STREAM_TIMEOUT_MS = 30_000;

  function endpoint(path) {
    return `${baseUrl}${path}`;
  }

  async function withTimeout(ms, operation) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), ms);
    try {
      return await operation(controller.signal);
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function probe() {
    if (!baseUrl) return false;
    try {
      const response = await withTimeout(900, (signal) => fetch(endpoint("/healthz"), {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
        credentials: "omit",
      }));
      return response.ok;
    } catch (_error) {
      return false;
    }
  }

  async function authStatus() {
    if (!baseUrl) throw new Error("API base is not configured");
    let response;
    try {
      response = await withTimeout(1500, (signal) => fetch(endpoint("/api/v1/auth/status"), {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
        signal,
      }));
    } catch (cause) {
      const error = new Error("登录服务暂时不可用。", { cause });
      error.network = true;
      throw error;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "登录服务暂时不可用。");
      error.publicPayload = payload;
      error.status = response.status;
      error.network = false;
      throw error;
    }
    return payload || { enabled: false, authenticated: true };
  }

  async function login(password) {
    if (!baseUrl) throw new Error("API base is not configured");
    let response;
    try {
      response = await withTimeout(5000, (signal) => fetch(endpoint("/api/v1/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ password: String(password || "") }),
        credentials: "include",
        signal,
      }));
    } catch (cause) {
      const error = new Error("登录服务暂时不可用，请稍后重试。", { cause });
      error.network = true;
      throw error;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "密码不正确。");
      error.publicPayload = payload;
      error.status = response.status;
      error.network = false;
      error.authRequired = response.status === 401 || response.status === 429;
      throw error;
    }
    return payload || { authenticated: true };
  }

  async function logout() {
    if (!baseUrl) return { authenticated: false };
    const response = await fetch(endpoint("/api/v1/auth/logout"), {
      method: "POST",
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    return response.json().catch(() => ({ authenticated: false }));
  }

  class SSEParser {
    constructor(onEvent) {
      this.buffer = "";
      this.event = "message";
      this.data = [];
      this.onEvent = onEvent;
    }

    feed(chunk) {
      this.buffer += chunk;
      const lines = this.buffer.split(/\r?\n/);
      this.buffer = lines.pop() || "";
      lines.forEach((line) => this.consume(line));
    }

    flush() {
      if (this.buffer) this.consume(this.buffer);
      this.buffer = "";
      this.dispatch();
    }

    consume(line) {
      if (line === "") {
        this.dispatch();
        return;
      }
      if (line.startsWith(":")) return;
      const separator = line.indexOf(":");
      const field = separator === -1 ? line : line.slice(0, separator);
      const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
      if (field === "event") this.event = value || "message";
      if (field === "data") this.data.push(value);
    }

    dispatch() {
      if (!this.data.length) {
        this.event = "message";
        return;
      }
      const raw = this.data.join("\n");
      this.onEvent(this.event, raw);
      this.event = "message";
      this.data = [];
    }
  }

  async function streamChat({ message, sessionId, clientMessageId, onEvent, signal }) {
    if (!baseUrl) throw new Error("API base is not configured");
    const requestController = new AbortController();
    let timedOut = false;
    const forwardAbort = () => requestController.abort();
    if (signal) {
      if (signal.aborted) requestController.abort();
      else signal.addEventListener("abort", forwardAbort, { once: true });
    }
    const timeout = window.setTimeout(() => {
      timedOut = true;
      requestController.abort();
    }, STREAM_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(endpoint("/api/v1/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message,
          session_id: sessionId || undefined,
          client_message_id: clientMessageId || undefined,
          client_timezone: "Asia/Shanghai",
        }),
        credentials: "include",
        signal: requestController.signal,
      });

      const contentType = response.headers.get("content-type") || "";
      if (!response.ok || !contentType.includes("text/event-stream")) {
        let payload = null;
        try {
          payload = await response.json();
        } catch (_error) {
          // Keep the public error generic; the UI must not display raw response text.
        }
        const error = new Error(payload?.error?.message || "服务暂时不可用，请稍后重试。");
        error.publicPayload = payload || {
          status: "failed",
          error: { code: "SERVICE_BUSY", retryable: true, message: error.message },
        };
        error.network = false;
        error.authRequired = response.status === 401 || payload?.error?.code === "AUTH_REQUIRED";
        throw error;
      }

      if (!response.body) throw new Error("流式响应不可用");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let terminalSeen = false;
      const parser = new SSEParser((eventName, raw) => {
        // Once a terminal event arrives the API contract is complete.  Ignore
        // accidental frames after it and stop reading below; this prevents a
        // proxy that keeps the HTTP connection alive from keeping the UI in
        // “正在准备” indefinitely.
        if (terminalSeen) return;
        let payload;
        try {
          payload = JSON.parse(raw);
        } catch (_error) {
          return;
        }
        if (eventName === "message.completed" || eventName === "run.error") {
          terminalSeen = true;
        }
        onEvent(eventName, payload);
      });
      while (!terminalSeen) {
        const chunk = await reader.read();
        if (chunk.done) break;
        parser.feed(decoder.decode(chunk.value, { stream: true }));
      }
      // Release the network reader promptly when a terminal frame was seen.
      // The server normally closes immediately, but this is important when a
      // load balancer buffers/keeps the connection open.
      if (terminalSeen) {
        try {
          await reader.cancel();
        } catch (_error) {
          // The terminal event has already been delivered; cancellation is
          // only a resource cleanup best effort.
        }
      }
      parser.feed(decoder.decode());
      parser.flush();
    } catch (error) {
      if (timedOut && !signal?.aborted) {
        const timeoutError = new Error("流式响应超时，已切换到离线演示。", { cause: error });
        timeoutError.network = true;
        throw timeoutError;
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", forwardAbort);
    }
  }

  async function highlights(dateValue, timezone) {
    if (!baseUrl) throw new Error("API base is not configured");
    const query = new URLSearchParams({ timezone: timezone || "Asia/Shanghai" });
    if (dateValue) query.set("date", dateValue);
    let response;
    try {
      response = await fetch(endpoint(`/api/v1/highlights?${query.toString()}`), {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
      });
    } catch (cause) {
      const error = new Error("日期赛事连接暂时不可用。", { cause });
      error.network = true;
      throw error;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "日期赛事暂时不可用。");
      error.publicPayload = payload;
      error.status = response.status;
      error.network = false;
      error.authRequired = response.status === 401 || payload?.error?.code === "AUTH_REQUIRED";
      throw error;
    }
    return payload;
  }

  async function highlightsAvailability(fromDate, toDate, timezone) {
    if (!baseUrl) throw new Error("API base is not configured");
    const query = new URLSearchParams({ timezone: timezone || "Asia/Shanghai" });
    if (fromDate) query.set("from", fromDate);
    if (toDate) query.set("to", toDate);
    let response;
    try {
      response = await withTimeout(6000, (signal) => fetch(
        endpoint(`/api/v1/highlights/availability?${query.toString()}`),
        {
          method: "GET",
          headers: { Accept: "application/json" },
          credentials: "include",
          signal,
        },
      ));
    } catch (cause) {
      const error = new Error("日期赛事连接暂时不可用。", { cause });
      error.network = true;
      throw error;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "日期赛事暂时不可用。");
      error.publicPayload = payload;
      error.status = response.status;
      error.network = false;
      error.authRequired = response.status === 401 || payload?.error?.code === "AUTH_REQUIRED";
      throw error;
    }
    return payload;
  }

  window.CourtsideApi = {
    baseUrl,
    probe,
    authStatus,
    login,
    logout,
    streamChat,
    highlights,
    highlightsAvailability,
    SSEParser,
  };
})();
