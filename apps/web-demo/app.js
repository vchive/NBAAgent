/*
 * COURTSIDE / NBA Intelligence
 * A dependency-free UI demo.  The demo transport emits the same logical
 * events as POST /api/v1/chat/stream, so it can be replaced by a real SSE
 * client without changing the renderer or reducer.
 */
(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const el = {
    time: $("#beijing-time"),
    modeLabel: $("#mode-label"),
    onlineLabel: $("#online-label"),
    onlineLabelText: $("#online-label-text"),
    dayDivider: $("#day-divider"),
    newSession: $("#new-session"),
    featuredGame: $("#featured-game"),
    featuredGameMeta: $("#featured-game-meta"),
    featuredGameState: $("#featured-game-state"),
    featuredHomeToken: $("#featured-home-token"),
    featuredHomeName: $("#featured-home-name"),
    featuredAwayToken: $("#featured-away-token"),
    featuredAwayName: $("#featured-away-name"),
    featuredGameFoot: $("#featured-game-foot"),
    gamesSection: $("#games-section"),
    gameList: $("#game-list"),
    gameListCount: $("#game-list-count"),
    highlightsTitle: $("#highlights-title"),
    highlightModes: $$("[data-highlight-mode]"),
    highlightDateLabel: $("#highlight-date-label"),
    highlightDate: $("#highlight-date"),
    highlightDatePicker: $("#highlight-date-picker"),
    highlightDateTrigger: $("#highlight-date-trigger"),
    highlightDateValue: $("#highlight-date-value"),
    highlightCalendar: $("#highlight-calendar"),
    calendarPrev: $("#calendar-prev"),
    calendarNext: $("#calendar-next"),
    calendarMonth: $("#calendar-month"),
    calendarGrid: $("#calendar-grid"),
    calendarStatus: $("#calendar-status"),
    calendarStatusText: $("#calendar-status-text"),
    highlightsEmpty: $("#highlights-empty"),
    promptList: $("#prompt-list"),
    chatLog: $("#chat-log"),
    chatForm: $("#chat-form"),
    input: $("#message-input"),
    charCount: $("#char-count"),
    sendButton: $("#send-button"),
    streamStatus: $("#stream-status"),
    streamStage: $("#stream-stage"),
    stopStream: $("#stop-stream"),
    connectionState: $("#connection-state"),
    connectionLabel: $("#connection-label"),
    pbpList: $("#pbp-list"),
    periodTabs: $$(".period-tab"),
    eventCount: $("#event-count"),
    quarterCells: $$(`[data-quarter]`),
    replayPlay: $("#replay-play"),
    replayLabel: $("#replay-label"),
    pbpSlider: $("#pbp-slider"),
    replayPosition: $("#replay-position"),
    selectedPlayText: $("#selected-play-text"),
    awayScore: $("#away-score"),
    homeScore: $("#home-score"),
    scoreClock: $("#score-clock"),
    hudEyebrow: $("#hud-eyebrow"),
    hudStatus: $("#hud-status"),
    hudAwayToken: $("#hud-away-token"),
    hudAwayName: $("#hud-away-name"),
    hudAwayRecord: $("#hud-away-record"),
    hudHomeToken: $("#hud-home-token"),
    hudHomeName: $("#hud-home-name"),
    hudHomeRecord: $("#hud-home-record"),
    hudPossession: $("#hud-possession"),
    hudPace: $("#hud-pace"),
    toast: $("#toast"),
    authGate: $("#auth-gate"),
    authForm: $("#auth-form"),
    authPassword: $("#auth-password"),
    authSubmit: $("#auth-submit"),
    authError: $("#auth-error"),
    logout: $("#logout-button"),
  };

  const STORAGE_KEY = "courtside-demo-session-v1";
  const STAGE_COPY = {
    parsing: "正在解析问题",
    retrieving: "正在核对比赛数据",
    verifying: "正在核验事实口径",
    composing: "正在整理回答",
  };

  // Fixture events are deliberately small and deterministic: they make the
  // replay useful offline while keeping the browser out of the fact pipeline.
  const PBP = {
    Q2: [
      { clock: "03:18.0", team: "OKC", teamClass: "token-okc", player: "C. Holmgren", action: "封盖 · 防守回合", detail: "雷霆守住禁区", away: 43, home: 46 },
      { clock: "02:41.0", team: "BOS", teamClass: "token-bos", player: "D. White", action: "三分命中 · 3分", detail: "凯尔特人重新领先", away: 43, home: 49 },
      { clock: "00:12.0", team: "OKC", teamClass: "token-okc", player: "S. Gilgeous-Alexander", action: "罚球 2 中 2", detail: "半场结束前缩小分差", away: 52, home: 55 },
    ],
    Q3: [
      { clock: "08:35.0", team: "BOS", teamClass: "token-bos", player: "J. Tatum", action: "助攻 · 空切上篮", detail: "凯尔特人连续得分", away: 65, home: 69 },
      { clock: "04:02.0", team: "OKC", teamClass: "token-okc", player: "J. Williams", action: "三分命中 · 3分", detail: "雷霆追至 2 分", away: 76, home: 78 },
      { clock: "00:00.0", team: "BOS", teamClass: "token-bos", player: "J. Brown", action: "压哨三分命中 · 3分", detail: "第三节结束", away: 80, home: 86 },
    ],
    Q4: [
      { clock: "02:14.0", team: "OKC", teamClass: "token-okc", player: "S. Gilgeous-Alexander", action: "急停跳投命中 · 2分", detail: "雷霆追至 3 分", away: 98, home: 101 },
      { clock: "01:42.0", team: "BOS", teamClass: "token-bos", player: "J. Brown", action: "突破上篮命中 · 2分", detail: "凯尔特人稳住优势", away: 98, home: 103 },
      { clock: "00:58.0", team: "OKC", teamClass: "token-okc", player: "L. Dort", action: "底角三分命中 · 3分", detail: "雷霆再次迫近", away: 101, home: 103 },
      { clock: "00:31.0", team: "BOS", teamClass: "token-bos", player: "D. White", action: "接球三分命中 · 3分", detail: "凯尔特人领先 5 分", away: 101, home: 106 },
      { clock: "00:05.0", team: "OKC", teamClass: "token-okc", player: "S. Gilgeous-Alexander", action: "突破造犯规 · 罚球 1 中 1", detail: "最后一次有效得分", away: 102, home: 106 },
      { clock: "00:00.0", team: "BOS", teamClass: "token-bos", player: "J. Tatum", action: "终场哨响 · 比赛结束", detail: "凯尔特人 108–104 取胜", away: 104, home: 108 },
    ],
    // This fixture ended in regulation.  Keeping OT empty makes the tab a
    // useful no-data state without inventing a second, contradictory score.
    OT: [],
  };

  const state = {
    sessionId: loadSessionId(),
    currentPeriod: "Q4",
    pbpIndex: 5,
    replayTimer: null,
    run: null,
    streaming: false,
    toastTimer: null,
    lastRequest: null,
    contextRequest: null,
    retryCount: 0,
    highlightMode: "today",
    highlightDate: "2026-06-12",
    historyDate: "2026-06-12",
    activeGame: null,
    activePbp: PBP,
    highlightGames: [],
    selectedGameId: null,
    apiAvailable: false,
    apiProbeComplete: false,
    highlightRequest: 0,
    // Availability is kept in the browser projection because the current
    // highlights contract is intentionally date-scoped. Unknown API dates are
    // never treated as empty; they remain disabled until verified.
    highlightAvailability: new Map(),
    calendarMonth: "2026-06",
    calendarScanMonths: new Set(),
    calendarScanRequests: new Map(),
    calendarScanToken: 0,
    calendarOpen: false,
    authEnabled: false,
    authenticated: false,
    authBootstrapped: false,
  };

  // The static demo keeps a deterministic multi-game slate offline. The same
  // shape is returned by GET /api/v1/highlights in the API implementation, so
  // this projection can switch transports without changing the interaction.
  const HIGHLIGHT_FIXTURES = {
    "2026-06-12": [
      {
        game_id: "2026-finals-g4",
        date: "2026-06-12",
        home_name: "凯尔特人",
        home_abbreviation: "BOS",
        away_name: "雷霆",
        away_abbreviation: "OKC",
        home_score: 108,
        away_score: 104,
        series_game_number: 4,
        status: "final",
        start_utc: "2026-06-12T01:30:00Z",
        quarter_scores: { Q1: "27–24", Q2: "25–31", Q3: "28–31", Q4: "24–22" },
        pace: "98.4",
      },
      {
        game_id: "2026-demo-den-gsw",
        date: "2026-06-12",
        home_name: "掘金",
        home_abbreviation: "DEN",
        away_name: "勇士",
        away_abbreviation: "GSW",
        home_score: 103,
        away_score: 99,
        status: "final",
        series_game_number: null,
        start_utc: "2026-06-11T21:30:00Z",
      },
      {
        game_id: "2026-demo-lal-nyk",
        date: "2026-06-12",
        home_name: "湖人",
        home_abbreviation: "LAL",
        away_name: "尼克斯",
        away_abbreviation: "NYK",
        home_score: 112,
        away_score: 106,
        status: "final",
        series_game_number: null,
        start_utc: "2026-06-11T18:00:00Z",
      },
    ],
    "2026-06-10": {
      game_id: "2026-finals-g3",
      date: "2026-06-10",
      home_name: "雷霆",
      home_abbreviation: "OKC",
      away_name: "凯尔特人",
      away_abbreviation: "BOS",
      home_score: 101,
      away_score: 112,
      series_game_number: 3,
      status: "final",
      start_utc: "2026-06-10T01:30:00Z",
    },
    "2026-06-08": {
      game_id: "2026-finals-g2",
      date: "2026-06-08",
      home_name: "雷霆",
      home_abbreviation: "OKC",
      away_name: "凯尔特人",
      away_abbreviation: "BOS",
      home_score: 107,
      away_score: 99,
      series_game_number: 2,
      status: "final",
      start_utc: "2026-06-08T01:30:00Z",
    },
    "2026-06-06": {
      game_id: "2026-finals-g1",
      date: "2026-06-06",
      home_name: "凯尔特人",
      home_abbreviation: "BOS",
      away_name: "雷霆",
      away_abbreviation: "OKC",
      home_score: 118,
      away_score: 110,
      series_game_number: 1,
      status: "final",
      start_utc: "2026-06-06T01:30:00Z",
    },
    "2026-05-20": {
      game_id: "2026-regular-bos-okc",
      date: "2026-05-20",
      home_name: "凯尔特人",
      home_abbreviation: "BOS",
      away_name: "雷霆",
      away_abbreviation: "OKC",
      home_score: 106,
      away_score: 103,
      status: "final",
      start_utc: "2026-05-20T00:00:00Z",
    },
  };

  // Only the local G4 snapshot currently has a text replay.  API highlights
  // can contain other games; those cards still update the scoreboard, while
  // the replay deliberately enters a truthful "暂无逐回合记录" state instead
  // of showing G4 events for the wrong game.
  const PBP_BY_GAME = {
    "2026-finals-g4": PBP,
  };

  const TOKEN_PALETTE = Object.freeze({
    bos: "token-bos",
    okc: "token-okc",
    lal: "token-lal",
    nyk: "token-nyk",
    den: "token-den",
    gsw: "token-gsw",
  });

  const KNOWN_TOKEN_CLASSES = ["token-neutral", ...Object.values(TOKEN_PALETTE)];

  function formatShortDate(value) {
    return String(value || "").replaceAll("-", "/");
  }

  function tokenClass(abbreviation) {
    const key = String(abbreviation || "").trim().toLowerCase();
    return TOKEN_PALETTE[key] || "token-neutral";
  }

  function setTeamToken(node, abbreviation) {
    if (!node) return;
    node.classList.remove(...KNOWN_TOKEN_CLASSES);
    node.classList.add(tokenClass(abbreviation));
    node.textContent = abbreviation || "—";
  }

  function statusLabel(status) {
    return {
      final: "FINAL",
      live: "LIVE",
      scheduled: "SCHEDULED",
      postponed: "POSTPONED",
    }[String(status || "").toLowerCase()] || "UNKNOWN";
  }

  function loadSessionId() {
    try {
      const existing = window.sessionStorage.getItem(STORAGE_KEY);
      // The API contract requires a UUID session id. Older demo builds used
      // a human-readable fallback (for example session-...); discard those
      // values instead of sending an invalid id after a browser upgrade.
      if (existing && isUuid(existing)) return existing;
    } catch (_error) {
      // Private browsing can disable storage; an in-memory id is sufficient.
    }
    const created = makeId("session");
    try {
      window.sessionStorage.setItem(STORAGE_KEY, created);
    } catch (_error) {
      // Ignore storage failures.
    }
    return created;
  }

  function makeId(prefix) {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // session_id is sent to FastAPI as a UUID. Keep the fallback RFC-4122
    // shaped even in older/insecure browsers where randomUUID is unavailable;
    // the prefix is intentionally ignored for wire compatibility.
    const bytes = Array.from({ length: 16 }, () => Math.floor(Math.random() * 256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = bytes.map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function isUuid(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value || ""));
  }
  function updateClock() {
    if (!el.time) return;
    const value = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date());
    el.time.textContent = value;
  }

  function showToast(message) {
    if (!el.toast) return;
    window.clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      el.toast.hidden = true;
    }, 2800);
  }

  function nearBottom() {
    if (!el.chatLog) return true;
    return el.chatLog.scrollHeight - el.chatLog.scrollTop - el.chatLog.clientHeight < 80;
  }

  function scrollChat(force = false) {
    if (!el.chatLog || (!force && !nearBottom())) return;
    el.chatLog.scrollTo({ top: el.chatLog.scrollHeight, behavior: "smooth" });
  }

  function setConnection(status, label) {
    if (!el.connectionState) return;
    el.connectionState.dataset.state = status;
    el.connectionLabel.textContent = label;
  }

  function setAuthGate(visible, message = "") {
    if (!el.authGate) return;
    el.authGate.hidden = !visible;
    if (el.logout) el.logout.hidden = !state.authEnabled || !state.authenticated;
    if (el.authError) {
      el.authError.textContent = message;
      el.authError.hidden = !message;
    }
    if (visible) {
      window.setTimeout(() => el.authPassword?.focus(), 0);
    }
  }

  function requireLogin(message = "请先登录后再访问该服务。") {
    state.authenticated = false;
    state.authEnabled = true;
    setComposerBusy(state.streaming);
    setAuthGate(true, message);
    setConnection("ready", "需要登录");
  }

  async function submitLogin() {
    if (!el.authPassword || !window.CourtsideApi?.login) return;
    const password = el.authPassword.value;
    if (!password) {
      setAuthGate(true, "请输入访问密码。");
      el.authPassword.focus();
      return;
    }
    if (el.authSubmit) el.authSubmit.disabled = true;
    setAuthGate(true, "正在验证密码…");
    try {
      await window.CourtsideApi.login(password);
      state.authenticated = true;
      state.authBootstrapped = true;
      el.authPassword.value = "";
      setAuthGate(false);
      setComposerBusy(false);
      setConnection("ready", "API 就绪");
      detectApi();
    } catch (error) {
      if (error?.network) {
        setAuthGate(true, "登录服务暂时不可用，请稍后重试。");
      } else {
        setAuthGate(true, error?.message || "密码不正确。");
      }
    } finally {
      if (el.authSubmit) el.authSubmit.disabled = false;
    }
  }

  async function bootstrapAuth() {
    if (!window.CourtsideApi?.baseUrl || !window.CourtsideApi?.authStatus) {
      state.authBootstrapped = true;
      state.authenticated = true;
      setAuthGate(false);
      detectApi();
      return;
    }
    try {
      const status = await window.CourtsideApi.authStatus();
      state.authEnabled = Boolean(status?.enabled);
      state.authenticated = !state.authEnabled || Boolean(status?.authenticated);
      state.authBootstrapped = true;
      if (state.authEnabled && !state.authenticated) {
        setComposerBusy(false);
        setAuthGate(true);
        setConnection("ready", "需要登录");
        return;
      }
      setAuthGate(false);
      detectApi();
    } catch (error) {
      // A rolling deployment may serve the new page from an API image that
      // predates the auth route. Treat a 404 as the old, unprotected demo;
      // real network failures still leave the offline UI usable.
      if (error?.status === 404 || error?.network) {
        state.authEnabled = false;
        state.authenticated = true;
        state.authBootstrapped = true;
        setAuthGate(false);
        detectApi();
        return;
      }
      requireLogin(error?.message || "登录服务暂时不可用。");
    }
  }

  function setTransportLabel(available) {
    const live = Boolean(available);
    if (el.modeLabel) {
      el.modeLabel.textContent = live ? "LIVE API" : "FIXTURE MODE";
      el.modeLabel.parentElement?.setAttribute(
        "title",
        live ? "已连接 NBA Agent API" : "当前为本地 fixture 演示数据",
      );
    }
    if (el.onlineLabel) {
      if (el.onlineLabelText) {
        el.onlineLabelText.textContent = live ? "API READY" : "OFFLINE DEMO";
      }
      el.onlineLabel.parentElement?.setAttribute(
        "title",
        live ? "已连接本地 NBA Agent API" : "当前使用内置 fixture 演示数据",
      );
    }
  }

  function setComposerBusy(busy) {
    state.streaming = busy;
    const locked = state.authEnabled && !state.authenticated;
    el.input.disabled = busy || locked;
    el.sendButton.disabled = locked;
    el.sendButton.classList.toggle("is-stop", busy);
    el.sendButton.setAttribute("aria-label", busy ? "停止生成" : "发送问题");
    el.sendButton.innerHTML = busy
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10v10H7z" /></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>';
  }

  function setStreamStatus(visible, text) {
    if (!el.streamStatus) return;
    el.streamStatus.hidden = !visible;
    if (text) el.streamStage.textContent = text;
    el.chatLog.setAttribute("aria-busy", visible ? "true" : "false");
    // The compact status region announces progress.  Suppress token-by-token
    // announcements in the log while a stream is active, then restore the
    // polite live region for the terminal answer.
    el.chatLog.setAttribute("aria-live", visible ? "off" : "polite");
  }

  function autoGrowInput() {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 130)}px`;
  }

  function updateCharCount() {
    const length = el.input.value.length;
    el.charCount.textContent = `${length} / 2000`;
    el.charCount.classList.toggle("near-limit", length >= 1700 && length < 2000);
    el.charCount.classList.toggle("at-limit", length >= 2000);
  }

  function appendUserMessage(text) {
    const article = document.createElement("article");
    article.className = "message user-message dynamic-message";
    article.dataset.messageId = makeId("user");

    const avatar = document.createElement("div");
    avatar.className = "message-avatar user-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "YOU";

    const body = document.createElement("div");
    body.className = "message-body";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "YOU";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    meta.append(name, time);

    const bubble = document.createElement("div");
    bubble.className = "user-bubble";
    bubble.textContent = text;
    body.append(meta, bubble);
    article.append(avatar, body);
    el.chatLog.append(article);
    scrollChat(true);
    return article;
  }

  function createAssistantPlaceholder() {
    const article = document.createElement("article");
    article.className = "message assistant-message dynamic-message";
    article.dataset.messageId = makeId("assistant");

    const avatar = document.createElement("div");
    avatar.className = "message-avatar assistant-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "CS";

    const body = document.createElement("div");
    body.className = "message-body";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "COURTSIDE";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    meta.append(name, time);

    const bubble = document.createElement("div");
    bubble.className = "user-bubble streaming-bubble";
    bubble.setAttribute("aria-label", "正在生成回答");
    body.append(meta, bubble);
    article.append(avatar, body);
    el.chatLog.append(article);
    scrollChat(true);
    return { article, avatar, body, meta, bubble };
  }

  function currentShortTime() {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date());
  }

  function beijingDateString(date = new Date()) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }

  // -------------------------------------------------------------------------
  // History date picker
  // -------------------------------------------------------------------------
  // Native <input type=date> controls cannot disable individual calendar days.
  // The small calendar below keeps that input as a wire/accessibility fallback
  // while rendering an explicit availability state for every visible day.
  // Availability is conservative: an API date that has not been checked is
  // shown as “待核验” and is disabled, never guessed to be an empty day.

  const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  function isIsoDate(value) {
    if (!ISO_DATE_RE.test(String(value || ""))) return false;
    const [year, month, day] = String(value).split("-").map(Number);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return parsed.getUTCFullYear() === year
      && parsed.getUTCMonth() === month - 1
      && parsed.getUTCDate() === day;
  }

  function monthKeyForDate(value) {
    if (!isIsoDate(value)) return state.calendarMonth || "2026-06";
    return String(value).slice(0, 7);
  }

  function monthParts(monthKey) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(monthKey || ""));
    if (!match) return { year: 2026, month: 6 };
    return { year: Number(match[1]), month: Number(match[2]) };
  }

  function normalizeMonthKey(monthKey) {
    const { year, month } = monthParts(monthKey);
    const normalized = new Date(Date.UTC(year, month - 1, 1));
    return `${normalized.getUTCFullYear()}-${String(normalized.getUTCMonth() + 1).padStart(2, "0")}`;
  }

  function shiftMonth(monthKey, offset) {
    const { year, month } = monthParts(monthKey);
    const shifted = new Date(Date.UTC(year, month - 1 + Number(offset || 0), 1));
    return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
  }

  function datesInMonth(monthKey) {
    const { year, month } = monthParts(monthKey);
    const first = new Date(Date.UTC(year, month - 1, 1));
    const count = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const leading = first.getUTCDay();
    return [
      ...Array.from({ length: leading }, () => null),
      ...Array.from({ length: count }, (_item, index) => {
        return `${year}-${String(month).padStart(2, "0")}-${String(index + 1).padStart(2, "0")}`;
      }),
    ];
  }

  function monthDateRange(monthKey) {
    const { year, month } = monthParts(monthKey);
    const from = `${year}-${String(month).padStart(2, "0")}-01`;
    const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const to = `${year}-${String(month).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
    return { from, to };
  }

  function fixtureDateSet() {
    return new Set(Object.keys(HIGHLIGHT_FIXTURES));
  }

  function fixtureGamesForDate(dateValue) {
    const value = HIGHLIGHT_FIXTURES[dateValue];
    if (Array.isArray(value)) return value;
    return value ? [value] : [];
  }

  function allFixtureGames() {
    return Object.keys(HIGHLIGHT_FIXTURES).flatMap((dateValue) => fixtureGamesForDate(dateValue));
  }

  function seedFixtureAvailability() {
    fixtureDateSet().forEach((dateValue) => {
      state.highlightAvailability.set(dateValue, "available");
    });
  }

  function availabilityForDate(dateValue) {
    if (!isIsoDate(dateValue)) return "unknown";
    if (dateValue > beijingDateString()) return "future";
    const known = state.highlightAvailability.get(dateValue);
    if (known) return known;
    // The fixture snapshot is complete for offline mode. In API mode, leave
    // unseen dates explicitly unknown until the calendar verifies them.
    return state.apiAvailable ? "unknown" : "empty";
  }

  function recordHighlightAvailability(dateValue, hasGames, source = "api") {
    if (!isIsoDate(dateValue)) return;
    if (source === "fixture") {
      state.highlightAvailability.set(dateValue, hasGames ? "available" : "empty");
      return;
    }
    // API responses are authoritative, including an empty game list. Never
    // turn transport/provider errors into an empty result.
    state.highlightAvailability.set(dateValue, hasGames ? "available" : "empty");
  }

  function calendarStatusCopy() {
    if (!state.apiAvailable) return "演示数据：灰色日期无比赛，不可选择";
    const values = datesInMonth(state.calendarMonth)
      .filter(Boolean)
      .map((dateValue) => availabilityForDate(dateValue));
    if (values.includes("loading")) return "正在核对本月赛事…";
    if (values.includes("unknown") || values.includes("error")) {
      return "尚未核验的日期暂不可选，核对完成后会自动开放";
    }
    return "灰色日期无比赛，不可选择";
  }

  function calendarDayLabel(dateValue, status) {
    const [, month, day] = String(dateValue).split("-");
    const suffix = {
      available: "有比赛",
      empty: "无比赛，不可选",
      future: "未来日期，不可选",
      loading: "正在核对",
      unknown: "尚未核验",
      error: "暂时无法核验",
    }[status] || "不可选";
    return `${Number(month)}月${Number(day)}日，${suffix}`;
  }

  function renderCalendar(monthKey = state.calendarMonth) {
    if (!el.calendarGrid) return;
    state.calendarMonth = normalizeMonthKey(monthKey);
    const { year, month } = monthParts(state.calendarMonth);
    if (el.calendarMonth) el.calendarMonth.textContent = `${year} 年 ${month} 月`;
    el.calendarGrid.textContent = "";
    const selected = state.highlightDate;
    datesInMonth(state.calendarMonth).forEach((dateValue) => {
      if (!dateValue) {
        const spacer = document.createElement("span");
        spacer.className = "calendar-day-spacer";
        spacer.setAttribute("aria-hidden", "true");
        el.calendarGrid.append(spacer);
        return;
      }
      const day = document.createElement("button");
      const status = availabilityForDate(dateValue);
      day.type = "button";
      day.className = `calendar-day calendar-day-${status}`;
      day.dataset.date = dateValue;
      day.dataset.availability = status;
      day.setAttribute("role", "gridcell");
      day.setAttribute("aria-label", calendarDayLabel(dateValue, status));
      day.textContent = String(Number(dateValue.slice(-2)));
      if (dateValue === selected) {
        day.classList.add("selected");
        day.setAttribute("aria-selected", "true");
      } else {
        day.setAttribute("aria-selected", "false");
      }
      if (dateValue === beijingDateString()) day.classList.add("today");
      if (status !== "available") {
        day.disabled = true;
        day.setAttribute("aria-disabled", "true");
      }
      day.addEventListener("click", () => selectCalendarDate(dateValue));
      el.calendarGrid.append(day);
    });

    const currentMonth = monthKeyForDate(beijingDateString());
    if (el.calendarNext) {
      const nextDisabled = state.calendarMonth >= currentMonth;
      el.calendarNext.disabled = nextDisabled;
      el.calendarNext.setAttribute("aria-disabled", nextDisabled ? "true" : "false");
    }
    if (el.calendarPrev) el.calendarPrev.disabled = false;
    if (el.calendarStatusText) el.calendarStatusText.textContent = calendarStatusCopy();
  }

  function calendarScanIsCurrent(monthMarker, token) {
    return state.calendarScanRequests.get(monthMarker) === token;
  }

  function renderCalendarIfVisible(monthMarker) {
    if (state.calendarMonth === monthMarker) renderCalendar(monthMarker);
  }

  async function verifyCalendarDatesLegacy(monthMarker, candidates, token) {
    if (typeof window.CourtsideApi?.highlights !== "function") return false;
    let cursor = 0;
    const verify = async () => {
      while (cursor < candidates.length) {
        const index = cursor;
        cursor += 1;
        try {
          const payload = await window.CourtsideApi.highlights(candidates[index], "Asia/Shanghai");
          if (!calendarScanIsCurrent(monthMarker, token)) return;
          recordHighlightAvailability(candidates[index], Boolean(payload?.games?.length));
        } catch (_error) {
          if (!calendarScanIsCurrent(monthMarker, token)) return;
          state.highlightAvailability.set(candidates[index], "error");
        }
        renderCalendarIfVisible(monthMarker);
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, candidates.length) }, verify));
    return true;
  }

  async function ensureCalendarAvailability(monthKey = state.calendarMonth, force = false) {
    state.calendarMonth = normalizeMonthKey(monthKey);
    const monthMarker = state.calendarMonth;
    if (!state.apiAvailable || !window.CourtsideApi) {
      datesInMonth(monthMarker).forEach((dateValue) => {
        if (!dateValue) return;
        if (!state.highlightAvailability.has(dateValue)) {
          state.highlightAvailability.set(dateValue, fixtureDateSet().has(dateValue) ? "available" : "empty");
        }
      });
      renderCalendar(monthMarker);
      return;
    }
    if (!force && state.calendarScanMonths.has(monthMarker)) {
      renderCalendarIfVisible(monthMarker);
      return;
    }
    // Keep an in-flight request per month. Navigating to another month no
    // longer invalidates this request, so returning to the month can reuse its
    // result instead of getting stuck in a permanent loading state. A forced
    // retry explicitly replaces the prior request for that month.
    if (!force && state.calendarScanRequests.has(monthMarker)) {
      renderCalendarIfVisible(monthMarker);
      return;
    }
    if (force) {
      state.calendarScanMonths.delete(monthMarker);
      state.calendarScanRequests.delete(monthMarker);
    }
    const token = ++state.calendarScanToken;
    state.calendarScanRequests.set(monthMarker, token);
    const today = beijingDateString();
    const visibleDates = datesInMonth(monthMarker).filter(Boolean);
    const candidates = visibleDates.filter((dateValue) => {
      if (dateValue > today) return false;
      const status = state.highlightAvailability.get(dateValue);
      return !status || status === "unknown" || status === "error" || (force && status === "loading");
    });
    if (!candidates.length) {
      if (calendarScanIsCurrent(monthMarker, token)) {
        state.calendarScanRequests.delete(monthMarker);
        state.calendarScanMonths.add(monthMarker);
        renderCalendarIfVisible(monthMarker);
      }
      return;
    }
    candidates.forEach((dateValue) => state.highlightAvailability.set(dateValue, "loading"));
    renderCalendarIfVisible(monthMarker);
    const range = monthDateRange(monthMarker);
    let verified = false;
    try {
      // The bounded availability projection answers a whole calendar month in
      // one provider pass. It also carries an explicit `unknown` state, so a
      // timeout/partial response cannot be painted as a confirmed empty day.
      if (typeof window.CourtsideApi.highlightsAvailability === "function") {
        const payload = await window.CourtsideApi.highlightsAvailability(
          range.from,
          range.to,
          "Asia/Shanghai",
        );
        if (!calendarScanIsCurrent(monthMarker, token)) return;
        const returned = new Set();
        (Array.isArray(payload?.days) ? payload.days : []).forEach((item) => {
          if (!isIsoDate(item?.date) || !visibleDates.includes(item.date)) return;
          returned.add(item.date);
          if (item.is_future) {
            state.highlightAvailability.set(item.date, "future");
          } else if (["available", "empty", "unknown"].includes(item.status)) {
            state.highlightAvailability.set(item.date, item.status);
          } else {
            state.highlightAvailability.set(item.date, "unknown");
          }
        });
        // A malformed/incomplete successful response is not proof of no games.
        candidates.forEach((dateValue) => {
          if (!returned.has(dateValue) && dateValue <= today) {
            state.highlightAvailability.set(dateValue, "unknown");
          }
        });
        // Keep unknown days disabled, but allow a later calendar open to retry
        // a partial provider response instead of caching uncertainty forever.
        verified = !candidates.some((dateValue) => ["unknown", "error", "loading"]
          .includes(state.highlightAvailability.get(dateValue)));
      } else {
        // Older API deployments may not have the range endpoint yet. Keep the
        // conservative per-day fallback for compatibility, with a small
        // concurrency cap to avoid a request storm.
        await verifyCalendarDatesLegacy(monthMarker, candidates, token);
        if (!calendarScanIsCurrent(monthMarker, token)) return;
        verified = !candidates.some((dateValue) => ["unknown", "error", "loading"]
          .includes(state.highlightAvailability.get(dateValue)));
      }
    } catch (error) {
      if (!calendarScanIsCurrent(monthMarker, token)) return;
      // A rolling deployment can serve the new static page from an older API
      // image for a short period. If the range route is missing, retry through
      // the established date-scoped endpoint; all other failures remain
      // visibly distinct from a verified empty day.
      if (error?.status === 404 && typeof window.CourtsideApi.highlights === "function") {
        await verifyCalendarDatesLegacy(monthMarker, candidates, token);
        if (!calendarScanIsCurrent(monthMarker, token)) return;
        verified = !candidates.some((dateValue) => ["unknown", "error", "loading"]
          .includes(state.highlightAvailability.get(dateValue)));
      } else {
        if (error?.authRequired) {
          requireLogin("登录已失效，请重新登录。");
          state.calendarScanRequests.delete(monthMarker);
          return;
        }
        candidates.forEach((dateValue) => {
          if (dateValue <= today) state.highlightAvailability.set(dateValue, "error");
        });
      }
    }
    if (!calendarScanIsCurrent(monthMarker, token)) return;
    state.calendarScanRequests.delete(monthMarker);
    if (verified) state.calendarScanMonths.add(monthMarker);
    else state.calendarScanMonths.delete(monthMarker);
    renderCalendarIfVisible(monthMarker);
  }

  function syncHighlightDatePicker(mode, dateValue) {
    if (!el.highlightDatePicker) return;
    const visible = mode === "history";
    el.highlightDatePicker.hidden = !visible;
    if (el.highlightDateLabel) el.highlightDateLabel.hidden = !visible;
    if (mode === "history" && isIsoDate(dateValue)) state.historyDate = dateValue;
    const safeDate = mode === "history"
      ? (isIsoDate(dateValue) ? dateValue : state.historyDate)
      : (state.historyDate || dateValue || state.highlightDate);
    if (el.highlightDate) {
      el.highlightDate.value = safeDate || "";
      // The custom button is the visible control; retain the native input for
      // browsers/users that disable script.
      el.highlightDate.hidden = true;
    }
    if (el.highlightDateValue) el.highlightDateValue.textContent = formatShortDate(safeDate);
    if (visible) {
      state.calendarMonth = monthKeyForDate(safeDate);
      renderCalendar(state.calendarMonth);
      ensureCalendarAvailability(state.calendarMonth);
    } else {
      closeCalendar();
    }
  }

  function openCalendar() {
    if (!el.highlightCalendar || !el.highlightDateTrigger) return;
    state.calendarOpen = true;
    el.highlightCalendar.hidden = false;
    el.highlightDateTrigger.setAttribute("aria-expanded", "true");
    renderCalendar(state.calendarMonth);
    // A partial or failed month remains non-selectable, but reopening the
    // picker gives it an explicit retry path without re-fetching months that
    // have already been verified as available/empty.
    const needsRetry = datesInMonth(state.calendarMonth)
      .filter(Boolean)
      .some((dateValue) => ["unknown", "error"].includes(availabilityForDate(dateValue)));
    ensureCalendarAvailability(state.calendarMonth, needsRetry);
  }

  function closeCalendar() {
    state.calendarOpen = false;
    if (el.highlightCalendar) el.highlightCalendar.hidden = true;
    if (el.highlightDateTrigger) el.highlightDateTrigger.setAttribute("aria-expanded", "false");
  }

  function selectCalendarDate(dateValue) {
    const status = availabilityForDate(dateValue);
    if (status !== "available") {
      showToast(status === "loading" || status === "unknown"
        ? "正在核对该日期是否有比赛，请稍候"
        : status === "error"
          ? "该日期暂时无法核验，请稍后重试"
          : status === "future"
            ? "不能选择未来日期"
            : "这一天没有比赛，暂不可选");
      return;
    }
    selectHighlightDate(dateValue);
    closeCalendar();
  }

  function bindCalendarDocumentEvents() {
    document.addEventListener("click", (event) => {
      if (!state.calendarOpen || !el.highlightDatePicker) return;
      if (!el.highlightDatePicker.contains(event.target)) closeCalendar();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.calendarOpen) {
        closeCalendar();
        el.highlightDateTrigger?.focus();
      }
    });
  }

  // Small pure helpers are exposed for browser smoke tests and for future
  // embedders that want to render the same calendar without the full chat UI.
  window.CourtsideDateUtils = Object.freeze({
    isIsoDate,
    monthKeyForDate,
    normalizeMonthKey,
    shiftMonth,
    datesInMonth,
  });

  function createTextWithBold(text) {
    const fragment = document.createDocumentFragment();
    const pieces = String(text ?? "").split(/(\*\*[^*]+\*\*)/g);
    pieces.forEach((piece) => {
      if (piece.startsWith("**") && piece.endsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = piece.slice(2, -2);
        fragment.append(strong);
      } else {
        fragment.append(document.createTextNode(piece));
      }
    });
    return fragment;
  }

  function appendBlock(container, block, index) {
    if (!block || typeof block !== "object") return;
    const type = String(block.type || "text").toLowerCase();
    if (!["text", "analysis", "warning", "clarification", "no_data", "table", "fact"].includes(type)) return;

    if (type === "fact") {
      let grid = $(".fact-grid.dynamic-facts", container);
      if (!grid) {
        grid = document.createElement("div");
        grid.className = "fact-grid dynamic-facts answer-block";
        grid.setAttribute("aria-label", "关键事实");
        container.append(grid);
      }
      const card = document.createElement("div");
      card.className = `fact-card accent-${["green", "orange", "blue"][index % 3]}`;
      const label = document.createElement("span");
      label.className = "fact-label";
      label.textContent = block.label || "事实";
      const value = document.createElement("strong");
      value.className = "fact-value";
      value.textContent = block.value === null || block.value === undefined ? "暂无数据" : String(block.value);
      const unit = document.createElement("span");
      unit.className = "fact-unit";
      unit.textContent = block.unit || "";
      card.append(label, value, unit);
      grid.append(card);
      return;
    }

    if (type === "table") {
      const section = document.createElement("section");
      section.className = "answer-block table-block";
      if (block.label) {
        const label = document.createElement("div");
        label.className = "block-label";
        label.textContent = block.label;
        section.append(label);
      }
      const wrap = document.createElement("div");
      wrap.className = "answer-table-wrap";
      const table = document.createElement("table");
      table.className = "answer-table";
      const caption = document.createElement("caption");
      caption.className = "sr-only";
      caption.textContent = block.label || "数据表格";
      table.append(caption);
      const columns = Array.isArray(block.columns) ? block.columns : [];
      const rows = Array.isArray(block.rows) ? block.rows : [];
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      columns.forEach((column) => {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = String(column ?? "");
        headRow.append(th);
      });
      thead.append(headRow);
      const tbody = document.createElement("tbody");
      rows.forEach((row) => {
        if (!Array.isArray(row)) return;
        const tr = document.createElement("tr");
        columns.forEach((_column, columnIndex) => {
          const td = document.createElement("td");
          const value = row[columnIndex];
          td.textContent = value === null || value === undefined ? "暂无数据" : String(value);
          tr.append(td);
        });
        tbody.append(tr);
      });
      table.append(thead, tbody);
      wrap.append(table);
      section.append(wrap);
      container.append(section);
      return;
    }

    const section = document.createElement("section");
    section.className = `answer-block ${type === "no_data" ? "no-data" : type}-block`;
    const label = document.createElement("div");
    label.className = "block-label";
    label.textContent = block.label || (
      type === "analysis" ? "分析" :
      type === "clarification" ? "需要补充条件" :
      type === "no_data" ? "暂无匹配记录" :
      type === "warning" ? "提示" : "事实"
    );
    const text = document.createElement("p");
    text.className = "block-text";
    text.append(createTextWithBold(block.content || ""));
    section.append(label, text);
    container.append(section);
  }

  function evidenceLabel(value) {
    const normalized = String(value || "none").toLowerCase();
    if (normalized === "verified") return { text: "已核验", className: "verified", icon: "✓" };
    if (normalized === "partial") return { text: "部分核验", className: "partial", icon: "△" };
    return { text: "暂无数据", className: "none", icon: "—" };
  }

  function renderSimpleMarkdown(container, markdown) {
    const text = String(markdown || "").trim();
    if (!text) return;
    text.split(/\n{2,}|\n/).filter(Boolean).forEach((line) => {
      const paragraph = document.createElement("p");
      paragraph.className = "assistant-plain answer-block";
      paragraph.append(createTextWithBold(line.replace(/^[-*]\s+/, "")));
      container.append(paragraph);
    });
  }

  function renderError(container, response, retryable) {
    const card = document.createElement("div");
    card.className = "error-card dynamic-error";
    const label = document.createElement("div");
    label.className = "block-label";
    label.textContent = "连接状态";
    const text = document.createElement("p");
    text.textContent = response?.error?.message || "服务暂时不可用，请稍后重试。";
    card.append(label, text);
    const actions = document.createElement("div");
    actions.className = "error-actions";
    if (retryable) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "retry-button";
      retry.textContent = "重试";
      retry.addEventListener("click", () => retryLastRequest());
      actions.append(retry);
    }
    const ref = document.createElement("span");
    ref.className = "request-reference";
    ref.textContent = response?.request_id ? `请求 ${String(response.request_id).slice(0, 8)}` : "";
    actions.append(ref);
    card.append(actions);
    container.append(card);
  }

  function renderCompletedAnswer(placeholder, response) {
    if (!placeholder || !response) return;
    const body = placeholder.body;
    body.textContent = "";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "COURTSIDE";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    const evidence = evidenceLabel(response.evidence_state);
    const mark = document.createElement("span");
    mark.className = "verified-mark";
    mark.textContent = `${evidence.icon} ${evidence.text}`;
    meta.append(name, time, mark);

    const card = document.createElement("div");
    card.className = "answer-card dynamic-answer";

    if (Array.isArray(response.corrections) && response.corrections.length) {
      response.corrections.forEach((correction) => {
        appendBlock(card, {
          type: correction.status === "unverified" ? "warning" : "text",
          label: correction.status === "unverified" ? "待核验" : "核验结果",
          content: correction.message,
        }, 0);
      });
    }

    if (response.status === "blocked") {
      appendBlock(card, { type: "warning", label: "安全提示", content: response.answer_markdown }, 0);
    } else if (response.status === "needs_clarification") {
      appendBlock(card, { type: "clarification", content: response.answer_markdown }, 0);
    } else if (response.status === "no_data") {
      appendBlock(card, { type: "no_data", content: response.answer_markdown }, 0);
    } else if (Array.isArray(response.blocks) && response.blocks.length) {
      response.blocks.forEach((block, index) => appendBlock(card, block, index));
    } else {
      renderSimpleMarkdown(card, response.answer_markdown);
    }

    if (response.follow_up) {
      const follow = document.createElement("button");
      follow.type = "button";
      follow.className = "follow-up-button";
      follow.textContent = `继续追问：${response.follow_up}`;
      follow.addEventListener("click", () => {
        el.input.value = response.follow_up;
        updateCharCount();
        autoGrowInput();
        el.input.focus();
      });
      card.append(follow);
    }

    const foot = document.createElement("div");
    foot.className = "answer-foot";
    const chip = document.createElement("span");
    chip.className = `evidence-chip ${evidence.className}`;
    chip.append(document.createTextNode(`${evidence.icon} ${evidence.text}`));
    const asOf = document.createElement("span");
    asOf.textContent = response.as_of_beijing
      ? `公开资料 · 数据截至北京时间 ${response.as_of_beijing}`
      : "公开资料 · 当前没有可用的时间口径";
    foot.append(chip, asOf);
    card.append(foot);
    // `body` remains attached to the article while streaming.  Reusing the
    // same nodes keeps focus/scroll behaviour stable and avoids duplicate
    // assistant messages when the terminal envelope arrives.
    body.append(meta, card);
    if (placeholder.article && !placeholder.article.parentElement) {
      placeholder.article.append(placeholder.avatar || createAvatar("assistant-avatar", "CS"), body);
      el.chatLog.append(placeholder.article);
    }
  }

  function createAvatar(className, text) {
    const avatar = document.createElement("div");
    avatar.className = `message-avatar ${className}`;
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = text;
    return avatar;
  }

  function renderErrorAnswer(placeholder, response) {
    const body = placeholder.body;
    body.textContent = "";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "COURTSIDE";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    meta.append(name, time);
    body.append(meta);
    renderError(body, response, Boolean(response?.error?.retryable));
  }

  function updateStreamingText(run, text) {
    if (!run || !run.placeholder) return;
    run.partialText += text;
    run.placeholder.bubble.textContent = run.partialText;
    scrollChat();
  }

  function finishRun(response) {
    const run = state.run;
    if (!run) return;
    run.timers.forEach((timer) => window.clearTimeout(timer));
    if (run.placeholder) {
      if (response.status === "failed") renderErrorAnswer(run.placeholder, response);
      else renderCompletedAnswer(run.placeholder, response);
      run.placeholder.article.querySelector(".streaming-bubble")?.classList.remove("streaming-bubble");
    }
    state.lastRequest = { message: run.message, clientMessageId: run.clientMessageId };
    if (response.status !== "failed") {
      state.contextRequest = { message: run.message, clientMessageId: run.clientMessageId };
    }
    state.retryCount = response.status === "failed" ? state.retryCount : 0;
    state.run = null;
    setComposerBusy(false);
    setStreamStatus(false);
    setConnection(response.status === "failed" ? "error" : "ready", response.status === "failed" ? "需要重试" : "就绪");
    if (document.activeElement === el.sendButton || document.activeElement === el.stopStream) el.input.focus();
    scrollChat(true);
  }

  function cancelRun(showMessage = true) {
    const run = state.run;
    if (!run) return;
    if (run.abortController) run.abortController.abort();
    run.timers.forEach((timer) => window.clearTimeout(timer));
    if (showMessage && run.placeholder) {
      run.placeholder.bubble.classList.remove("streaming-bubble");
      run.placeholder.bubble.textContent = "已停止生成。您可以继续提问，或点击重试。";
    }
    state.run = null;
    setComposerBusy(false);
    setStreamStatus(false);
    setConnection("ready", "就绪");
    if (showMessage) showToast("已停止本次回答");
  }

  function handleStreamEvent(eventName, payload) {
    const run = state.run;
    if (!run || !payload) return;
    if (eventName === "run.started") {
      run.started = true;
      run.requestId = payload.request_id || run.requestId;
      setConnection("working", "处理中");
      return;
    }
    if (eventName === "run.status") {
      const stage = STAGE_COPY[payload.stage] || "正在处理问题";
      setStreamStatus(true, stage);
      return;
    }
    if (eventName === "message.delta") {
      updateStreamingText(run, payload.text || "");
      return;
    }
    if (eventName === "clarification.required") {
      run.branch = "clarification";
      setStreamStatus(true, "需要一个补充条件");
      return;
    }
    if (eventName === "safety.blocked") {
      run.branch = "blocked";
      setStreamStatus(true, "正在给出安全提示");
      return;
    }
    if (eventName === "run.error") {
      finishRun({ ...payload, status: "failed" });
      return;
    }
    if (eventName === "message.completed") {
      finishRun(payload);
    }
  }

  function schedule(run, callback, delay) {
    const timer = window.setTimeout(() => {
      if (state.run === run) callback();
    }, delay);
    run.timers.push(timer);
  }

  function createDemoResponse(message, options = {}) {
    const text = String(message || "").trim();
    const requestId = makeId("request");
    const base = {
      request_id: requestId,
      session_id: state.sessionId,
      latency_ms: 1680,
      as_of_beijing: "2026-06-13 11:42",
      evidence_state: "verified",
      corrections: [],
      follow_up: null,
    };

    if (/(博彩|下注|盘口|赔率|赌球|假球|黑哨|政治|涉华|社会争议|场外|绯闻|隐私|法律|犯罪|司法|人身攻击|辱骂|侮辱|歧视|仇恨)/i.test(text)) {
      return {
        ...base,
        status: "blocked",
        evidence_state: "none",
        as_of_beijing: null,
        answer_markdown: "这个话题不属于赛事助手的讨论范围。您可以问我比赛、球员或球队数据。",
        blocks: [],
        follow_up: "G4 最后 5 秒发生了什么？",
      };
    }

    // These explicit demo probes exercise the retry/error states before the
    // out-of-scope classifier below (the probe text itself need not mention
    // basketball).
    if (!options.forceSuccess && /断线|网络|连接中断/i.test(text)) {
      return {
        ...base,
        status: "failed",
        error: { code: "UPSTREAM_TIMEOUT", retryable: true, message: "数据连接暂时中断，请稍后重试。" },
      };
    }

    if (!options.forceSuccess && /超时|服务忙|稍后/i.test(text)) {
      return {
        ...base,
        status: "failed",
        error: { code: "SERVICE_BUSY", retryable: true, message: "服务当前较忙，请稍后重试。" },
      };
    }

    const contextShorthand = /那场|最后那个球|上一条|刚才/i.test(text);
    if (contextShorthand && !state.contextRequest) {
      return {
        ...base,
        status: "needs_clarification",
        evidence_state: "none",
        as_of_beijing: null,
        answer_markdown: "请告诉我具体的比赛或日期，我再为您核对。",
        blocks: [],
        follow_up: "例如：2025-26 总决赛 G4",
      };
    }

    if (contextShorthand && state.contextRequest) {
      return {
        ...base,
        status: "completed",
        answer_markdown: "我沿用上一轮的 G4 比赛上下文：凯尔特人以 108–104 击败雷霆，系列赛大比分为 3–1。",
        blocks: [
          { type: "text", content: "已沿用上一轮确定的比赛：**2025-26 总决赛 G4**。" },
          { type: "fact", label: "当前上下文", value: "BOS vs OKC", unit: "FINALS · G4" },
          { type: "fact", label: "终场比分", value: "108–104", unit: "BOS 胜" },
        ],
        follow_up: "请回放全场最后 5 秒发生了什么？",
      };
    }

    if (!options.forceSuccess && (/(天气|股票|写代码|旅游|食谱|电影推荐)/i.test(text) || (!/(nba|比赛|球员|球队|凯尔特人|雷霆|总决赛|系列赛|回合|战术|防守|比分|得分|冠军|布朗|塔图姆|亚历山大|霍姆格伦|怀特|多尔特|celtics|thunder|okc|bos|tatum|brown|gilgeous)/i.test(text) && text.length > 0))) {
      return {
        ...base,
        status: "no_data",
        evidence_state: "none",
        as_of_beijing: null,
        answer_markdown: "我专注于 NBA 比赛、球员和球队信息。换个篮球问题，我来帮您查。",
        blocks: [],
        follow_up: "这轮系列赛目前大比分是多少？",
      };
    }

    if (/最后|关键|5\s*秒|回合|play[- ]?by[- ]?play/i.test(text)) {
      const events = PBP.Q4.slice(-3);
      return {
        ...base,
        status: "completed",
        answer_markdown: "全场最后 5 秒没有新的有效投篮命中，终场比分定格为 108–104。",
        blocks: [
          { type: "text", content: "全场最后 5 秒的关键节点如下，时间按该节剩余时间记录。" },
          { type: "table", label: "Q4 · 最后 5 秒", columns: ["时间", "球队", "事件", "比分"], rows: events.map((event) => [event.clock, event.team, event.action, `${event.away}–${event.home}`]) },
          { type: "analysis", label: "分析", content: "凯尔特人用一次及时暂停和连续防守篮板消耗了最后回合，雷霆未能再获得完整出手机会。" },
        ],
        follow_up: "要不要看看第三节的转折回合？",
      };
    }

    if (/战术|限制|防守|复盘|为什么|失利|伟大/i.test(text)) {
      return {
        ...base,
        status: "completed",
        answer_markdown: "先说结论：凯尔特人通过换防与弱侧协防，降低了雷霆挡拆后的直线突破效率。",
        blocks: [
          { type: "text", content: "先说结论：凯尔特人通过换防与弱侧协防，降低了雷霆挡拆后的直线突破效率。" },
          { type: "fact", label: "对手核心", value: "S. Gilgeous-Alexander", unit: "27 PTS" },
          { type: "fact", label: "末节分差", value: "+4", unit: "BOS" },
          { type: "analysis", label: "分析", content: "1）挡拆第一道防线延误持球；2）弱侧提前收缩，迫使传球路线变长；3）轮转后优先保护篮下，再接受低效率外线出手。以上是基于已核验回合的战术解读。" },
        ],
        follow_up: "需要我按回合拆解最后一节吗？",
      };
    }

    if (/记得|赢了|输了|核验|对吗|是不是/i.test(text)) {
      return {
        ...base,
        status: "completed",
        corrections: [{ status: "corrected", message: "核验结果：G4 的胜者是凯尔特人，不是雷霆。" }],
        answer_markdown: "凯尔特人以 108–104 获胜，系列赛总比分来到 3–1。",
        blocks: [
          { type: "text", content: "凯尔特人以 **108–104** 获胜，系列赛总比分来到 **3–1**。" },
          { type: "table", label: "G4 比赛摘要", columns: ["项目", "结果"], rows: [["胜者", "凯尔特人"], ["得分王", "J. Brown · 32 分"], ["比赛状态", "FINAL"]] },
        ],
        follow_up: "要不要继续看最后 5 秒的逐回合记录？",
      };
    }

    return {
      ...base,
      status: "completed",
      answer_markdown: "凯尔特人在主场以 108–104 击败雷霆。杰伦·布朗得到全场最高的 32 分，凯尔特人将系列赛优势扩大到 3–1。",
      blocks: [
        { type: "text", content: "凯尔特人在主场以 **108–104** 击败雷霆。" },
        { type: "fact", label: "全场最高", value: "J. Brown", unit: "32 PTS" },
        { type: "fact", label: "系列赛大比分", value: "BOS 3–1", unit: "领先" },
        { type: "table", label: "比赛摘要（单节得分）", columns: ["球队", "Q1", "Q2", "Q3", "Q4", "终场"], rows: [["雷霆", 27, 25, 28, 24, 104], ["凯尔特人", 24, 31, 31, 22, 108]] },
      ],
      follow_up: "请回放全场最后 5 秒发生了什么？",
    };
  }

  function startDemoRun(message, options = {}) {
    if (state.streaming) return;
    const clientMessageId = options.clientMessageId || makeId("client");
    if (!options.reuseUser) appendUserMessage(message);
    const placeholder = createAssistantPlaceholder();
    const response = createDemoResponse(message, options);
    const run = {
      message,
      clientMessageId,
      requestId: response.request_id,
      response,
      placeholder,
      partialText: "",
      timers: [],
      started: false,
      branch: null,
    };
    state.run = run;
    setComposerBusy(true);
    // Show an actionable transport state immediately.  The server may take a
    // few seconds to emit its first progress event while the live model is
    // selected; “正在准备” looked like a stuck page during that window.
    setStreamStatus(true, "正在连接服务");
    setConnection("working", "连接中");

    schedule(run, () => handleStreamEvent("run.started", { request_id: response.request_id, session_id: state.sessionId }), 80);

    if (response.status === "blocked") {
      schedule(run, () => handleStreamEvent("safety.blocked", { request_id: response.request_id }), 310);
      schedule(run, () => handleStreamEvent("message.completed", response), 630);
      return;
    }

    if (response.status === "needs_clarification") {
      schedule(run, () => handleStreamEvent("run.status", { stage: "parsing" }), 260);
      schedule(run, () => handleStreamEvent("clarification.required", { request_id: response.request_id }), 550);
      schedule(run, () => handleStreamEvent("message.completed", response), 820);
      return;
    }

    if (response.status === "failed") {
      schedule(run, () => handleStreamEvent("run.status", { stage: "retrieving" }), 280);
      schedule(run, () => handleStreamEvent("run.error", { request_id: response.request_id, session_id: state.sessionId, error: response.error }), 780);
      return;
    }

    schedule(run, () => handleStreamEvent("run.status", { stage: "parsing" }), 220);
    schedule(run, () => handleStreamEvent("run.status", { stage: "retrieving" }), 560);
    schedule(run, () => handleStreamEvent("run.status", { stage: "verifying" }), 900);
    schedule(run, () => handleStreamEvent("run.status", { stage: "composing" }), 1160);

    // Deltas begin after verification.  The final envelope remains the source
    // of truth and replaces this draft when `message.completed` arrives.
    const draft = response.answer_markdown || "正在整理已核验事实……";
    const chunks = chunkText(draft, 18);
    chunks.forEach((chunk, index) => {
      schedule(run, () => handleStreamEvent("message.delta", { text: chunk }), 1320 + index * 45);
    });
    schedule(run, () => handleStreamEvent("message.completed", response), 1380 + chunks.length * 45);
  }

  function startApiRun(message, options = {}) {
    if (state.streaming || !window.CourtsideApi) return false;
    const clientMessageId = options.clientMessageId || makeId("client");
    if (!options.reuseUser) appendUserMessage(message);
    const placeholder = createAssistantPlaceholder();
    const controller = new AbortController();
    const run = {
      message,
      clientMessageId,
      requestId: null,
      placeholder,
      partialText: "",
      timers: [],
      started: false,
      branch: null,
      live: true,
      abortController: controller,
    };
    state.run = run;
    setComposerBusy(true);
    setStreamStatus(true, "正在连接服务");
    setConnection("working", "连接中");

    // If the first SSE frame is delayed by a proxy or a cold model connection,
    // keep the user informed instead of leaving the initial label unchanged.
    // ``finishRun`` clears this timer together with the rest of the run state.
    schedule(run, () => {
      if (!run.started && state.run === run) {
        setStreamStatus(true, "服务响应较慢，仍在等待");
      }
    }, 2500);

    window.CourtsideApi.streamChat({
      message,
      sessionId: state.sessionId,
      clientMessageId,
      signal: controller.signal,
      onEvent: (eventName, payload) => handleStreamEvent(eventName, payload),
    }).then(() => {
      // A healthy server always emits a terminal event.  If a proxy closes the
      // stream early, surface a retryable error instead of leaving the input
      // locked in a perpetual loading state.
      if (state.run === run) {
        finishRun({
          request_id: run.requestId || makeId("request"),
          session_id: state.sessionId,
          status: "failed",
          error: { code: "UPSTREAM_TIMEOUT", retryable: true, message: "数据连接暂时中断，请稍后重试。" },
        });
      }
    }).catch((error) => {
      if (controller.signal.aborted || state.run !== run) return;
      if (error?.authRequired) {
        state.run = null;
        placeholder.article.remove();
        setComposerBusy(false);
        setStreamStatus(false);
        requireLogin("登录已失效，请重新登录。");
        return;
      }
      // Auto-fallback is intentionally limited to transport failures.  A
      // valid API error must remain visible so the user can retry it.
      if (error?.network !== false) {
        // Stop sending subsequent questions into the same broken transport.
        // A page reload/probe can re-enable the API, while the current session
        // remains immediately usable through the deterministic fixture.
        state.apiAvailable = false;
        state.apiProbeComplete = true;
        state.run = null;
        placeholder.article.remove();
        setComposerBusy(false);
        setStreamStatus(false);
        setTransportLabel(false);
        setConnection("ready", "离线演示");
        showToast("API 暂不可用，已切换到离线演示");
        startDemoRun(message, { reuseUser: true, clientMessageId });
        return;
      }
      finishRun(error.publicPayload || {
        request_id: run.requestId || makeId("request"),
        session_id: state.sessionId,
        status: "failed",
        error: { code: "SERVICE_BUSY", retryable: true, message: error.message || "服务暂时不可用，请稍后重试。" },
      });
    });
    return true;
  }

  function startRequest(message, options = {}) {
    if (state.authEnabled && !state.authenticated) {
      requireLogin();
      return false;
    }
    if (!options.forceDemo && state.apiAvailable && window.CourtsideApi) {
      return startApiRun(message, options);
    }
    startDemoRun(message, options);
    return true;
  }

  function chunkText(text, size) {
    const chunks = [];
    for (let index = 0; index < text.length; index += size) chunks.push(text.slice(index, index + size));
    return chunks.length ? chunks : [""];
  }

  function submitCurrentInput() {
    if (state.authEnabled && !state.authenticated) {
      requireLogin();
      return;
    }
    const message = el.input.value.trim();
    if (!message) {
      showToast("请先输入一个 NBA 问题");
      el.input.focus();
      return;
    }
    if (state.streaming) return;
    el.input.value = "";
    updateCharCount();
    autoGrowInput();
    startRequest(message);
  }

  function retryLastRequest() {
    if (!state.lastRequest || state.streaming) return;
    const dynamic = $$(".dynamic-message");
    const lastAssistant = dynamic.filter((item) => item.classList.contains("assistant-message")).pop();
    if (lastAssistant) lastAssistant.remove();
    state.retryCount += 1;
    showToast("正在使用同一请求重试");
    startRequest(state.lastRequest.message, {
      reuseUser: true,
      clientMessageId: state.lastRequest.clientMessageId,
      // Let the offline demo demonstrate a successful recovery while still
      // preserving the same idempotency key exposed by the real contract.
      forceSuccess: state.retryCount > 0,
    });
  }

  function newSession() {
    if (state.streaming) cancelRun(false);
    state.sessionId = makeId("session");
    state.lastRequest = null;
    state.contextRequest = null;
    state.retryCount = 0;
    try {
      window.sessionStorage.setItem(STORAGE_KEY, state.sessionId);
    } catch (_error) {
      // Ignore storage failures.
    }
    $$(".dynamic-message").forEach((message) => message.remove());
    showToast("已开始新对话，会话上下文已隔离");
    el.input.value = "";
    updateCharCount();
    autoGrowInput();
    el.input.focus();
  }

  function resetHud() {
    setTeamToken(el.hudAwayToken, "—");
    setTeamToken(el.hudHomeToken, "—");
    if (el.hudAwayName) el.hudAwayName.textContent = "暂无比赛";
    if (el.hudHomeName) el.hudHomeName.textContent = "暂无比赛";
    if (el.hudAwayRecord) el.hudAwayRecord.textContent = "客场";
    if (el.hudHomeRecord) el.hudHomeRecord.textContent = "主场";
    if (el.hudEyebrow) el.hudEyebrow.textContent = "GAME HUD / NO GAME SELECTED";
    if (el.hudStatus) el.hudStatus.textContent = "NO DATA";
    if (el.awayScore) el.awayScore.textContent = "—";
    if (el.homeScore) el.homeScore.textContent = "—";
    if (el.scoreClock) el.scoreClock.textContent = "—";
    if (el.hudPossession) el.hudPossession.textContent = "最后控球：—";
    if (el.hudPace) el.hudPace.textContent = "节奏 —";
    el.quarterCells.forEach((cell) => {
      const score = $("[data-quarter-score]", cell);
      if (score) score.textContent = "—";
    });
  }

  function applyGameToHud(game) {
    const gameNumber = game.series_game_number ? `G${game.series_game_number}` : "GAME";
    const pbp = pbpForGame(game.game_id);
    const fixture = allFixtureGames().find((item) => item.game_id === game.game_id);
    const quarterScores = game.quarter_scores || fixture?.quarter_scores || {};
    setTeamToken(el.hudAwayToken, game.away_abbreviation || "AWAY");
    setTeamToken(el.hudHomeToken, game.home_abbreviation || "HOME");
    if (el.hudAwayName) el.hudAwayName.textContent = game.away_name || "客队";
    if (el.hudHomeName) el.hudHomeName.textContent = game.home_name || "主队";
    if (el.hudAwayRecord) el.hudAwayRecord.textContent = "客场";
    if (el.hudHomeRecord) el.hudHomeRecord.textContent = "主场";
    if (el.hudEyebrow) el.hudEyebrow.textContent = `GAME HUD / ${gameNumber}`;
    if (el.hudStatus) el.hudStatus.textContent = statusLabel(game.status);
    if (el.awayScore) el.awayScore.textContent = game.away_score == null ? "—" : String(game.away_score);
    if (el.homeScore) el.homeScore.textContent = game.home_score == null ? "—" : String(game.home_score);
    if (el.scoreClock) el.scoreClock.textContent = statusLabel(game.status) === "FINAL" ? "00:00 · FINAL" : statusLabel(game.status);
    if (el.hudPossession) el.hudPossession.textContent = "最后控球：—";
    const pace = game.pace ?? fixture?.pace;
    if (el.hudPace) el.hudPace.textContent = pace == null ? "节奏 —" : `节奏 ${pace}`;
    if (pbp) {
      const allEvents = Object.values(pbp).flat();
      const last = allEvents[allEvents.length - 1];
      if (last) {
        if (el.hudPossession) el.hudPossession.textContent = `最后控球：${last.team}`;
      }
    }
    el.quarterCells.forEach((cell) => {
      const period = cell.dataset.quarter;
      const score = $("[data-quarter-score]", cell);
      if (!score) return;
      score.textContent = quarterScores[period] || "—";
    });
  }

  function normalizeHighlightGames(games) {
    const seen = new Set();
    return (Array.isArray(games) ? games : []).filter((game) => {
      if (!game || typeof game !== "object") return false;
      const id = String(game.game_id || "").trim();
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }

  function pbpForGame(gameId) {
    const key = String(gameId || "");
    return Object.prototype.hasOwnProperty.call(PBP_BY_GAME, key) ? PBP_BY_GAME[key] : null;
  }

  function gameHasPbp(game) {
    const pbp = game ? pbpForGame(game.game_id) : null;
    return Boolean(pbp && Object.values(pbp).some((events) => Array.isArray(events) && events.length));
  }

  function defaultPbpPeriod(pbp) {
    // Prefer the final period for a familiar replay entry point, then fall
    // back to the latest populated period.  A game without PBP still opens on
    // Q4 so the empty state explains the missing source honestly.
    return ["Q4", "Q3", "Q2", "Q1", "OT"].find((period) => {
      return Array.isArray(pbp?.[period]) && pbp[period].length;
    }) || "Q4";
  }

  function formatGameStart(startUtc) {
    if (!startUtc) return "时间待定";
    const parsed = new Date(startUtc);
    if (Number.isNaN(parsed.getTime())) return "时间待定";
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(parsed);
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
    );
    if (!values.month || !values.day || !values.hour || !values.minute) return "时间待定";
    return `${values.month}/${values.day} ${values.hour}:${values.minute}`;
  }

  function gameStatusClass(status) {
    const normalized = String(status || "unknown").toLowerCase();
    return ["live", "final", "scheduled", "postponed"].includes(normalized)
      ? normalized
      : "unknown";
  }

  function updateGameListSelection() {
    if (!el.gameList) return;
    $$(".game-list-card", el.gameList).forEach((card) => {
      const selected = card.dataset.gameId === String(state.selectedGameId || "");
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-pressed", selected ? "true" : "false");
      card.setAttribute("aria-current", selected ? "true" : "false");
    });
  }

  function renderGameList(games) {
    if (!el.gameList) return;
    const values = normalizeHighlightGames(games);
    el.gameList.textContent = "";
    if (el.gameListCount) el.gameListCount.textContent = `${String(values.length).padStart(2, "0")} 场`;
    if (el.gamesSection) el.gamesSection.hidden = !values.length;
    if (!values.length) return;

    values.forEach((game, index) => {
      const gameId = String(game.game_id);
      const card = document.createElement("button");
      card.type = "button";
      card.className = `game-list-card game-list-card-${gameStatusClass(game.status)}`;
      card.dataset.gameId = gameId;
      card.setAttribute("aria-label", `查看${game.away_name || "客队"}对${game.home_name || "主队"}的比赛`);
      card.addEventListener("click", () => selectActiveGame(gameId, { announce: true }));

      const head = document.createElement("span");
      head.className = "game-list-card-head";
      const order = document.createElement("span");
      order.className = "game-list-order";
      order.textContent = String(index + 1).padStart(2, "0");
      const start = document.createElement("span");
      start.className = "game-list-time";
      start.textContent = formatGameStart(game.start_utc);
      const status = document.createElement("span");
      status.className = "game-list-status";
      status.textContent = statusLabel(game.status);
      head.append(order, start, status);

      const matchup = document.createElement("span");
      matchup.className = "game-list-matchup";
      const away = document.createElement("span");
      away.className = "game-list-team game-list-away";
      const awayToken = document.createElement("span");
      awayToken.className = `team-token ${tokenClass(game.away_abbreviation)}`;
      awayToken.textContent = game.away_abbreviation || "AWY";
      const awayName = document.createElement("span");
      awayName.className = "game-list-team-name";
      awayName.textContent = game.away_name || "客队";
      away.append(awayToken, awayName);
      const awayScore = document.createElement("strong");
      awayScore.className = "game-list-score";
      awayScore.textContent = game.away_score == null ? "—" : String(game.away_score);
      const divider = document.createElement("span");
      divider.className = "game-list-score-divider";
      divider.textContent = "–";
      const homeScore = document.createElement("strong");
      homeScore.className = "game-list-score";
      homeScore.textContent = game.home_score == null ? "—" : String(game.home_score);
      const home = document.createElement("span");
      home.className = "game-list-team game-list-home";
      const homeName = document.createElement("span");
      homeName.className = "game-list-team-name";
      homeName.textContent = game.home_name || "主队";
      const homeToken = document.createElement("span");
      homeToken.className = `team-token ${tokenClass(game.home_abbreviation)}`;
      homeToken.textContent = game.home_abbreviation || "HOM";
      home.append(homeName, homeToken);
      matchup.append(away, awayScore, divider, homeScore, home);

      const foot = document.createElement("span");
      foot.className = "game-list-card-foot";
      const coverage = document.createElement("span");
      coverage.className = `game-list-coverage ${gameHasPbp(game) ? "has-pbp" : "no-pbp"}`;
      coverage.textContent = gameHasPbp(game) ? "文字回放" : "暂无 PBP";
      const hint = document.createElement("span");
      hint.className = "game-list-hint";
      hint.textContent = String(gameId).startsWith("2026-demo-") ? "DEMO" : "查看 HUD";
      foot.append(coverage, hint);
      card.append(head, matchup, foot);
      el.gameList.append(card);
    });
    updateGameListSelection();
  }

  function renderFeaturedGame(game, mode, dateValue) {
    if (!game || !el.featuredGame) return;
    const safeDate = dateValue || state.highlightDate || "";
    el.featuredGame.hidden = false;
    el.featuredGame.setAttribute("aria-label", `查看${game.home_name || "主队"}与${game.away_name || "客队"}的比赛回放`);
    setTeamToken(el.featuredHomeToken, game.home_abbreviation || "HOME");
    setTeamToken(el.featuredAwayToken, game.away_abbreviation || "AWAY");
    if (el.featuredHomeName) el.featuredHomeName.textContent = game.home_name || "主队";
    if (el.featuredAwayName) el.featuredAwayName.textContent = game.away_name || "客队";
    if (el.featuredGameMeta) {
      const gameNumber = game.series_game_number ? `G${game.series_game_number}` : "GAME";
      const demoLabel = String(game.game_id || "").startsWith("2026-demo-") ? " · DEMO" : "";
      el.featuredGameMeta.textContent = `NBA · ${gameNumber}${demoLabel} · ${String(game.game_id || "").toUpperCase()}`;
    }
    if (el.featuredGameState) el.featuredGameState.textContent = statusLabel(game.status);
    const scores = $$(".mini-scoreboard > strong", el.featuredGame);
    if (scores[0]) scores[0].textContent = game.home_score == null ? "—" : String(game.home_score);
    if (scores[1]) scores[1].textContent = game.away_score == null ? "—" : String(game.away_score);
    if (el.featuredGameFoot) {
      const todayLabel = safeDate === beijingDateString() ? "今日赛事" : "演示赛事";
      el.featuredGameFoot.textContent = `${formatShortDate(safeDate)} · ${mode === "history" ? "历史回顾" : todayLabel}`;
    }
  }

  function selectActiveGame(gameId, options = {}) {
    const targetId = String(gameId || "");
    const game = state.highlightGames.find((item) => String(item.game_id) === targetId);
    if (!game) return false;
    state.activeGame = game;
    state.selectedGameId = targetId;
    state.activePbp = pbpForGame(targetId);
    updateGameListSelection();
    renderFeaturedGame(game, state.highlightMode, state.highlightDate);
    applyGameToHud(game);
    renderPbp(defaultPbpPeriod(state.activePbp));
    if (options.announce) {
      const matchup = `${game.away_name || "客队"} · ${game.home_name || "主队"}`;
      showToast(state.activePbp ? `已切换至 ${matchup}，可查看文字回放` : `已切换至 ${matchup}，该场暂无可用文字回放`);
    }
    return true;
  }

  function renderHighlightProjection(games, mode, dateValue) {
    const values = normalizeHighlightGames(games);
    const previousId = state.selectedGameId;
    const game = values.find((item) => String(item.game_id) === String(previousId || "")) || values[0];
    state.highlightGames = values;
    state.highlightMode = mode;
    state.highlightDate = dateValue;
    el.highlightModes.forEach((button) => {
      const active = button.dataset.highlightMode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const todayButton = el.highlightModes.find((button) => button.dataset.highlightMode === "today");
    const todayStatus = state.apiAvailable ? availabilityForDate(beijingDateString()) : "available";
    if (todayButton) {
      // Keep the mode switch reachable even on an off day: selecting “今日
      // 赛事” should show the truthful empty state rather than trapping the
      // user in history. Only individual calendar dates are non-selectable.
      todayButton.disabled = false;
      todayButton.classList.toggle("no-games", todayStatus === "empty");
      todayButton.setAttribute("aria-disabled", "false");
      if (todayStatus === "empty") todayButton.setAttribute("title", "今日没有比赛");
      else if (["unknown", "loading", "error"].includes(todayStatus)) todayButton.setAttribute("title", "今日赛程尚未核验");
      else todayButton.removeAttribute("title");
    }
    const safeDate = dateValue || state.highlightDate || "";
    syncHighlightDatePicker(mode, safeDate);
    if (el.highlightsTitle) {
      el.highlightsTitle.textContent = mode === "history" ? `历史回顾 · ${formatShortDate(safeDate)}` : "今日看点";
    }
    if (el.dayDivider) {
      const dividerText = mode === "history"
        ? `历史回顾 · ${formatShortDate(safeDate)}`
        : (state.apiAvailable && safeDate === beijingDateString()
          ? `今天 · ${formatShortDate(safeDate)}`
          : `演示日期 · ${formatShortDate(safeDate)}`);
      const label = $("span", el.dayDivider);
      if (label) label.textContent = dividerText;
    }
    if (!game) {
      state.activeGame = null;
      state.selectedGameId = null;
      state.activePbp = null;
      el.featuredGame.hidden = true;
      renderGameList([]);
      if (el.highlightsEmpty) {
        // Reset a transient validation message before rendering a normal
        // empty-date response.
        el.highlightsEmpty.textContent = "这一天暂无可用比赛记录。";
        el.highlightsEmpty.hidden = false;
      }
      resetHud();
      renderPbp("Q4");
      return;
    }
    state.activeGame = game;
    state.selectedGameId = String(game.game_id);
    state.activePbp = pbpForGame(game.game_id);
    renderGameList(values);
    renderFeaturedGame(game, mode, dateValue);
    if (el.highlightsEmpty) el.highlightsEmpty.hidden = true;
    applyGameToHud(game);
    renderPbp(defaultPbpPeriod(state.activePbp));
  }

  async function loadHighlights(mode, selectedDate) {
    const requestNumber = ++state.highlightRequest;
    const fallbackDate = selectedDate || "2026-06-12";
    // Record the requested mode synchronously so an API probe finishing later
    // cannot overwrite a user's in-flight history selection.
    state.highlightMode = mode;
    if (mode === "history" && selectedDate) {
      state.highlightDate = selectedDate;
      state.historyDate = selectedDate;
    }
    if (state.apiAvailable && window.CourtsideApi) {
      try {
        const payload = await window.CourtsideApi.highlights(
          mode === "today" ? null : selectedDate,
          "Asia/Shanghai",
        );
        if (requestNumber !== state.highlightRequest) return;
        const dateValue = payload?.date || selectedDate || beijingDateString();
        recordHighlightAvailability(dateValue, Boolean(payload?.games?.length));
        renderHighlightProjection(payload?.games || [], mode, dateValue);
        if (!payload?.games?.length && mode === "history") showToast("该日期暂无比赛记录");
        return;
      } catch (error) {
        if (requestNumber !== state.highlightRequest) return;
        if (error?.authRequired) {
          requireLogin("登录已失效，请重新登录。");
          clearHighlightProjection("请登录后查看赛事数据。");
          return;
        }
        const publicError = error?.publicPayload?.error;
        // A reachable API error is authoritative: clear stale cards and show
        // the server's safe message.  Only a transport failure may fall back
        // to the local fixture, and that state is labelled explicitly.
        if (error?.network === false) {
          // A future-date rejection must not leave the previously selected
          // game's card visible as if it belonged to the rejected/failed date.
          const message = publicError?.message || "日期赛事暂时不可用。";
          if (mode === "history" && selectedDate) {
            state.highlightAvailability.set(
              selectedDate,
              publicError?.code === "INVALID_PAYLOAD" ? "future" : "error",
            );
          }
          clearHighlightProjection(message);
          showToast(message);
          if (mode === "history" && el.highlightDate) {
            el.highlightDate.value = state.highlightDate;
          }
          return;
        }
        setTransportLabel(false);
        state.apiAvailable = false;
        // Drop in-flight/verified API availability when falling back to the
        // deterministic preview. Otherwise a late live response could mix
        // with fixture dates and make the calendar claim a stale state.
        state.highlightAvailability.clear();
        state.calendarScanMonths.clear();
        state.calendarScanRequests.clear();
        state.calendarScanToken += 1;
        seedFixtureAvailability();
      }
    }
    const fixtureGames = fixtureGamesForDate(fallbackDate);
    recordHighlightAvailability(fallbackDate, Boolean(fixtureGames.length), "fixture");
    renderHighlightProjection(fixtureGames, mode, fallbackDate);
    if (!fixtureGames.length && mode === "history") showToast("该日期暂无比赛记录");
    if (state.apiProbeComplete && !state.apiAvailable) {
      setConnection("ready", "离线演示");
    }
  }

  function setHighlightsMode(mode) {
    const selectedDate = mode === "today"
      ? (state.apiAvailable ? beijingDateString() : "2026-06-12")
      : (state.historyDate || el.highlightDate?.value || state.highlightDate || "2026-06-12");
    loadHighlights(mode, selectedDate);
  }

  function selectHighlightDate(value) {
    if (!value || !isIsoDate(value)) return;
    const today = beijingDateString();
    if (value > today) {
      // Keep the prior date selected for a predictable retry, but clear the
      // stale game card immediately and expose a persistent in-rail error.
      // Invalidate any older, still-pending highlights request so its response
      // cannot put the stale card back after this validation failure.
      state.highlightRequest += 1;
      state.highlightAvailability.set(value, "future");
      clearHighlightProjection("未来日期不可查询。");
      showToast("不能选择未来日期");
      if (el.highlightDate) el.highlightDate.value = state.highlightDate;
      renderCalendar(state.calendarMonth);
      return;
    }
    const availability = availabilityForDate(value);
    if (availability !== "available") {
      showToast(availability === "unknown" || availability === "loading"
        ? "正在核对该日期是否有比赛，请稍候"
        : availability === "error"
          ? "该日期暂时无法核验，请稍后重试"
          : "这一天没有比赛，暂不可选");
      return;
    }
    loadHighlights("history", value);
  }

  function clearHighlightProjection(message = "这一天暂无可用比赛记录。") {
    state.activeGame = null;
    state.activePbp = null;
    state.highlightGames = [];
    state.selectedGameId = null;
    if (el.featuredGame) el.featuredGame.hidden = true;
    renderGameList([]);
    if (el.highlightsEmpty) {
      el.highlightsEmpty.textContent = message;
      el.highlightsEmpty.hidden = false;
    }
    resetHud();
    renderPbp("Q4");
  }

  function currentPbp() {
    return state.activePbp || {};
  }

  function renderPbp(period, preserveIndex = false) {
    state.currentPeriod = period;
    const events = currentPbp()[period] || [];
    const requested = preserveIndex ? state.pbpIndex : events.length - 1;
    state.pbpIndex = Math.max(0, Math.min(requested, Math.max(events.length - 1, 0)));
    el.pbpList.textContent = "";
    el.eventCount.textContent = `${String(events.length).padStart(2, "0")} EVENTS`;
    el.pbpSlider.max = String(Math.max(events.length - 1, 0));
    el.pbpSlider.value = String(state.pbpIndex);
    el.pbpSlider.disabled = !events.length;
    el.replayPlay.disabled = !events.length;
    el.replayPlay.setAttribute("aria-disabled", events.length ? "false" : "true");
    stopReplay();

    if (!events.length) {
      const empty = document.createElement("div");
      empty.className = "pbp-empty";
      empty.setAttribute("role", "listitem");
      empty.setAttribute("aria-live", "polite");
      empty.textContent = !state.activeGame
        ? "暂无选中的比赛，先从左侧选择一场赛事。"
        : (period === "OT" ? "本场没有加时回合，终场在第四节结束时确定。" : "该场暂无可用的逐回合记录。");
      el.pbpList.append(empty);
      el.periodTabs.forEach((tab) => {
        const active = tab.dataset.period === period;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      updateQuarterHighlight(period);
      el.replayPosition.textContent = "00 / 00";
      el.selectedPlayText.textContent = empty.textContent;
      const game = state.activeGame;
      el.awayScore.textContent = game?.away_score == null ? "—" : String(game.away_score);
      el.homeScore.textContent = game?.home_score == null ? "—" : String(game.home_score);
      el.scoreClock.textContent = game
        ? (statusLabel(game.status) === "FINAL" ? "00:00 · FINAL" : statusLabel(game.status))
        : "—";
      return;
    }

    events.forEach((event, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "pbp-event";
      item.setAttribute("role", "listitem");
      item.dataset.index = String(index);
      item.setAttribute("aria-label", `${event.clock} ${event.player} ${event.action}`);

      const clock = document.createElement("span");
      clock.className = "pbp-time";
      clock.textContent = event.clock;
      const token = document.createElement("span");
      token.className = `pbp-team ${event.teamClass}`;
      token.textContent = event.team;
      const description = document.createElement("span");
      description.className = "pbp-description";
      const player = document.createElement("strong");
      player.textContent = event.player;
      const action = document.createElement("span");
      action.textContent = event.action;
      description.append(player, action);
      const score = document.createElement("span");
      score.className = "pbp-score";
      score.textContent = `${event.away}–${event.home}`;
      item.append(clock, token, description, score);
      el.pbpList.append(item);
    });

    el.periodTabs.forEach((tab) => {
      const active = tab.dataset.period === period;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    updateQuarterHighlight(period);
    selectPbp(state.pbpIndex, false);
  }

  function updateQuarterHighlight(period) {
    el.quarterCells.forEach((cell) => {
      const active = cell.dataset.quarter === period;
      cell.classList.toggle("current", active);
      cell.setAttribute("aria-current", active ? "true" : "false");
    });
  }

  function selectPbp(index, shouldScroll = false) {
    const events = currentPbp()[state.currentPeriod] || [];
    if (!events.length) return;
    state.pbpIndex = Math.max(0, Math.min(Number(index) || 0, events.length - 1));
    const event = events[state.pbpIndex];
    updateQuarterHighlight(state.currentPeriod);
    $$(".pbp-event", el.pbpList).forEach((item, itemIndex) => {
      const active = itemIndex === state.pbpIndex;
      item.classList.toggle("active", active);
      item.setAttribute("aria-current", active ? "true" : "false");
    });
    el.pbpSlider.value = String(state.pbpIndex);
    el.replayPosition.textContent = `${String(state.pbpIndex + 1).padStart(2, "0")} / ${String(events.length).padStart(2, "0")}`;
    el.selectedPlayText.textContent = `${event.player}：${event.action}。${event.detail}。`;
    el.awayScore.textContent = String(event.away);
    el.homeScore.textContent = String(event.home);
    el.scoreClock.textContent = `${event.clock} · ${state.currentPeriod}`;
    if (shouldScroll) {
      const active = $(".pbp-event.active", el.pbpList);
      active?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function stopReplay() {
    if (state.replayTimer) window.clearInterval(state.replayTimer);
    state.replayTimer = null;
    el.replayPlay.classList.remove("playing");
    el.replayPlay.setAttribute("aria-pressed", "false");
    el.replayLabel.textContent = "播放回放";
  }

  function toggleReplay() {
    if (state.replayTimer) {
      stopReplay();
      return;
    }
    const events = currentPbp()[state.currentPeriod] || [];
    if (!events.length) {
      showToast(!state.activeGame
        ? "请先选择一场比赛"
        : state.currentPeriod === "OT"
          ? "本场暂无加时回合"
          : "该场暂无可用的逐回合记录");
      return;
    }
    if (state.pbpIndex >= events.length - 1) selectPbp(0, true);
    el.replayPlay.classList.add("playing");
    el.replayPlay.setAttribute("aria-pressed", "true");
    el.replayLabel.textContent = "暂停回放";
    state.replayTimer = window.setInterval(() => {
      if (state.pbpIndex >= events.length - 1) {
        stopReplay();
        return;
      }
      selectPbp(state.pbpIndex + 1, true);
    }, 820);
  }

  function initSseParserDemo() {
    // Kept as a small, tested-in-browser parser for replacing the demo
    // transport with fetch('/api/v1/chat/stream') later.  Comments/heartbeats
    // are ignored and multi-line data fields are joined per SSE semantics.
    window.CourtsideSSEParser = class CourtsideSSEParser {
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
        const split = line.indexOf(":");
        const field = split === -1 ? line : line.slice(0, split);
        const value = split === -1 ? "" : line.slice(split + 1).replace(/^ /, "");
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
    };
  }

  async function detectApi() {
    if (!window.CourtsideApi) return;
    const available = await window.CourtsideApi.probe();
    state.apiAvailable = available;
    state.apiProbeComplete = true;
    setTransportLabel(available);
    if (available) {
      // The offline preview may have marked unknown dates as empty. Once the
      // API is reachable, discard every offline availability value (including
      // fixture hints) and rebuild the visible month from authoritative API
      // evidence; a fixture date must never suppress a live query.
      state.highlightAvailability.clear();
      state.calendarScanMonths.clear();
      // Invalidate availability requests started by the offline/previous
      // transport before rebuilding the calendar from the API response.
      state.calendarScanRequests.clear();
      state.calendarScanToken += 1;
      setConnection("ready", "API 就绪");
      // Replace the offline preview with the server's real local-day
      // projection as soon as the probe succeeds.  The fixture remains visible
      // while the probe is pending, but a connected “今日赛事” rail must not
      // silently show an old demo date.
      // The probe is asynchronous; do not reset a history view selected while
      // it was in flight.  ``loadHighlights`` records mode synchronously for
      // the same race window.
      if (state.highlightMode === "today") {
        loadHighlights("today", beijingDateString());
      } else {
        loadHighlights("history", state.historyDate);
        ensureCalendarAvailability(state.calendarMonth, true);
      }
    }
  }

  function bindEvents() {
    el.authForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      submitLogin();
    });
    el.logout?.addEventListener("click", async () => {
      try {
        await window.CourtsideApi?.logout();
      } catch (_error) {
        // The server-side session will expire independently; still lock the
        // local UI immediately when a user explicitly logs out.
      }
      requireLogin("您已退出登录。");
    });
    el.chatForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.streaming) {
        cancelRun();
        return;
      }
      submitCurrentInput();
    });

    el.input.addEventListener("input", () => {
      updateCharCount();
      autoGrowInput();
    });

    el.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submitCurrentInput();
      }
    });

    el.stopStream.addEventListener("click", () => cancelRun());
    el.sendButton.addEventListener("click", (event) => {
      if (state.streaming) {
        event.preventDefault();
        cancelRun();
      }
    });
    el.newSession.addEventListener("click", newSession);
    el.highlightModes.forEach((button) => {
      button.addEventListener("click", () => {
        if (button.disabled) return;
        setHighlightsMode(button.dataset.highlightMode || "today");
      });
    });
    el.highlightDate?.addEventListener("change", () => selectHighlightDate(el.highlightDate.value));
    el.highlightDateTrigger?.addEventListener("click", () => {
      if (state.calendarOpen) closeCalendar();
      else openCalendar();
    });
    el.calendarPrev?.addEventListener("click", () => {
      state.calendarMonth = shiftMonth(state.calendarMonth, -1);
      renderCalendar(state.calendarMonth);
      ensureCalendarAvailability(state.calendarMonth);
    });
    el.calendarNext?.addEventListener("click", () => {
      const next = shiftMonth(state.calendarMonth, 1);
      if (next > monthKeyForDate(beijingDateString())) return;
      state.calendarMonth = next;
      renderCalendar(state.calendarMonth);
      ensureCalendarAvailability(state.calendarMonth);
    });
    el.featuredGame.addEventListener("click", () => {
      if (!state.activeGame) return;
      renderPbp("Q4");
      document.querySelector(".pbp-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
      const gameNumber = state.activeGame.series_game_number ? `G${state.activeGame.series_game_number}` : "该场";
      showToast(state.activePbp ? `已定位到 ${gameNumber} 第四节文字回放` : `${gameNumber} 暂无可用文字回放`);
    });

    el.promptList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-prompt]");
      if (!button) return;
      const prompt = button.dataset.prompt || "";
      if (state.streaming) return;
      el.input.value = "";
      updateCharCount();
      autoGrowInput();
      startRequest(prompt);
    });

    el.pbpList.addEventListener("click", (event) => {
      const item = event.target.closest(".pbp-event");
      if (item) selectPbp(item.dataset.index, true);
    });
    el.periodTabs.forEach((tab, tabIndex) => {
      tab.addEventListener("click", () => {
        stopReplay();
        renderPbp(tab.dataset.period);
      });
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const nextIndex = (tabIndex + direction + el.periodTabs.length) % el.periodTabs.length;
        const next = el.periodTabs[nextIndex];
        next.focus();
        stopReplay();
        renderPbp(next.dataset.period);
      });
    });
    el.pbpSlider.addEventListener("input", () => selectPbp(el.pbpSlider.value, true));
    el.replayPlay.addEventListener("click", toggleReplay);

    window.addEventListener("beforeunload", () => stopReplay());
    bindCalendarDocumentEvents();
  }

  function init() {
    updateClock();
    window.setInterval(updateClock, 1000);
    updateCharCount();
    autoGrowInput();
    if (el.highlightDate) {
      el.highlightDate.max = beijingDateString();
      el.highlightDate.hidden = true;
    }
    seedFixtureAvailability();
    state.calendarMonth = monthKeyForDate(el.highlightDate?.value || state.highlightDate);
    renderPbp("Q4");
    setTransportLabel(false);
    renderHighlightProjection(fixtureGamesForDate("2026-06-12"), "today", "2026-06-12");
    initSseParserDemo();
    bindEvents();
    // Authenticate before probing highlights/chat. If uvicorn is not running,
    // the page remains a usable offline interaction demo; if a password is
    // configured, the gate blocks all data requests until login succeeds.
    bootstrapAuth();
  }

  init();
})();
