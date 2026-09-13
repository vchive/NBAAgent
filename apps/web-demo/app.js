/*
 * 种花 Agent / Flower Intelligence
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
    welcomeMessage: $(".welcome-message"),
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
    gamesSectionTitle: $("#games-section-title"),
    highlightsTitle: $("#highlights-title"),
    highlightsBadgeLabel: $("#highlights-badge-label"),
    highlightModes: $$("[data-highlight-mode]"),
    historyControls: $("#history-controls"),
    historyRecent: $("#history-recent"),
    historyCustom: $("#history-custom"),
    historyRangePicker: $("#history-range-picker"),
    historyFrom: $("#history-from"),
    historyTo: $("#history-to"),
    historyRangeApply: $("#history-range-apply"),
    historyStatus: $("#history-status"),
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
    recommendations: $("#recommendations"),
    recommendationList: $("#recommendation-list"),
    intelligenceMode: $("#intelligence-mode"),
    intelligenceHelp: $("#intelligence-help"),
    conversationScope: $("#conversation-scope"),
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
    hudLeaderRow: $("#hud-leader-row"),
    hudLeaderName: $("#hud-leader-name"),
    hudLeaderLine: $("#hud-leader-line"),
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

  const STORAGE_KEY = "flower-agent-session-v1";
  // A fixed snapshot is an opt-in development transport, never an implicit
  // recovery path for a public/live page.  It can be enabled either by the
  // server reporting `mode: fixture` or by setting this runtime value before
  // app.js loads in a standalone static demo.
  const EXPLICIT_RUNTIME_MODE = String(window.COURTSIDE_RUNTIME_MODE || "").trim().toLowerCase();
  const EXPLICIT_FIXTURE_MODE = ["demo", "fixture"].includes(EXPLICIT_RUNTIME_MODE);
  const STAGE_COPY = {
    understanding: "正在理解问题",
    checking: "正在核对相关信息",
    completing: "正在整理回答",
    processing: "正在处理请求",
  };

  // Recommendation prompts are a browser-side convenience only.  Keep the
  // alias map deliberately small and deterministic: it identifies the topic
  // already visible in the conversation, but never tries to infer a result.
  // Kept intentionally small: aliases are used only to make follow-up
  // suggestions useful in the browser.  Facts and plant identification stay
  // server-side, where the session context is authoritative.
  const PLANT_ALIASES = [
    ["月季", "玫瑰", "rose", "roses"],
    ["绣球", "八仙花", "hydrangea", "hydrangeas"],
    ["蝴蝶兰", "兰花", "orchid", "orchids"],
    ["君子兰", "clivia"],
    ["栀子", "栀子花", "gardenia"],
    ["茉莉", "jasmine"],
    ["长寿花", "kalanchoe"],
    ["绿萝", "pothos"],
    ["多肉", "succulent", "succulents"],
    ["向日葵", "sunflower", "sunflowers"],
    ["郁金香", "tulip", "tulips"],
    ["薰衣草", "lavender"],
  ];

  // Legacy aliases are retained privately for old, explicitly selected NBA
  // links.  They are never rendered by the default flower experience.
  const LEGACY_NBA_TEAM_ALIASES = [
    ["老鹰", "亚特兰大老鹰", "Hawks", "ATL"],
    ["凯尔特人", "波士顿凯尔特人", "Celtics", "BOS"],
    ["篮网", "布鲁克林篮网", "Nets", "BKN"],
    ["黄蜂", "夏洛特黄蜂", "Hornets", "CHA"],
    ["公牛", "芝加哥公牛", "Bulls", "CHI"],
    ["骑士", "克利夫兰骑士", "Cavaliers", "CLE"],
    ["独行侠", "达拉斯独行侠", "小牛", "Mavericks", "DAL"],
    ["掘金", "丹佛掘金", "Nuggets", "DEN"],
    ["活塞", "底特律活塞", "Pistons", "DET"],
    ["勇士", "金州勇士", "Warriors", "GSW"],
    ["火箭", "休斯顿火箭", "Rockets", "HOU"],
    ["步行者", "印第安纳步行者", "Pacers", "IND"],
    ["快船", "洛杉矶快船", "Clippers", "LAC"],
    ["湖人", "洛杉矶湖人", "Lakers", "LAL"],
    ["灰熊", "孟菲斯灰熊", "Grizzlies", "MEM"],
    ["热火", "迈阿密热火", "Heat", "MIA"],
    ["雄鹿", "密尔沃基雄鹿", "Bucks", "MIL"],
    ["森林狼", "明尼苏达森林狼", "Timberwolves", "MIN"],
    ["鹈鹕", "新奥尔良鹈鹕", "Pelicans", "NOP"],
    ["尼克斯", "纽约尼克斯", "Knicks", "NYK"],
    ["雷霆", "俄克拉荷马城雷霆", "Thunder", "OKC"],
    ["魔术", "奥兰多魔术", "Magic", "ORL"],
    ["76人", "费城76人", "Sixers", "PHI"],
    ["太阳", "菲尼克斯太阳", "Suns", "PHX"],
    ["开拓者", "波特兰开拓者", "Trail Blazers", "POR"],
    ["国王", "萨克拉门托国王", "Kings", "SAC"],
    ["马刺", "圣安东尼奥马刺", "Spurs", "SAS"],
    ["猛龙", "多伦多猛龙", "Raptors", "TOR"],
    ["爵士", "犹他爵士", "Jazz", "UTA"],
    ["奇才", "华盛顿奇才", "Wizards", "WAS"],
  ];

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
    pbpIndex: 0,
    replayTimer: null,
    run: null,
    streaming: false,
    toastTimer: null,
    lastRequest: null,
    contextRequest: null,
    gardenContext: null,
    retryCount: 0,
    recommendationContext: null,
    // The left rail starts unbound.  A game context is created only after the
    // user explicitly enters 赛事下钻 and clicks a game card.
    highlightMode: "roaming",
    highlightDate: "2026-06-12",
    historyDate: "2026-06-12",
    historyView: "recent",
    historyRangeFrom: "",
    historyRangeTo: "",
    historyLoading: false,
    historyLoadingTimer: null,
    activeGame: null,
    activePbp: null,
    highlightGames: [],
    selectedGameId: null,
    detailRequest: 0,
    detailLoadingGameId: null,
    gameDetails: new Map(),
    apiAvailable: false,
    apiProbeComplete: false,
    apiDataMode: EXPLICIT_FIXTURE_MODE ? "demo" : "unknown",
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
    intelligenceMode: "hybrid",
    defaultIntelligenceMode: "hybrid",
    fullIntelligenceEnabled: true,
  };

  function fixtureTransportEnabled() {
    return EXPLICIT_FIXTURE_MODE
      || (state.apiProbeComplete && state.apiDataMode === "demo");
  }

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
        venue_name: "TD Garden",
        venue_city: "Boston",
        status: "final",
        start_utc: "2026-06-12T01:30:00Z",
        quarter_scores: { Q1: "27–24", Q2: "25–31", Q3: "28–31", Q4: "24–22" },
        leaders: [{ player_name: "杰伦·布朗", points: 32, rebounds: 8, assists: 5 }],
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
        venue_name: "Ball Arena",
        venue_city: "Denver",
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
        venue_name: "Crypto.com Arena",
        venue_city: "Los Angeles",
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
      venue_name: "Paycom Center",
      venue_city: "Oklahoma City",
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
      venue_name: "Paycom Center",
      venue_city: "Oklahoma City",
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
      venue_name: "TD Garden",
      venue_city: "Boston",
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

  function setHistoryControls(mode, view = state.historyView) {
    const visible = mode === "history";
    const busy = visible && state.historyLoading;
    if (el.historyControls) el.historyControls.hidden = !visible;
    if (el.historyRecent) {
      const active = visible && view === "recent";
      el.historyRecent.classList.toggle("active", active);
      el.historyRecent.setAttribute("aria-pressed", active ? "true" : "false");
      el.historyRecent.disabled = false;
    }
    if (el.historyCustom) {
      const active = visible && view === "range";
      el.historyCustom.classList.toggle("active", active);
      el.historyCustom.setAttribute("aria-pressed", active ? "true" : "false");
      el.historyCustom.disabled = false;
    }
    if (el.historyRangePicker) el.historyRangePicker.hidden = !visible || view !== "range";
    if (el.historyRangeApply) el.historyRangeApply.disabled = busy;
  }

  function setHistoryStatus(message = "", visible = Boolean(message), tone = "info") {
    if (!el.historyStatus) return;
    el.historyStatus.textContent = message;
    el.historyStatus.hidden = !visible;
    el.historyStatus.dataset.state = tone;
  }

  function handleHighlightTransportFailure(message, mode = "history") {
    const publicMessage = safePublicErrorMessage(
      message,
      mode === "date" ? "日期资料服务暂时不可用。" : "园艺资料服务暂时不可用。",
    );
    state.historyLoading = false;
    setHistoryControls("history", state.historyView);
    clearHighlightProjection(publicMessage);
    setHistoryStatus(
      `${publicMessage} 可稍后重试。`,
      true,
      "error",
    );
    setConnection("error", "连接异常");
  }

  function setHistoryRangeDefaults() {
    const today = beijingDateString();
    const end = isIsoDate(state.historyRangeTo) && state.historyRangeTo <= today
      ? state.historyRangeTo
      : today;
    const startDate = new Date(`${end}T00:00:00Z`);
    startDate.setUTCDate(startDate.getUTCDate() - 6);
    const start = isIsoDate(state.historyRangeFrom) && state.historyRangeFrom <= end
      ? state.historyRangeFrom
      : startDate.toISOString().slice(0, 10);
    state.historyRangeFrom = start;
    state.historyRangeTo = end;
    if (el.historyFrom) el.historyFrom.value = start;
    if (el.historyTo) el.historyTo.value = end;
  }

  function cancelHighlightsLoading() {
    window.clearTimeout(state.historyLoadingTimer);
    state.historyLoadingTimer = null;
    state.historyLoading = false;
    setHistoryControls(state.highlightMode, state.historyView);
    if (el.historyStatus?.dataset.state === "loading") setHistoryStatus();
  }

  function renderHighlightsLoading(message, requestNumber) {
    // A persistent-cache hit normally returns before this threshold. Keep the
    // current cards visible during that short window and announce only one
    // loading state when the request is genuinely perceptible.
    cancelHighlightsLoading();
    state.historyLoadingTimer = window.setTimeout(() => {
      state.historyLoadingTimer = null;
      if (requestNumber !== state.highlightRequest) return;
      state.historyLoading = true;
      setHistoryControls(state.highlightMode, state.historyView);
      setHistoryStatus(message, true, "loading");
    }, 250);
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
        // Authentication adapters may pass through an upstream error. Keep
        // the gate limited to application-owned, public wording rather than
        // exposing provider URLs, quota details, or stack traces.
        setAuthGate(true, safePublicErrorMessage(error, "密码不正确。"));
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
      requireLogin(safePublicErrorMessage(error, "登录服务暂时不可用。"));
    }
  }

  function setTransportLabel(available, dataMode = state.apiDataMode) {
    const live = Boolean(available);
    const normalizedMode = String(dataMode || "unknown").toLowerCase();
    const checking = !live && !state.apiProbeComplete && Boolean(window.CourtsideApi?.baseUrl);
    const offlineFixture = !live && fixtureTransportEnabled();
    const modeText = normalizedMode === "live"
      ? "ONLINE DATA"
      : normalizedMode === "hybrid"
        ? "ONLINE + LOCAL"
        : normalizedMode === "demo"
          ? "LOCAL KNOWLEDGE"
          : "FLOWER SERVICE";
    if (el.modeLabel) {
      el.modeLabel.textContent = live
        ? modeText
        : checking
          ? "DATA SERVICE"
          : offlineFixture
            ? "LOCAL KNOWLEDGE"
            : "SERVICE OFFLINE";
      el.modeLabel.parentElement?.setAttribute(
        "title",
        live
          ? (normalizedMode === "demo" ? "已连接种花对话服务，使用本地知识" : "已连接园艺资料服务")
          : checking
            ? "正在检查数据服务"
            : offlineFixture
              ? "已显式启用本地花卉知识"
              : "园艺资料服务暂时不可用",
      );
    }
    if (el.onlineLabel) {
      if (el.onlineLabelText) {
        el.onlineLabelText.textContent = live
          ? "CONNECTED"
          : checking
            ? "CONNECTING"
            : offlineFixture
              ? "LOCAL ONLY"
              : "SERVICE OFFLINE";
      }
      el.onlineLabel.parentElement?.setAttribute(
        "title",
        live
          ? "已连接种花对话服务"
          : checking
            ? "正在连接对话服务"
            : offlineFixture
              ? "当前使用显式启用的本地花卉知识"
              : "种花服务暂时不可用",
      );
    }
  }

  function setIntelligenceCapability(enabled, defaultMode = null) {
    state.fullIntelligenceEnabled = Boolean(enabled);
    const normalizedDefault = String(defaultMode || "").toLowerCase();
    if (normalizedDefault === "full" || normalizedDefault === "hybrid") {
      state.defaultIntelligenceMode = normalizedDefault;
    }
    if (!el.intelligenceMode) return;
    el.intelligenceMode.disabled = Boolean(
      state.apiProbeComplete && state.apiAvailable && !enabled
    );
    if (el.intelligenceHelp) {
      el.intelligenceHelp.textContent = el.intelligenceMode.disabled
        ? "服务端未开启"
        : "智能回答 + 资料核验";
    }
    // Reflect the server default after the API probe so the first message
    // follows the mode shown by the switch. Offline preview remains hybrid.
    if (state.apiAvailable && state.fullIntelligenceEnabled) {
      state.intelligenceMode = state.defaultIntelligenceMode;
      el.intelligenceMode.checked = state.intelligenceMode === "full";
    }
  }

  function setWelcomeForTransport(dataMode) {
    if (!el.welcomeMessage) return;
    const normalized = String(dataMode || "unknown").toLowerCase();
    if (normalized === "demo") {
      setWelcomeForFixtureTransport();
      return;
    }
    // Do not present the fixed fixture scoreboard as today's live answer.
    const label = $(".answer-label", el.welcomeMessage);
    const title = $("h3", el.welcomeMessage);
    const copy = $(".answer-lead p", el.welcomeMessage);
    const grid = $(".fact-grid", el.welcomeMessage);
    const foot = $(".answer-foot", el.welcomeMessage);
    if (label) label.textContent = "服务已连接";
    if (title) title.textContent = "已连接园艺资料服务";
    if (copy) copy.textContent = "您可以询问选花、浇水、光照、施肥、修剪和病虫害。";
    if (grid) grid.hidden = true;
    if (foot) foot.textContent = "资料会标注更新时间与不确定性 · 等待你的问题";
  }

  function setWelcomeForFixtureTransport() {
    if (!el.welcomeMessage) return;
    const label = $(".answer-label", el.welcomeMessage);
    const title = $("h3", el.welcomeMessage);
    const copy = $(".answer-lead p", el.welcomeMessage);
    const grid = $(".fact-grid", el.welcomeMessage);
    const foot = $(".answer-foot", el.welcomeMessage);
    // Fixture transport is still a valid offline answer source, but it must
    // not look like a selected/current game while the app is in roaming mode.
    if (label) label.textContent = "漫游模式";
    if (title) title.textContent = "直接问一个种植问题。";
    if (copy) copy.textContent = "无需先选择植物；可以从空间、光照或养护目标开始提问。";
    if (grid) grid.hidden = true;
    if (foot) foot.textContent = "漫游模式 · 等待您的问题";
  }

  function setWelcomeForOfflineFixture() {
    if (!el.welcomeMessage) return;
    const label = $(".answer-label", el.welcomeMessage);
    const title = $("h3", el.welcomeMessage);
    const copy = $(".answer-lead p", el.welcomeMessage);
    const grid = $(".fact-grid", el.welcomeMessage);
    const foot = $(".answer-foot", el.welcomeMessage);
    if (label) label.textContent = "离线模式";
    if (title) title.textContent = "在线园艺资料暂时不可用";
    if (copy) copy.textContent = "仍可使用内置常见花卉知识；需要实时信息时可稍后重试。";
    if (grid) grid.hidden = true;
    if (foot) foot.textContent = "离线知识可用 · 在线资料稍后重试";
  }

  function setWelcomeForUnavailableTransport() {
    if (!el.welcomeMessage) return;
    const label = $(".answer-label", el.welcomeMessage);
    const title = $("h3", el.welcomeMessage);
    const copy = $(".answer-lead p", el.welcomeMessage);
    const grid = $(".fact-grid", el.welcomeMessage);
    const foot = $(".answer-foot", el.welcomeMessage);
    if (label) label.textContent = "连接提示";
    if (title) title.textContent = "园艺资料服务暂时不可用";
    if (copy) copy.textContent = "当前不会使用演示内容代替回答；请检查网络后重试。";
    if (grid) grid.hidden = true;
    if (foot) foot.textContent = "连接恢复后可继续当前会话";
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
    avatar.textContent = "花";

    const body = document.createElement("div");
    body.className = "message-body";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "种花 AGENT";
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
    // Only an explicitly enabled fixture snapshot can prove an offline date
    // empty. In public/live mode, an unavailable transport means unknown.
    if (state.apiAvailable) return "unknown";
    return fixtureTransportEnabled() ? "empty" : "unknown";
  }

  function recordHighlightAvailability(dateValue, hasGames, source = "api") {
    if (!isIsoDate(dateValue)) return;
    if (source === "demo_snapshot") {
      state.highlightAvailability.set(dateValue, hasGames ? "available" : "empty");
      return;
    }
    // API responses are authoritative, including an empty game list. Never
    // turn transport/provider errors into an empty result.
    state.highlightAvailability.set(dateValue, hasGames ? "available" : "empty");
  }

  function calendarStatusCopy() {
    if (!state.apiAvailable) {
      return fixtureTransportEnabled()
        ? "演示数据：可选择日期查看固定演示结果"
        : "园艺资料服务不可用，日期暂无法核验";
    }
    const values = datesInMonth(state.calendarMonth)
      .filter(Boolean)
      .map((dateValue) => availabilityForDate(dateValue));
    if (values.includes("loading")) return "正在核对本月资料…";
    if (values.includes("unknown") || values.includes("error")) {
      return "尚未核验的日期暂不可选，核对完成后会自动开放";
    }
    return "可选择有资料或已确认无资料的日期；灰色日期尚未核验";
  }

  function calendarDayLabel(dateValue, status) {
    const [, month, day] = String(dateValue).split("-");
    const suffix = {
      available: "有资料",
      empty: "无资料，可查看",
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
      // Confirmed empty days are still selectable: entering 赛事下钻 and
      // choosing today should be able to prove that there are no games,
      // rather than trapping the user on the previous date. Unknown/error
      // and future days remain disabled until they can be verified.
      if (["future", "loading", "unknown", "error"].includes(status)) {
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
          state.highlightAvailability.set(
            dateValue,
            fixtureTransportEnabled() && fixtureDateSet().has(dateValue) ? "available" : "unknown",
          );
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
    if (["future", "loading", "unknown", "error"].includes(status)) {
      showToast(status === "loading" || status === "unknown"
        ? "正在核对该日期是否有资料，请稍候"
        : status === "error"
          ? "该日期暂时无法核验，请稍后重试"
          : status === "future"
            ? "不能选择未来日期"
            : "不能选择未来日期");
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

  // Search snippets are internal grounding material.  They may still be
  // present in the response envelope so the server can audit provenance, but
  // the chat surface should show the composed answer rather than a raw
  // "补充线索/公开资料线索" result list.  Keep this projection deliberately
  // narrow: normal prose that merely mentions a public report is retained.
  const SUPPLEMENTAL_HEADING_RE = /^(?:补充线索|补充资料|相关报道(?:还)?提到|公开资料(?:新闻)?(?:线索|摘要|结果)|公开资料检索到以下(?:相关)?(?:线索|摘要|结果)|公开网页(?:线索|摘要|结果)|网页(?:搜索|检索)(?:结果|摘要|线索)|搜索(?:结果|摘要|线索)|检索(?:结果|摘要|线索)|公开报道(?:线索|摘要|结果))(?:\s*[（(][^）)]{0,80}[）)])?(?:\s*$|\s*[：:].*)$/u;
  const INLINE_SUPPLEMENTAL_RE = /^(.*?[。！？!?；;，,])\s*(?:\*\*|__|`)*(?:补充线索|补充资料|相关报道(?:还)?提到|公开资料(?:新闻)?(?:线索|摘要|结果)|公开网页(?:线索|摘要|结果)|搜索(?:结果|摘要|线索)|检索(?:结果|摘要|线索))(?:\s*[（(][^）)]{0,80}[）)])?(?:\*\*|__|`)*\s*[：:].*$/u;
  const SUPPLEMENTAL_CAVEAT_RE = /^(?:这些内容目前按公开网页线索处理，不标记为已核验事实|这些内容不标记为已核验事实|公开报道尚未与(?:官方|结构化)?比赛记录交叉核验|以上(?:为|公开资料仅作)线索，?尚未(?:与(?:官方|结构化)?比赛记录)?交叉核验|尚未与(?:官方|结构化)?比赛记录交叉核验)(?:[；;，,。.!！?？].*)?$/u;
  const INTERNAL_RETRIEVAL_RE = /^(?:已找到与问题相关的公开报道[；;，,。]?\s*)?(?:当前接入的)?结构化比赛(?:记录|数据)[^。.!！?？]{0,180}(?:暂未|尚未|没有|未)[^。.!！?？]{0,180}[。.!！?？]?$/u;
  const INTERNAL_PROCESS_LINE_RE = /^(?=[^\n]{0,360}(?:查询|检索|调用|命中|未命中|复用|读取|写入|返回|未找到|没有找到|完成搜索|失败))(?=[^\n]{0,360}(?:结构化(?:比赛)?(?:记录|数据)|缓存|数据库|索引|工具|搜索))[^\n]{1,360}$/u;
  const INTERNAL_SCALAR_RE = /^(?:工具|缓存|数据库|索引|结构化比赛(?:记录|数据))(?:状态|结果|信息|详情)?$/u;
  // Construct the private runtime token without publishing its literal name
  // in the browser bundle.  The client still blocks accidental server/model
  // disclosure, while users inspecting public assets do not see the brand.
  const PRIVATE_RUNTIME_TOKEN = String.fromCharCode(104, 101, 114, 109, 101, 115);
  const FORBIDDEN_INTERNAL_IDENTIFIER_RE = new RegExp(
    `(?:^|[^A-Za-z0-9_])(?:${PRIVATE_RUNTIME_TOKEN}|provider)(?=$|[^A-Za-z0-9_])`,
    "iu",
  );
  const SEARCH_PROCESS_LEAD_RE = /^(?:(?:我|我们|助手|系统|服务)\s*)?(?:进行(?:了)?\s*)?(?:在线|网页|公开资料)?\s*(?:搜索|检索|查询)(?:了)?[^，,:：。！？!?\n]{0,24}?(?:后|之后)?(?:发现|显示|表明|得知|可见)\s*[，,:：]\s*/u;
  const STRUCTURED_MISSING_LINE_RE = /^(?:(?:我|我们|助手|系统|服务)\s*)?(?:查询|检索|检查|读取)(?:了)?\s*(?:当前)?结构化(?:比赛)?(?:记录|数据)\s*[，,]?\s*(?:但|不过|然而)?\s*(?:没有|未能|未|缺少)\s*(?:找到|提供|包含|返回)?\s*([^。！？!?；;\n]{1,120})[。！？!?]?$/u;
  const MODEL_WORKFLOW_META_LINE_RE = /^(?:(?=[^\n]{0,520}(?:搜索|检索|来源|核验))(?=[^\n]{0,520}(?:我只使用|谨慎综合|存在不一致|说法不同|未经核验))[^\n]{1,520}|先给结论[^\n]{0,240}(?:明确标注|确认事实|过程细节)[^\n]{0,240})$/u;
  const COMPOSED_ANSWER_BOUNDARY_RE = /^(?:综合结论|最终(?:结论|回答)|结论|总结|综合来看|总体来看|总的来说|综合判断|简要回答)(?:\s*[：:].*|$)/u;
  const INTERNAL_STREAM_STARTS = Object.freeze([
    "补充线索", "补充资料", "相关报道还提到", "相关报道提到",
    "公开资料线索", "公开资料摘要", "公开资料结果", "公开资料新闻摘要",
    "公开网页线索", "公开网页摘要", "网页搜索结果", "网页搜索摘要",
    "搜索结果", "搜索摘要", "搜索线索", "检索结果", "检索摘要", "检索线索",
    "公开报道线索", "公开报道摘要", "根据搜索结果", "据搜索结果", "基于搜索结果",
    "补充资料显示", "缓存", "数据库", "索引", "结构化比赛记录", "结构化比赛数据",
    "我查询了结构化比赛", "我检索了结构化比赛", "我查询了结构化记录",
    "本轮已调用工具", "已调用工具", "我们搜索", "我们检索", "我们查询",
    "命中缓存", PRIVATE_RUNTIME_TOKEN, "provider",
  ]);

  function normalizeSupplementalLine(line) {
    // Search adapters/models occasionally wrap section labels in Markdown
    // emphasis (for example ``**搜索摘要**``).  Normalize only the outer
    // decoration used for a heading/caveat; ordinary inline emphasis remains
    // untouched in content that is kept for the user.
    return String(line || "")
      .trim()
      .replace(/^(?:>\s*)+/, "")
      .replace(/^#{1,6}\s*/, "")
      .replace(/^(?:\*\*|__|`)+/, "")
      .replace(/(?:\*\*|__|`)+(?=\s*(?:[：:（(]|$))/, "")
      .trim();
  }

  function isSupplementalHeading(line) {
    return SUPPLEMENTAL_HEADING_RE.test(normalizeSupplementalLine(line));
  }

  function stripSupplementalEvidence(markdown) {
    const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let hiding = false;

    lines.forEach((rawLine) => {
      let line = String(rawLine || "");
      let trimmed = line.trim();
      let normalized = normalizeSupplementalLine(trimmed);
      const structuredMissing = normalized.match(STRUCTURED_MISSING_LINE_RE);
      if (structuredMissing) {
        const detail = String(structuredMissing[1] || "")
          .trim()
          .replace(/^(?:可用的?|对应的?)\s*/u, "");
        line = /^(?:直接|对应)?匹配(?:项|内容|记录|结果)?$/u.test(detail)
          ? ""
          : `目前缺少${detail}。`;
      } else {
        line = line.replace(SEARCH_PROCESS_LEAD_RE, "");
      }
      trimmed = line.trim();
      normalized = normalizeSupplementalLine(trimmed);
      // Every provider-shaped heading starts (or restarts) the hidden evidence
      // section.  Search adapters may emit multiple adjacent sections (for
      // example "搜索摘要" followed by "公开资料摘要"); checking `!hiding`
      // here would let the second section's bullets leak into the public chat.
      if (isSupplementalHeading(trimmed)) {
        hiding = true;
        return;
      }
      const inlineSupplemental = normalized.match(INLINE_SUPPLEMENTAL_RE);
      if (inlineSupplemental) {
        const prefix = String(inlineSupplemental[1] || "")
          .trim()
          .replace(/[，,；;：:\s]+$/u, "");
        if (prefix) output.push(prefix);
        hiding = true;
        return;
      }
      if (hiding) {
        if (!trimmed) return;
        if (SUPPLEMENTAL_CAVEAT_RE.test(normalized)) {
          hiding = false;
          return;
        }
        // Search responses are not consistently formatted as Markdown lists.
        // Keep plain titles and multi-line excerpts hidden as well, and resume
        // only when the answer declares a new composed section explicitly.
        if (!COMPOSED_ANSWER_BOUNDARY_RE.test(normalized)) return;
        hiding = false;
      }
      if (SUPPLEMENTAL_CAVEAT_RE.test(normalized)) return;
      if (INTERNAL_RETRIEVAL_RE.test(normalized)) return;
      if (FORBIDDEN_INTERNAL_IDENTIFIER_RE.test(normalized)) return;
      if (MODEL_WORKFLOW_META_LINE_RE.test(normalized)) return;
      if (INTERNAL_PROCESS_LINE_RE.test(normalized)) return;
      output.push(line);
    });

    return output
      .join("\n")
      .replace(/^\s*(?:(?:根据|据|基于|结合)(?:本次|当前|现有)?(?:公开)?(?:网页)?(?:搜索|检索)(?:结果|资料|信息)?|搜索结果(?:显示|表明)|相关报道(?:还)?提到|补充资料(?:显示|表明))\s*[，,:：]\s*/gmu, "")
      .replace(/^(?:基于|结合)[^。！？!?\n]{0,160}(?:交叉检索|检索材料|已核验事实|比赛记录)[^。！？!?\n]{0,100}(?:回答)?\s*[：:]\s*/u, "")
      .replace(/[（(](?:已核验(?:的)?硬事实|依据公开报道(?:，?谨慎表述)?|基于公开报道(?:，?谨慎表述)?|交叉检索材料|结构化数据)[）)]/gu, "")
      .replace(/^\s*(?:根据|据)(?:相关|现有)?公开(?:报道|资料|信息)\s*[，,:：]\s*/gmu, "")
      .replace(/^\s*>?\s*(?:说明|注|备注)\s*[：:][^\n]{0,400}(?:结构化|检索|公开复盘|来源|官方\/?赛事录像|交叉核验)[^\n]*$/gmu, "")
      .replace(/^\s*数据截至北京时间\s*[^\n]{0,120}(?:已核验|部分核验)[。.!！]?\s*$/gmu, "")
      .replace(/^\s*(?:已经|已|目前)?(?:拿到|获得|取得|汇总(?:了)?|整理(?:了)?)[^。！？!?\n]{0,160}(?:硬事实|过程线索|检索材料|资料)[^。！？!?\n]{0,100}(?:综合回答|回答如下)?[。！？!?]?\s*$/gmu, "")
      .replace(/(?:需要说明(?:的是|一点)?\s*[，,:：]?\s*)?(?:当前)?结构化(?:比赛)?记录(?:里|中)?(?:没有|未提供|缺少|不含)\s*([^，,。！？!?；;\n]{1,120})[，,]/gu, "目前缺少$1，")
      .replace(/(?:打法|过程|战术)?细节(?:[（(][^）)]{0,140}[）)])?[^。！？!?；;\n]{0,80}(?:来自|依据|基于)[^。！？!?；;\n]{0,100}(?:公开)?(?:报道|资料|搜索|检索)(?:线索|材料)?[^。！？!?；;\n]{0,120}[；;]/gu, "因此无法可靠还原具体回合和反超节点；")
      .replace(/(?:这些|以上)(?:信息|内容|数据)?(?:是|属于)?已核验(?:的)?硬事实/gu, "这些可以确认")
      .replace(/^\s*(?:\*\*)?(?:当前)?可以确认的硬事实(?:\*\*)?\s*[：:]?\s*$/gmu, "**关键数据**")
      .replace(/^\s*(?:\*\*)?可以推断的有限可能因素(?:\*\*)?\s*[：:]\s*/gmu, "**谨慎分析**：")
      .replace(/[，,]\s*公开(?:报道|资料|网页)[^。！？!?；;\n]{0,220}(?:不一致|冲突)[^。！？!?；;\n]{0,160}[，,]/gu, "，")
      .replace(/我不能凭空(?:替你|为你)?(?:还原|补全|编出)/gu, "因此暂时无法可靠还原")
      .replace(/(?:本场(?:比赛)?|这场(?:比赛)?|该场(?:比赛)?|单场|G\s*\d+)(?:中)?\s*([\u4e00-\u9fffA-Za-z·.'’\-]{1,32})\s*(?:当选|获选|获评|获得|荣膺|拿下)(?:了)?\s*(?:本届)?\s*((?:(?:总决赛|系列赛)\s*(?:的)?\s*(?:MVP|最有价值球员))|FMVP)/giu, (_match, subject, award) => `${subject}最终当选${String(award).includes("系列赛") ? "系列赛" : "总决赛"} MVP`)
      .replace(/(?:在\s*)?(?:本场(?:比赛)?|这场(?:比赛)?|该场(?:比赛)?|单场|G\s*\d+)(?:中)?\s*(?:当选|获选|获评|获得|荣膺|拿下)(?:了)?\s*(?:本届)?\s*((?:(?:总决赛|系列赛)\s*(?:的)?\s*(?:MVP|最有价值球员))|FMVP)/giu, (_match, award) => `最终当选${String(award).includes("系列赛") ? "系列赛" : "总决赛"} MVP`)
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function containsInternalImplementationText(value) {
    const normalized = normalizeSupplementalLine(String(value ?? ""));
    if (!normalized) return false;
    return INTERNAL_SCALAR_RE.test(normalized)
      || FORBIDDEN_INTERNAL_IDENTIFIER_RE.test(normalized)
      || INTERNAL_PROCESS_LINE_RE.test(normalized)
      || isSupplementalHeading(normalized);
  }

  function projectStreamingAnswer(markdown) {
    const raw = String(markdown ?? "").replace(/\r\n?/g, "\n");
    let projected = stripSupplementalEvidence(raw);
    if (!raw || raw.endsWith("\n")) return projected;

    // A stream can split an internal heading at any character ("搜" →
    // "搜索摘" → "搜索摘要：").  Until that trailing fragment is either a
    // complete safe sentence or clearly not an internal prefix, keep only the
    // already-safe part of the answer on screen.
    const trailingLine = raw.slice(raw.lastIndexOf("\n") + 1);
    let suffixStart = 0;
    for (const delimiter of ["。", "！", "？", "!", "?", "；", ";"]) {
      suffixStart = Math.max(suffixStart, trailingLine.lastIndexOf(delimiter) + 1);
    }
    const unsafeSuffix = trailingLine.slice(suffixStart).trim();
    const normalized = normalizeSupplementalLine(unsafeSuffix);
    const folded = normalized.toLocaleLowerCase("en-US");
    const potentialInternal = Boolean(normalized) && INTERNAL_STREAM_STARTS.some((label) => {
      const foldedLabel = label.toLocaleLowerCase("en-US");
      return foldedLabel.startsWith(folded) || folded.startsWith(foldedLabel);
    });
    if (!potentialInternal || !projected) return projected;
    if (projected.endsWith(unsafeSuffix)) {
      projected = projected.slice(0, -unsafeSuffix.length).trimEnd();
    }
    return projected;
  }

  // Expose only this pure, provider-neutral projection for browser smoke
  // tests/embedders. It does not expose response metadata or search sources.
  window.CourtsideMarkdownUtils = Object.freeze({
    stripSupplementalEvidence,
    projectStreamingAnswer,
  });

  // Render the small Markdown subset used by the API without injecting HTML.
  // Agent/search answers commonly contain bullets; putting the whole answer
  // in one <p> made those bullets run together in the browser even though
  // the wire response contained line breaks.
  function appendMarkdownContent(container, markdown, paragraphClass = "block-text-line") {
    const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
    let list = null;
    let listType = null;
    const closeList = () => {
      list = null;
      listType = null;
    };
    lines.forEach((rawLine) => {
      const line = rawLine.trim();
      if (!line) {
        closeList();
        return;
      }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      const numbered = line.match(/^\d+[.)]\s+(.+)$/);
      if (bullet || numbered) {
        const nextType = numbered ? "ol" : "ul";
        if (!list || listType !== nextType) {
          closeList();
          list = document.createElement(nextType);
          list.className = "markdown-list";
          container.append(list);
          listType = nextType;
        }
        const item = document.createElement("li");
        item.append(createTextWithBold((bullet || numbered)[1]));
        list.append(item);
        return;
      }
      closeList();
      const paragraph = document.createElement("p");
      paragraph.className = paragraphClass;
      paragraph.append(createTextWithBold(line));
      container.append(paragraph);
    });
  }

  function appendBlock(container, block, index) {
    if (!block || typeof block !== "object") return;
    const type = String(block.type || "text").toLowerCase();
    if (!["text", "analysis", "warning", "clarification", "no_data", "table", "fact"].includes(type)) return;

    const rawLabel = String(block.label || "").trim();
    if (isSupplementalHeading(rawLabel)) return;
    const cleanLabel = stripSupplementalEvidence(rawLabel);
    if (rawLabel && (!cleanLabel || INTERNAL_SCALAR_RE.test(normalizeSupplementalLine(cleanLabel)))) return;
    const cleanContent = stripSupplementalEvidence(block.content || "");
    if (["text", "analysis", "warning", "clarification", "no_data"].includes(type) && !cleanContent) {
      return;
    }

    if (type === "fact") {
      if (containsInternalImplementationText(block.value)
        || containsInternalImplementationText(block.unit)) return;
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
      label.textContent = cleanLabel || "事实";
      const value = document.createElement("strong");
      value.className = "fact-value";
      const projectedValue = stripSupplementalEvidence(block.value ?? "");
      if (block.value !== null && block.value !== undefined && !projectedValue) return;
      value.textContent = projectedValue || "暂无数据";
      const unit = document.createElement("span");
      unit.className = "fact-unit";
      unit.textContent = stripSupplementalEvidence(block.unit || "");
      card.append(label, value, unit);
      grid.append(card);
      return;
    }

    if (type === "table") {
      const rawColumns = Array.isArray(block.columns) ? block.columns : [];
      const rawRows = Array.isArray(block.rows) ? block.rows : [];
      if (
        containsInternalImplementationText(rawLabel)
        || rawColumns.some((value) => containsInternalImplementationText(value))
        || rawRows.some((row) => Array.isArray(row)
          && row.some((value) => containsInternalImplementationText(value)))
      ) return;
      const section = document.createElement("section");
      section.className = "answer-block table-block";
      if (cleanLabel) {
      const label = document.createElement("div");
      label.className = "block-label";
        label.textContent = cleanLabel;
        section.append(label);
      }
      const wrap = document.createElement("div");
      wrap.className = "answer-table-wrap";
      const table = document.createElement("table");
      table.className = "answer-table";
      const caption = document.createElement("caption");
      caption.className = "sr-only";
      caption.textContent = cleanLabel || "数据表格";
      table.append(caption);
      const columns = rawColumns;
      const rows = rawRows;
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      columns.forEach((column) => {
        const th = document.createElement("th");
        th.scope = "col";
        const projectedColumn = stripSupplementalEvidence(column ?? "");
        th.textContent = projectedColumn && !INTERNAL_SCALAR_RE.test(normalizeSupplementalLine(projectedColumn))
          ? projectedColumn
          : "—";
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
          const projectedCell = stripSupplementalEvidence(value ?? "");
          td.textContent = value === null || value === undefined
            ? "暂无数据"
            : projectedCell && !INTERNAL_SCALAR_RE.test(normalizeSupplementalLine(projectedCell))
              ? projectedCell
              : "—";
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
    label.textContent = cleanLabel || (
      type === "analysis" ? "分析" :
      type === "clarification" ? "需要补充条件" :
      type === "no_data" ? "暂无匹配记录" :
      type === "warning" ? "提示" : "事实"
    );
    const text = document.createElement("div");
    text.className = "block-text";
    appendMarkdownContent(text, cleanContent);
    section.append(label, text);
    container.append(section);
  }

  function evidenceLabel(value, status = "completed") {
    const normalized = String(value || "none").toLowerCase();
    if (normalized === "verified") return { text: "已核验", className: "verified", icon: "✓" };
    if (normalized === "partial") return { text: "部分核验", className: "partial", icon: "△" };
    if (String(status || "").toLowerCase() === "completed") {
      return { text: "已回答", className: "none", icon: "·" };
    }
    return { text: "暂无数据", className: "none", icon: "—" };
  }

  function compositionLabel(value, response = null) {
    const mode = String(value?.mode || "deterministic").toLowerCase();
    const status = String(value?.status || "not_requested").toLowerCase();
    const conversational = String(response?.status || "").toLowerCase() === "completed"
      && String(response?.evidence_state || "none").toLowerCase() === "none";
    if (mode === "agent" && status === "used") {
      const latency = Number.isFinite(Number(value?.latency_ms)) && Number(value.latency_ms) > 0
        ? ` · ${Math.round(Number(value.latency_ms) / 100) / 10}s`
        : "";
      return {
        text: `智能分析${latency}`,
        className: "agent",
      title: conversational ? "已完成智能回答" : "已完成智能回答与资料核验",
      };
    }
    if (mode === "model" && status === "used") {
      const latency = Number.isFinite(Number(value?.latency_ms)) && Number(value.latency_ms) > 0
        ? ` · ${Math.round(Number(value.latency_ms) / 100) / 10}s`
        : "";
      return { text: `智能分析${latency}`, className: "model", title: "已完成智能分析" };
    }
    if (mode === "fallback" && status === "disabled") {
      return { text: "已完成资料核验", className: "fallback", title: "已使用可用资料回答" };
    }
    if (mode === "fallback") {
      return { text: "已补充资料核验", className: "fallback", title: "回答已由可用资料补充完成" };
    }
    if (conversational) {
      return { text: "会话处理", className: "deterministic", title: "由当前应用会话状态确定性回答" };
    }
      return { text: "建议已整理", className: "deterministic", title: "建议由当前会话与可用资料整理" };
  }

  function renderSimpleMarkdown(container, markdown) {
    const text = stripSupplementalEvidence(markdown);
    if (!text) return;
    const content = document.createElement("div");
    content.className = "assistant-plain answer-block";
    appendMarkdownContent(content, text, "assistant-plain-line");
    container.append(content);
  }

  function verifiedFinalWinnerName(game) {
    if (!game || String(game.status || "").trim().toLowerCase() !== "final") return null;
    if (game.home_score == null || game.away_score == null) return null;
    if (String(game.home_score).trim() === "" || String(game.away_score).trim() === "") return null;
    const homeScore = Number(game.home_score);
    const awayScore = Number(game.away_score);
    if (!Number.isFinite(homeScore) || !Number.isFinite(awayScore) || homeScore === awayScore) {
      return null;
    }
    const winner = homeScore > awayScore ? game.home_name : game.away_name;
    return String(winner || "").trim() || null;
  }

  function teamAliasIndex(text, alias) {
    const source = String(text || "");
    const token = String(alias || "");
    if (!source || !token) return -1;
    if (!/^[a-z\s]+$/i.test(token)) return source.indexOf(token);
    const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
    const match = new RegExp(`(^|[^a-z])${escaped}(?=$|[^a-z])`, "i").exec(source);
    return match ? match.index + match[1].length : -1;
  }

  function plantsMentionedIn(text) {
    const hits = PLANT_ALIASES.map(([canonical, ...aliases]) => {
      const indices = [canonical, ...aliases]
        .map((alias) => teamAliasIndex(text, alias))
        .filter((index) => index >= 0);
      return indices.length ? { name: canonical, index: Math.min(...indices) } : null;
    }).filter(Boolean);
    hits.sort((left, right) => left.index - right.index);
    return hits.map((hit) => hit.name);
  }

  function appendRecommendationEvidence(value, target) {
    if (value == null) return;
    if (Array.isArray(value)) {
      value.forEach((item) => appendRecommendationEvidence(item, target));
      return;
    }
    if (typeof value === "object") {
      Object.values(value).forEach((item) => appendRecommendationEvidence(item, target));
      return;
    }
    const text = String(value).trim();
    if (text) target.push(text);
  }

  function verifiedRecommendationEvidence(response) {
    const evidenceState = String(response?.evidence_state || "none").toLowerCase();
    if (!new Set(["verified", "partial"]).has(evidenceState)) return "";
    const evidence = [];
    appendRecommendationEvidence(response?.answer_markdown, evidence);
    (Array.isArray(response?.blocks) ? response.blocks : []).forEach((block) => {
      appendRecommendationEvidence(block?.label, evidence);
      appendRecommendationEvidence(block?.content, evidence);
      appendRecommendationEvidence(block?.value, evidence);
      appendRecommendationEvidence(block?.unit, evidence);
      appendRecommendationEvidence(block?.columns, evidence);
      appendRecommendationEvidence(block?.rows, evidence);
    });
    (Array.isArray(response?.corrections) ? response.corrections : []).forEach((correction) => {
      appendRecommendationEvidence(correction?.message, evidence);
    });
    return evidence.join("\n");
  }

  function plantRecommendationDetails(text) {
    const source = String(text || "");
    const isPlan = /养护|浇水|施肥|修剪|光照|土壤|换盆|繁殖|病虫|黄叶|萎蔫|季节|多久|怎么做/i.test(source);
    return { isPlan, maxSteps: (source.match(/(?:步骤|建议|要点)/g) || []).length };
  }

  function isContextualRecommendationQuestion(text) {
    return /它|这盆|这株|刚才|上(?:一|个)问题|上一轮|这个植物|该花|后续|接下来|然后|怎么做|多久|还要/i.test(String(text || ""));
  }

  function updateRecommendationContext(response) {
    const question = String(state.run?.message || "").trim();
    const evidence = verifiedRecommendationEvidence(response);
    const questionPlants = plantsMentionedIn(question);
    const evidencePlants = plantsMentionedIn(evidence);
    const previous = state.recommendationContext;
    let plants = null;

    if (questionPlants.length) {
      plants = questionPlants;
    } else if (evidencePlants.length) {
      plants = evidencePlants;
    } else if (previous?.plants?.length && isContextualRecommendationQuestion(question)) {
      plants = previous.plants;
    }

    if (!plants) {
      state.recommendationContext = null;
      return null;
    }

    const details = plantRecommendationDetails(`${question}\n${evidence}`);
    state.recommendationContext = {
      plants: [...plants],
      isPlan: details.isPlan || Boolean(previous?.isPlan && plants.some((plant) => previous.plants.includes(plant))),
      maxSteps: Math.max(
        details.maxSteps,
        plants.some((plant) => previous?.plants?.includes(plant)) ? Number(previous?.maxSteps || 0) : 0,
      ),
    };
    return state.recommendationContext;
  }

  function contextualRecommendations(context) {
    const plant = context?.plants?.[0];
    if (!plant) return [];
    return [
      `${plant} 的光照和浇水怎么安排？`,
      `${plant} 黄叶或萎蔫时先排查什么？`,
      `${plant} 这个季节需要换盆或施肥吗？`,
    ];
  }

  function renderRecommendations(response) {
    if (!el.recommendations || !el.recommendationList) return;
    const game = state.activeGame;
    const plant = state.recommendationContext?.plants?.[0] || "这盆花";
    let suggestions = [];
    if (game) {
      // Legacy event cards are not part of the flower UI. If an old deep link
      // still selects one, keep recommendations in the current product domain
      // instead of exposing sports terminology.
      suggestions.push("这盆花下一步该怎么养？");
      suggestions.push("需要补充哪些光照和浇水信息？");
      suggestions.push("如何安全排查黄叶或虫害？");
    } else {
      const context = updateRecommendationContext(response);
      suggestions = contextualRecommendations(context);
      if (!suggestions.length) {
        suggestions.push("北阳台适合种什么花？");
        suggestions.push("这盆花多久浇水、怎么判断该浇了？");
        suggestions.push("月季黄叶时先做哪些低风险排查？");
      }
    }
    const values = [...new Set(suggestions.map((item) => item.trim()).filter(Boolean))].slice(0, 3);
    el.recommendationList.textContent = "";
    values.forEach((prompt) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "recommendation-button";
      button.dataset.prompt = prompt;
      button.textContent = prompt;
      el.recommendationList.append(button);
    });
    el.recommendations.hidden = values.length === 0;
  }

  // Error envelopes can originate at an upstream adapter.  Keep the public
  // surface useful while preventing provider names, URLs, billing text or
  // implementation details from leaking into the conversation.
  const INTERNAL_ERROR_RE = /(https?:\/\/|wss?:\/\/|api[_ -]?key|token|billing|quota\s+at|provider|endpoint|model[_ -]?name|stack trace)/iu;
  function safePublicErrorMessage(responseOrError, fallback = "种花服务暂时不可用，请稍后重试。") {
    const candidate = typeof responseOrError === "string"
      ? responseOrError
      : responseOrError?.error?.message || responseOrError?.message || "";
    const value = String(candidate || "").trim();
    if (!value || INTERNAL_ERROR_RE.test(value)) {
      const code = String(responseOrError?.error?.code || responseOrError?.code || "").toUpperCase();
      if (code.includes("QUOTA")) return "当前服务额度已用完，请稍后重试或联系管理员。";
      if (code.includes("TIMEOUT")) return "服务响应超时，请稍后重试。";
      if (code.includes("AUTH")) return "服务认证暂时不可用，请稍后重试。";
      return fallback;
    }
    return value.replace(/https?:\/\/\S+/giu, "").trim() || fallback;
  }

  function renderError(container, response, retryable) {
    if (Array.isArray(response?.notices) && response.notices.length) {
      const noticeCard = document.createElement("div");
      noticeCard.className = "answer-card capability-notice-card";
      renderCapabilityNotices(noticeCard, response.notices);
      if (noticeCard.children.length) container.append(noticeCard);
    }
    const card = document.createElement("div");
    card.className = "error-card dynamic-error";
    const label = document.createElement("div");
    label.className = "block-label";
    label.textContent = "连接状态";
    const text = document.createElement("p");
    text.textContent = safePublicErrorMessage(response);
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

  const CAPABILITY_NOTICE_DEFAULTS = Object.freeze({
    INTELLIGENCE_QUOTA_EXHAUSTED: {
      label: "智能分析提醒",
      message: "智能回答额度已用完；仍可使用本地花卉知识，开放性分析暂时受限。",
    },
    SEARCH_QUOTA_EXHAUSTED: {
      label: "在线搜索提醒",
      message: "在线资料额度已用完；本轮改用本地花卉知识，最新地域信息可能不完整。",
    },
    SEARCH_CAPACITY_LIMITED: {
      label: "在线搜索提醒",
      message: "在线资料额度暂受限；本轮已使用本地知识，结果可能不完整。",
    },
    INTELLIGENCE_TEMPORARILY_UNAVAILABLE: {
      label: "智能分析提醒",
      message: "智能回答暂时不可用；仍可使用本地花卉知识，开放性分析可能受限。",
    },
    INTELLIGENCE_AUTH_UNAVAILABLE: {
      label: "智能分析提醒",
      message: "智能回答当前不可用；仍可使用本地花卉知识，开放性分析暂时受限。",
    },
    SEARCH_TEMPORARILY_UNAVAILABLE: {
      label: "在线搜索提醒",
      message: "在线资料暂时不可用，当前无法补充最新网页信息；基础养护建议仍可提供。",
    },
    SEARCH_AUTH_UNAVAILABLE: {
      label: "在线搜索提醒",
      message: "在线资料当前不可用，暂时无法补充最新网页信息；基础养护建议仍可提供。",
    },
  });

  function renderCapabilityNotices(container, notices) {
    if (!Array.isArray(notices) || !notices.length) return;
    const seen = new Set();
    notices.forEach((notice) => {
      if (!notice || typeof notice !== "object") return;
      const code = String(notice.code || "").trim().toUpperCase();
      const fallback = CAPABILITY_NOTICE_DEFAULTS[code];
      if (!fallback || seen.has(code)) return;
      seen.add(code);
      // Do not render arbitrary upstream text here.  Capability notices use
      // application-owned wording so a dependency name or billing detail can
      // never leak through a successful chat envelope.
      const section = document.createElement("section");
      section.className = "answer-block warning-block capability-notice";
      section.dataset.noticeCode = code;
      const label = document.createElement("div");
      label.className = "block-label";
      label.textContent = fallback.label;
      const text = document.createElement("div");
      text.className = "block-text";
      appendMarkdownContent(text, fallback.message);
      section.append(label, text);
      container.append(section);
    });
  }

  function renderCompletedAnswer(placeholder, response) {
    if (!placeholder || !response) return;
    const body = placeholder.body;
    body.textContent = "";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "种花 AGENT";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    const dataOrigin = String(response.data_origin || "none").toLowerCase();
    const demoSnapshot = dataOrigin === "demo_snapshot"
      && String(response.evidence_state || "none").toLowerCase() !== "none";
    const mixedSnapshot = dataOrigin === "mixed"
      && String(response.evidence_state || "none").toLowerCase() !== "none";
    const evidence = demoSnapshot
      ? { text: "演示快照", className: "partial", icon: "◇" }
      : mixedSnapshot
        ? { text: "部分核验 · 含演示快照", className: "partial", icon: "△" }
      : evidenceLabel(response.evidence_state, response.status);
    const mark = document.createElement("span");
    mark.className = "verified-mark";
    mark.textContent = `${evidence.icon} ${evidence.text}`;
    meta.append(name, time, mark);

    const card = document.createElement("div");
    card.className = "answer-card dynamic-answer";

    // Quota exhaustion is a capability state, not an empty-data result.  Keep
    // any usable cached answer, but make the missing online capability
    // impossible to overlook at the top of the answer card.
    renderCapabilityNotices(card, response.notices);

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
    const composition = compositionLabel(response.composition, response);
    const source = document.createElement("span");
    source.className = `composition-chip ${composition.className}`;
    source.textContent = composition.text;
    source.title = composition.title;
    const chip = document.createElement("span");
    chip.className = `evidence-chip ${evidence.className}`;
    chip.append(document.createTextNode(`${evidence.icon} ${evidence.text}`));
    const asOf = document.createElement("span");
    asOf.textContent = demoSnapshot
      ? "本地知识快照 · 仅作一般养护参考"
      : mixedSnapshot
        ? response.as_of_beijing
          ? `本地知识 + 在线资料 · 更新于 ${response.as_of_beijing}`
          : "本地知识 + 在线资料 · 以现场观察为准"
      : response.as_of_beijing
      ? `园艺资料 · 更新于 ${response.as_of_beijing}`
      : response.status === "completed" && String(response.evidence_state || "none").toLowerCase() === "none"
        ? "本轮回答 · 无需展示时间"
        : "园艺资料 · 当前没有可用的时间口径";
    foot.append(source, chip, asOf);
    card.append(foot);
    // `body` remains attached to the article while streaming.  Reusing the
    // same nodes keeps focus/scroll behaviour stable and avoids duplicate
    // assistant messages when the terminal envelope arrives.
    body.append(meta, card);
    renderRecommendations(response);
    if (placeholder.article && !placeholder.article.parentElement) {
      placeholder.article.append(placeholder.avatar || createAvatar("assistant-avatar", "花"), body);
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
    name.textContent = "种花 AGENT";
    const time = document.createElement("span");
    time.textContent = currentShortTime();
    meta.append(name, time);
    body.append(meta);
    renderError(body, response, Boolean(response?.error?.retryable));
  }

  function updateStreamingText(run, text) {
    if (!run || !run.placeholder) return;
    run.partialText += text;
    const projected = projectStreamingAnswer(run.partialText);
    run.placeholder.bubble.textContent = projected || "正在整理回答…";
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
    state.lastRequest = {
      message: run.message,
      clientMessageId: run.clientMessageId,
      intelligenceMode: run.intelligenceMode,
      selectedGameId: run.selectedGameId || null,
    };
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
      as_of_beijing: null,
      evidence_state: "verified",
      data_origin: "local_knowledge",
      corrections: [],
      follow_up: null,
      composition: { mode: "deterministic", status: "not_requested", latency_ms: 0 },
    };

    // Safety short-circuit: never suggest mixing pesticides or consuming an
    // unknown plant. The UI deliberately gives immediate, practical steps and
    // does not perform an external search for a high-risk request.
    if (/(农药|杀虫剂|除草剂).{0,12}(混|一起|混合|同桶)|混.{0,12}(农药|杀虫剂|除草剂)|误食|吃了.{0,10}(叶子|花|植物)|猫|狗|宠物.{0,10}(吃|舔|咬)|中毒|喷药/i.test(text)) {
      return {
        ...base,
        status: "blocked",
        evidence_state: "none",
        as_of_beijing: null,
        answer_markdown: "先不要混用或继续接触这类物质。停止喷洒并离开通风处，保留产品标签和包装；若人或宠物已经误食、吸入或出现不适，请立即联系急救中心、兽医或当地中毒咨询机构。不要自行催吐，也不要根据网上说法配药。",
        blocks: [{ type: "warning", label: "安全提示", content: "先隔离暴露源、冲洗皮肤或眼睛（按产品标签操作），记录产品名称和接触时间，并寻求专业帮助。" }],
        follow_up: "如果没有发生暴露，我可以帮你制定低风险的病虫害排查步骤。",
      };
    }

    // These explicit demo probes exercise the retry/error states before the
    // out-of-scope classifier below (the probe text itself need not mention
    // basketball).
    if (!options.forceSuccess && /断线|网络|连接中断/i.test(text)) {
      return {
        ...base,
        status: "failed",
        error: { code: "UPSTREAM_TIMEOUT", retryable: true, message: "园艺资料连接暂时中断，请稍后重试。" },
      };
    }

    if (!options.forceSuccess && /超时|服务忙|稍后/i.test(text)) {
      return {
        ...base,
        status: "failed",
        error: { code: "SERVICE_BUSY", retryable: true, message: "种花服务当前较忙，请稍后重试。" },
      };
    }

    const plantHit = plantsMentionedIn(text)[0] || state.gardenContext?.plant || null;
    const locationHit = text.match(/(北京|上海|广州|深圳|杭州|成都|重庆|南京|武汉|天津|西安|苏州|青岛)/)?.[1] || state.gardenContext?.location || null;
    const lightHit = text.match(/(北阳台|南阳台|东向|西向|全日照|半阴|散射光|直射光|明亮无直射)/)?.[1] || state.gardenContext?.light || null;
    const containerHit = text.match(/(\d+(?:\.\d+)?\s*(?:厘米|cm)\s*(?:盆|花盆)|地栽|阳台|窗台)/i)?.[1] || state.gardenContext?.container || null;
    state.gardenContext = { ...(state.gardenContext || {}), ...(plantHit ? { plant: plantHit } : {}), ...(locationHit ? { location: locationHit } : {}), ...(lightHit ? { light: lightHit } : {}), ...(containerHit ? { container: containerHit } : {}) };

    const contextShorthand = /它|这盆|这株|这个植物|刚才|上一条|上一轮|然后|接下来/i.test(text);
    if (contextShorthand && !plantHit && !state.contextRequest && !state.activeGame) {
      return {
        ...base,
        status: "needs_clarification",
        evidence_state: "none",
        as_of_beijing: null,
        answer_markdown: "我还不知道你指的是哪种植物。请告诉我植物名称，或发来叶片、花盆和光照的文字描述。",
        blocks: [],
        follow_up: "例如：绣球，北阳台，上午有两小时直射光。",
      };
    }

    if (/你是谁|你能做什么|你是什么/i.test(text)) {
      return {
        ...base,
        status: "completed",
        answer_markdown: "我是种花 Agent，帮你选择花卉、安排光照和浇水、处理常见养护问题，并在需要时查找当地或近期资料。涉及人、宠物或化学品安全时，我会先给出谨慎的求助建议。",
        blocks: [],
        follow_up: "你可以先问：北阳台适合种什么花？",
      };
    }

    if (/北阳台|南阳台|窗台|适合种|选什么花|推荐.*花/i.test(text)) {
      return {
        ...base,
        status: "completed",
        answer_markdown: "先按光照和空间选，不要只看花名：北向或散射光阳台可优先考虑绣球、蝴蝶兰、君子兰；如果每天有 4–6 小时直射光，再考虑月季、天竺葵等。",
        blocks: [
          { type: "fact", label: "低光候选", value: "绣球 / 蝴蝶兰 / 君子兰", unit: "明亮散射光" },
          { type: "fact", label: "高光候选", value: "月季 / 天竺葵", unit: "每天约 4–6 小时直射光" },
          { type: "analysis", label: "下一步", content: "告诉我朝向、每天直射光时长、花盆大小和你想要的花期，我可以缩小到 2–3 个选择。" },
        ],
        follow_up: "我家是北阳台，上午只有一小时光，应该选哪一种？",
      };
    }

    if (/浇水|多久浇|该不该浇|干.*浇|施肥|肥料/i.test(text)) {
      const plant = plantHit || "这盆花";
      return {
        ...base,
        status: "completed",
        answer_markdown: `先不要按固定天数浇${plant}：手指探入表土约 2–3 厘米，明显干燥再一次浇透，直到盆底少量出水；托盘积水要倒掉。光照、盆径、季节和介质都会改变频率。`,
        blocks: [
          { type: "fact", label: "判断信号", value: "表土下 2–3 厘米干燥", unit: "再浇透" },
          { type: "warning", label: "避免", content: "不要让盆底长期泡水，也不要只看日历机械浇水。" },
        ],
        follow_up: "这盆花是什么品种、花盆多大、放在哪里？",
      };
    }

    if (/黄叶|发黄|萎蔫|掉叶|虫|蚜|白粉|病/i.test(text)) {
      return {
        ...base,
        status: "completed",
        evidence_state: "partial",
        answer_markdown: `仅凭${plantHit || "文字症状"}不能确定病因。先按低风险顺序排查：土壤是否长期湿、根部是否有异味、叶背有没有小虫或蛛网、近期是否突然暴晒或施肥过量。`,
        blocks: [
          { type: "analysis", label: "可能原因（按常见度）", content: "浇水过多或排水差、光照骤变、根系受损、虫害或营养失衡。" },
          { type: "text", label: "先做什么", content: "暂停施肥和强药；把植株移到稳定的明亮散射光，检查盆底排水与叶背，记录 2–3 天变化。若快速扩散、茎基部变软或整株萎蔫，请找当地园艺师确认。" },
        ],
        follow_up: "可以补充：植物名称、黄叶从老叶还是新叶开始、土壤湿度和最近一次施肥时间。",
      };
    }

    if (/天气|温度|湿度|上海|北京|广州|九月|十月|季节/i.test(text)) {
      return {
        ...base,
        status: "completed",
        evidence_state: "partial",
        answer_markdown: `${locationHit || "当地"}的季节养护会受当天温度、降雨和光照影响。没有实时天气时，先把${plantHit || "植株"}放在通风、光线稳定的位置，按表土干湿决定浇水，避免在高温或暴雨前施浓肥。若你告诉我城市、月份和植物，我可以再结合最新资料给出调整建议。`,
        blocks: [{ type: "warning", label: "信息边界", content: "实时天气和品种差异可能改变频率；请以现场观察和产品标签为准。" }],
        follow_up: "例如：上海九月，月季，南阳台，每天 5 小时直射光。",
      };
    }

    return {
      ...base,
      status: "completed",
      answer_markdown: "我可以帮你选花、安排光照和浇水、制定施肥/修剪计划，并排查常见黄叶和虫害。为了给出更准确的建议，请告诉我植物名称（或外观）、城市/季节、光照时长和花盆大小。",
      blocks: [
        { type: "text", label: "最小补充条件", content: "植物 + 光照（朝向/每天直射时长）+ 城市或季节 + 容器/介质。" },
      ],
      follow_up: "北阳台适合种什么花？",
    };
  }

  function startDemoRun(message, options = {}) {
    if (state.streaming) return;
    const clientMessageId = options.clientMessageId || makeId("client");
    const intelligenceMode = options.intelligenceMode || state.intelligenceMode;
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
      intelligenceMode,
      selectedGameId: selectedGameIdForRequest(),
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
    const intelligenceMode = options.intelligenceMode || state.intelligenceMode;
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
      intelligenceMode,
      selectedGameId: selectedGameIdForRequest(),
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
      intelligenceMode,
      selectedGameId: run.selectedGameId,
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
          error: { code: "UPSTREAM_TIMEOUT", retryable: true, message: "种花服务连接暂时中断，请稍后重试。" },
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
      if (error?.network !== false) {
        const allowFixtureFallback = fixtureTransportEnabled();
        state.apiAvailable = false;
        state.apiProbeComplete = true;
        setTransportLabel(false, state.apiDataMode);
        if (allowFixtureFallback) {
          // Only an explicitly configured fixture profile may replace the
          // failed request with the local deterministic snapshot.
          state.run = null;
          placeholder.article.remove();
          setComposerBusy(false);
          setStreamStatus(false);
          setWelcomeForOfflineFixture();
          setConnection("ready", "离线演示");
          showToast("演示服务暂不可用，已使用显式启用的离线快照");
          startDemoRun(message, { reuseUser: true, clientMessageId });
          return;
        }
        setWelcomeForUnavailableTransport();
        showToast("种花服务连接已中断，可点击重试");
        finishRun({
          request_id: run.requestId || makeId("request"),
          session_id: state.sessionId,
          status: "failed",
          error: {
            code: "NETWORK_UNAVAILABLE",
            retryable: true,
            message: "种花服务连接暂时中断，请检查网络后重试。",
          },
        });
        return;
      }
      finishRun(error.publicPayload || {
        request_id: run.requestId || makeId("request"),
        session_id: state.sessionId,
        status: "failed",
        error: {
          code: "SERVICE_BUSY",
          retryable: true,
          message: safePublicErrorMessage(error, "种花服务暂时不可用，请稍后重试。"),
        },
      });
    });
    return true;
  }

  function startUnavailableRun(message, options = {}) {
    const clientMessageId = options.clientMessageId || makeId("client");
    const intelligenceMode = options.intelligenceMode || state.intelligenceMode;
    if (!options.reuseUser) appendUserMessage(message);
    const placeholder = createAssistantPlaceholder();
    state.run = {
      message,
      clientMessageId,
      requestId: null,
      placeholder,
      partialText: "",
      timers: [],
      started: false,
      branch: null,
      live: false,
      abortController: null,
      intelligenceMode,
      selectedGameId: selectedGameIdForRequest(),
    };
    setWelcomeForUnavailableTransport();
    setTransportLabel(false, state.apiDataMode);
    finishRun({
      request_id: makeId("request"),
      session_id: state.sessionId,
      status: "failed",
      error: {
        code: "NETWORK_UNAVAILABLE",
        retryable: true,
        message: "园艺资料服务暂时不可用，请检查网络后重试。",
      },
    });
    return true;
  }

  function startRequest(message, options = {}) {
    if (state.authEnabled && !state.authenticated) {
      requireLogin();
      return false;
    }
    // A selected card is changed only by an explicit card click.  If the
    // message names another game, the server parser handles that condition
    // for this turn without changing the UI's drill-down scope.
    syncSelectedGameState();
    const fixtureEnabled = fixtureTransportEnabled();
    const apiConfigured = Boolean(window.CourtsideApi?.baseUrl);
    if (options.forceDemo && fixtureEnabled) {
      startDemoRun(message, options);
      return true;
    }
    // In public/live mode, a failed health probe is not permission to use
    // fixture facts.  Let the user retry the configured API directly: the
    // chat endpoint may already be healthy even if the short probe failed.
    if (apiConfigured && (state.apiAvailable || !fixtureEnabled)) {
      return startApiRun(message, options);
    }
    if (fixtureEnabled) {
      startDemoRun(message, options);
      return true;
    }
    return startUnavailableRun(message, options);
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
      showToast("请先输入一个种植问题");
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
      intelligenceMode: state.lastRequest.intelligenceMode || state.intelligenceMode,
      selectedGameId: selectedGameIdForRequest() || state.lastRequest.selectedGameId || null,
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
    state.recommendationContext = null;
    state.gardenContext = null;
    state.retryCount = 0;
    state.intelligenceMode = state.defaultIntelligenceMode || "hybrid";
    // A new chat is also a new game-context boundary. Keep the reusable
    // highlights list visible, but remove its selected card so the next
    // request cannot silently seed the fresh server session with the prior
    // conversation's matchup.
    state.detailRequest += 1;
    state.detailLoadingGameId = null;
    state.activeGame = null;
    state.activePbp = null;
    state.selectedGameId = null;
    setConversationScope(null);
    if (el.intelligenceMode) el.intelligenceMode.checked = state.intelligenceMode === "full";
    try {
      window.sessionStorage.setItem(STORAGE_KEY, state.sessionId);
    } catch (_error) {
      // Ignore storage failures.
    }
    $$(".dynamic-message").forEach((message) => message.remove());
    if (el.recommendations) el.recommendations.hidden = true;
    if (el.featuredGame) el.featuredGame.hidden = true;
    updateGameListSelection();
    resetHud();
    renderPbp("Q4");
    showToast("已开始新对话，会话上下文已隔离");
    el.input.value = "";
    updateCharCount();
    autoGrowInput();
    el.input.focus();
  }

  function resetHud() {
    setTeamToken(el.hudAwayToken, "—");
    setTeamToken(el.hudHomeToken, "—");
    if (el.hudAwayName) el.hudAwayName.textContent = "尚未选择植物";
    if (el.hudHomeName) el.hudHomeName.textContent = "尚未设置环境";
    if (el.hudAwayRecord) el.hudAwayRecord.textContent = "植物";
    if (el.hudHomeRecord) el.hudHomeRecord.textContent = "环境";
    if (el.hudEyebrow) el.hudEyebrow.textContent = "PLANT PROFILE / NO PLANT SELECTED";
    if (el.hudStatus) el.hudStatus.textContent = "未选择";
    if (el.awayScore) el.awayScore.textContent = "—";
    if (el.homeScore) el.homeScore.textContent = "—";
    if (el.scoreClock) el.scoreClock.textContent = "—";
    if (el.hudPossession) el.hudPossession.textContent = "下一步：—";
    if (el.hudPace) el.hudPace.textContent = "湿度 —";
    if (el.hudLeaderName) el.hudLeaderName.textContent = "—";
    if (el.hudLeaderLine) el.hudLeaderLine.textContent = "等待植物详情";
    el.quarterCells.forEach((cell) => {
      const score = $("[data-quarter-score]", cell);
      if (score) score.textContent = "—";
    });
  }

  function setConversationScope(game = null) {
    if (!el.conversationScope) return;
    const drilldown = Boolean(game && game.game_id);
    el.conversationScope.textContent = drilldown ? "植物档案" : "漫游模式";
    el.conversationScope.title = drilldown
      ? "已注入当前选中植物上下文；点击此处退出植物档案"
      : "未绑定具体植物；问题按通用园艺范围理解，可先问选花、养护或病虫害";
    el.conversationScope.dataset.scope = drilldown ? "drilldown" : "roaming";
    el.conversationScope.setAttribute("aria-pressed", drilldown ? "true" : "false");
  }

  function exitGameDrilldown() {
    if (!state.activeGame && !state.selectedGameId) return;
    clearSelectedGameContext();
    showToast("已返回漫游模式，后续问题不再绑定具体植物");
  }

  const REGULATION_PERIODS = ["Q1", "Q2", "Q3", "Q4"];
  const REPLAY_PERIODS = [...REGULATION_PERIODS, "OT"];

  function eventScore(event) {
    if (event?.away == null || event?.home == null) return null;
    const away = Number(event?.away);
    const home = Number(event?.home);
    if (!Number.isInteger(away) || away < 0 || !Number.isInteger(home) || home < 0) {
      return null;
    }
    return { away, home };
  }

  function clockAtPeriodEnd(value) {
    const normalized = String(value || "").trim();
    const match = normalized.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
    if (!match) return false;
    const minutes = Number(match[1] || 0);
    const seconds = Number(match[2]);
    return Number.isFinite(minutes) && Number.isFinite(seconds)
      && minutes === 0 && seconds === 0;
  }

  function clockAtRegulationPeriodStart(value) {
    const normalized = String(value || "").trim();
    const match = normalized.match(/^(\d+):(\d+(?:\.\d+)?)$/);
    if (!match) return false;
    return Number(match[1]) === 12 && Number(match[2]) === 0;
  }

  function scoreMatchesFinal(score, game) {
    return score
      && Number.isInteger(game?.away_score)
      && Number.isInteger(game?.home_score)
      && score.away === Number(game.away_score)
      && score.home === Number(game.home_score);
  }

  function periodEndScore(pbp, period, game) {
    const events = Array.isArray(pbp?.[period]) ? pbp[period] : [];
    const scoredEvents = events.filter((event) => eventScore(event));
    const last = scoredEvents[scoredEvents.length - 1];
    const score = eventScore(last);
    if (!score) return null;

    const endCopy = `${last?.player || ""} ${last?.action || ""} ${last?.detail || ""}`;
    const explicitPeriodEnd = clockAtPeriodEnd(last?.clock)
      || /(?:节|比赛|quarter|period|game).{0,8}(?:结束|终场|完毕|end)/i.test(endCopy)
      || /(?:结束|终场|end).{0,8}(?:节|比赛|quarter|period|game)/i.test(endCopy);
    if (explicitPeriodEnd) return score;

    const periodIndex = REGULATION_PERIODS.indexOf(period);
    if (periodIndex >= 0 && periodIndex < REGULATION_PERIODS.length - 1) {
      const nextPeriod = REGULATION_PERIODS[periodIndex + 1];
      const nextEvents = Array.isArray(pbp?.[nextPeriod]) ? pbp[nextPeriod] : [];
      const nextFirst = nextEvents.find((event) => eventScore(event));
      const nextScore = eventScore(nextFirst);
      const startCopy = `${nextFirst?.player || ""} ${nextFirst?.action || ""} ${nextFirst?.detail || ""}`;
      const explicitNextStart = /(?:节|quarter|period).{0,8}(?:开始|start)/i.test(startCopy)
        || /(?:开始|start).{0,8}(?:节|quarter|period)/i.test(startCopy)
        || clockAtRegulationPeriodStart(nextFirst?.clock);
      // Hupu places the previous quarter's final cumulative score on the
      // following quarter's 12:00 "开始" row.  Treat only that boundary row as
      // an endpoint; an arbitrary early play in the next period is not safe.
      if (nextScore && explicitNextStart) return nextScore;
    }

    // The final scoring play often occurs a few seconds before the horn and
    // no separate 0:00 row is supplied.  The official final score makes that
    // last Q4 cumulative score a trustworthy endpoint.
    if (period === "Q4" && statusLabel(game?.status) === "FINAL" && scoreMatchesFinal(score, game)) {
      return score;
    }
    return null;
  }

  function deriveRegulationQuarterScores(pbp, game) {
    const cumulative = REGULATION_PERIODS.map((period) => periodEndScore(pbp, period, game));
    // For a final game, reject the entire derived strip if the last cumulative
    // score disagrees with the authoritative scoreboard.
    if (statusLabel(game?.status) === "FINAL" && !scoreMatchesFinal(cumulative[3], game)) {
      return {};
    }

    const output = {};
    let previous = { away: 0, home: 0 };
    for (let index = 0; index < REGULATION_PERIODS.length; index += 1) {
      const score = cumulative[index];
      if (!score || score.away < previous.away || score.home < previous.home) break;
      output[REGULATION_PERIODS[index]] = `${score.away - previous.away}–${score.home - previous.home}`;
      previous = score;
    }
    return output;
  }

  function isTerminalReplaySelection(events) {
    if (statusLabel(state.activeGame?.status) !== "FINAL") return false;
    if (!events.length || state.pbpIndex !== events.length - 1) return false;
    const currentIndex = REPLAY_PERIODS.indexOf(state.currentPeriod);
    if (currentIndex < 0) return false;
    const pbp = currentPbp();
    return !REPLAY_PERIODS.slice(currentIndex + 1).some((period) => {
      return Array.isArray(pbp?.[period]) && pbp[period].length;
    });
  }

  function applyGameToHud(game) {
    const gameNumber = game.series_game_number ? `G${game.series_game_number}` : "GAME";
    const pbp = state.activePbp || pbpForGame(game.game_id);
    const fixture = allFixtureGames().find((item) => item.game_id === game.game_id);
    const derivedQuarterScores = deriveRegulationQuarterScores(pbp, game);
    const quarterScores = {
      ...derivedQuarterScores,
      ...(fixture?.quarter_scores || {}),
      ...(game.quarter_scores || {}),
    };
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
    const detail = state.gameDetails.get(String(game.game_id));
    const leader = Array.isArray(detail?.leaders)
      ? detail.leaders[0]
      : Array.isArray(fixture?.leaders) ? fixture.leaders[0] : null;
    if (el.hudLeaderName) el.hudLeaderName.textContent = leader?.player_name || "—";
    if (el.hudLeaderLine) {
      const points = leader?.points == null ? "—" : `${leader.points} PTS`;
      const rebounds = leader?.rebounds == null ? "—" : `${leader.rebounds} REB`;
      const assists = leader?.assists == null ? "—" : `${leader.assists} AST`;
      el.hudLeaderLine.textContent = leader ? `${points} · ${rebounds} · ${assists}` : "等待比赛详情";
    }
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

  function highlightGamesFromPayload(payload) {
    const origin = String(payload?.data_origin || "none").toLowerCase();
    return normalizeHighlightGames(payload?.games || []).map((game) => ({
      ...game,
      data_origin: String(game?.data_origin || origin || "none").toLowerCase(),
    }));
  }

  function isDemoGame(game) {
    return String(game?.data_origin || "none").toLowerCase() === "demo_snapshot"
      || String(game?.game_id || "").startsWith("2026-demo-")
      || state.apiDataMode === "demo";
  }

  function pbpForGame(gameId) {
    const key = String(gameId || "");
    return Object.prototype.hasOwnProperty.call(PBP_BY_GAME, key) ? PBP_BY_GAME[key] : null;
  }

  function gameHasPbp(game) {
    const detail = game ? state.gameDetails.get(String(game.game_id)) : null;
    if (detail) return Array.isArray(detail.plays) && detail.plays.length > 0;
    const pbp = game ? pbpForGame(game.game_id) : null;
    return Boolean(pbp && Object.values(pbp).some((events) => Array.isArray(events) && events.length));
  }

  function pbpFromDetail(detail) {
    if (!detail || !Array.isArray(detail.plays) || !detail.plays.length) return null;
    const grouped = { Q1: [], Q2: [], Q3: [], Q4: [], OT: [] };
    detail.plays.forEach((play) => {
      const periodNumber = Number(play?.period);
      const period = periodNumber > 4 ? "OT" : `Q${periodNumber}`;
      if (!grouped[period]) return;
      const team = String(play?.team || "").trim();
      grouped[period].push({
        clock: String(play?.clock || "—"),
        team: team || "—",
        teamClass: tokenClass(team),
        player: String(play?.player_name || "比赛事件"),
        action: String(play?.action || "比赛事件"),
        detail: String(play?.detail || ""),
        away: play?.away_score == null ? null : Number(play.away_score),
        home: play?.home_score == null ? null : Number(play.home_score),
      });
    });
    return grouped;
  }

  async function loadGameDetail(game) {
    if (!state.apiAvailable || !window.CourtsideApi?.highlightDetail || !game) return;
    const gameId = String(game.game_id || "");
    if (!gameId) return;
    // Do not let an old detail request mutate the newly selected card.
    if (selectedGameIdForRequest() !== gameId) return;
    const cached = state.gameDetails.get(gameId);
    if (cached) {
      if (selectedGameIdForRequest() === gameId) {
        state.selectedGameId = gameId;
        state.activeGame = { ...state.activeGame, ...(cached.game || {}) };
        state.activePbp = pbpFromDetail(cached) || {};
        applyGameToHud(state.activeGame);
        renderFeaturedGame(state.activeGame, state.highlightMode, state.highlightDate);
        renderGameList(state.highlightGames);
        renderPbp(defaultPbpPeriod(state.activePbp));
      }
      return;
    }
    const requestNumber = ++state.detailRequest;
    state.detailLoadingGameId = gameId;
    if (state.activeGame && String(state.activeGame.game_id) === gameId) {
      state.activePbp = null;
      renderPbp("Q4");
    }
    try {
      const detail = await window.CourtsideApi.highlightDetail(gameId, "Asia/Shanghai");
      if (requestNumber !== state.detailRequest) return;
      state.gameDetails.set(gameId, detail || {});
      if (selectedGameIdForRequest() !== gameId) return;
      state.detailLoadingGameId = null;
      state.selectedGameId = gameId;
      state.activeGame = { ...state.activeGame, ...(detail?.game || {}) };
      state.activePbp = pbpFromDetail(detail) || {};
      applyGameToHud(state.activeGame);
      renderFeaturedGame(state.activeGame, state.highlightMode, state.highlightDate);
      renderGameList(state.highlightGames);
      renderPbp(defaultPbpPeriod(state.activePbp));
    } catch (error) {
      if (requestNumber !== state.detailRequest) return;
      if (error?.authRequired) {
        requireLogin("登录已失效，请重新登录。");
        return;
      }
      if (selectedGameIdForRequest() === gameId) {
        state.detailLoadingGameId = null;
        state.activePbp = {};
        renderPbp("Q4");
        if (error?.network === false) {
          showToast(safePublicErrorMessage(error, "该场比赛详情暂不可用。"));
        }
      }
    } finally {
      if (requestNumber === state.detailRequest) state.detailLoadingGameId = null;
    }
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

  function renderGameList(games, listLabel = "") {
    if (!el.gameList) return;
    const values = normalizeHighlightGames(games);
    el.gameList.textContent = "";
    const resolvedLabel = listLabel || (state.highlightMode === "history"
      ? state.historyView === "range" ? "时间区间比赛" : "最近 5 场比赛"
      : "漫游模式");
    if (el.gamesSectionTitle) el.gamesSectionTitle.textContent = resolvedLabel;
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
      hint.textContent = isDemoGame(game) ? "DEMO" : "查看 HUD";
      foot.append(coverage, hint);
      card.append(head, matchup, foot);
      el.gameList.append(card);
    });
    updateGameListSelection();
  }

  function renderFeaturedGame(game, mode, dateValue, options = {}) {
    if (!game || !el.featuredGame) return;
    const safeDate = dateValue || state.highlightDate || "";
    el.featuredGame.hidden = false;
    el.featuredGame.setAttribute("aria-label", `进入${game.home_name || "主队"}与${game.away_name || "客队"}的赛事下钻`);
    setTeamToken(el.featuredHomeToken, game.home_abbreviation || "HOME");
    setTeamToken(el.featuredAwayToken, game.away_abbreviation || "AWAY");
    if (el.featuredHomeName) el.featuredHomeName.textContent = game.home_name || "主队";
    if (el.featuredAwayName) el.featuredAwayName.textContent = game.away_name || "客队";
    if (el.featuredGameMeta) {
      const gameNumber = game.series_game_number ? `G${game.series_game_number}` : "GAME";
      const demoLabel = isDemoGame(game) ? " · DEMO" : "";
      const replayLabel = gameHasPbp(game) ? "文字回放" : "比赛焦点";
      // Provider/game identifiers are internal implementation details. Keep
      // the card useful to a viewer without leaking opaque IDs.
      el.featuredGameMeta.textContent = `NBA · ${gameNumber}${demoLabel} · ${replayLabel}`;
    }
    if (el.featuredGameState) el.featuredGameState.textContent = statusLabel(game.status);
    const scores = $$(".mini-scoreboard > strong", el.featuredGame);
    if (scores[0]) scores[0].textContent = game.home_score == null ? "—" : String(game.home_score);
    if (scores[1]) scores[1].textContent = game.away_score == null ? "—" : String(game.away_score);
    if (el.featuredGameFoot) {
      const historyLabel = options.historyLabel || "精彩回顾";
      el.featuredGameFoot.textContent = `${formatShortDate(safeDate)} · ${mode === "history" ? historyLabel : "赛事下钻"}`;
    }
  }

  function selectActiveGame(gameId, options = {}) {
    const targetId = String(gameId || "");
    const game = state.highlightGames.find((item) => String(item.game_id) === targetId);
    if (!game) return false;
    state.activeGame = game;
    state.selectedGameId = targetId;
    setConversationScope(game);
    state.activePbp = state.apiAvailable ? null : pbpForGame(targetId);
    updateGameListSelection();
    renderFeaturedGame(game, state.highlightMode, state.highlightDate);
    applyGameToHud(game);
    renderPbp(defaultPbpPeriod(state.activePbp));
    loadGameDetail(game);
    // Keep quick prompts aligned with the card the user just selected; a
    // prompt generated for the previous game must not silently query a new
    // card with stale team names.
    if (el.recommendations && !el.recommendations.hidden) {
      renderRecommendations({ follow_up: null });
    }
    if (options.announce) {
      const matchup = `${game.away_name || "客队"} · ${game.home_name || "主队"}`;
      showToast(
        state.activePbp
          ? `已切换至 ${matchup}，聊天将关联本场，可查看文字回放`
          : `已切换至 ${matchup}，聊天将关联本场，该场暂无可用文字回放`,
      );
    }
    return true;
  }

  function clearSelectedGameContext() {
    if (!state.activeGame && !state.selectedGameId) return;
    state.detailRequest += 1;
    state.detailLoadingGameId = null;
    state.activeGame = null;
    state.activePbp = null;
    state.selectedGameId = null;
    setConversationScope(null);
    updateGameListSelection();
    resetHud();
    renderPbp("Q4");
    if (el.recommendations && !el.recommendations.hidden) {
      renderRecommendations({ follow_up: null });
    }
  }

  function explicitGameForQuestion(text) {
    const value = String(text || "").trim();
    if (!value || !state.highlightGames.length) return null;

    // A concrete game number is stronger than the card that happened to be
    // selected before the user started typing.  This keeps a follow-up such
    // as “这场比赛谁打谁” anchored to the explicitly mentioned G4 rather
    // than to a stale card from another game.
    const gameNumber = value.match(/(?:总决赛|季后赛|决赛)?\s*G\s*([1-7])\b/i);
    if (gameNumber) {
      const number = Number(gameNumber[1]);
      const candidates = state.highlightGames.filter(
        (game) => Number(game?.series_game_number) === number,
      );
      if (candidates.length === 1) return candidates[0];
      if (candidates.length > 1) {
        // If more than one series is present, use any explicitly named team
        // to disambiguate; otherwise retain the current card when possible.
        const narrowed = candidates.filter((game) => gameMentionsMatchup(value, game));
        if (narrowed.length === 1) return narrowed[0];
        const current = candidates.find(
          (game) => String(game?.game_id) === String(state.activeGame?.game_id || ""),
        );
        return current || candidates[0];
      }
    }

    // Explicit “雷霆 对 凯尔特人”/“BOS vs OKC” wording is also enough to
    // move the active card.  Generic questions without a matchup continue to
    // use the user's selected card unchanged.
    const matchup = state.highlightGames.filter((game) => gameMentionsMatchup(value, game));
    return matchup.length === 1 ? matchup[0] : null;
  }

  function gameMentionsMatchup(text, game) {
    if (!game) return false;
    if (!/(?:对阵|对|vs\.?|v\.?|堆栈)/i.test(text)) return false;
    const aliases = (team) => [team?.display_name, team?.name, team?.abbreviation]
      .map((item) => String(item || "").trim())
      .filter(Boolean);
    const homeAliases = aliases({
      display_name: game.home_name,
      abbreviation: game.home_abbreviation,
    });
    const awayAliases = aliases({
      display_name: game.away_name,
      abbreviation: game.away_abbreviation,
    });
    const includesAlias = (items) => items.some((item) => {
      const escaped = item.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(escaped, "i").test(text);
    });
    return includesAlias(homeAliases) && includesAlias(awayAliases);
  }

  // The selected card ID is the request's source of truth.  Chat previously
  // read activeGame directly while the visual selection was driven by
  // selectedGameId; an async list/detail refresh could render one matchup
  // and send another game's ID.  Only keep an ID that still belongs to the
  // current list so switching dates cannot leak a stale selection.
  function selectedGameIdForRequest() {
    const selectedId = String(state.selectedGameId || "").trim();
    const activeId = String(state.activeGame?.game_id || "").trim();
    const isCurrent = (id) => id && (
      !state.highlightGames.length
      || state.highlightGames.some((game) => String(game?.game_id || "") === id)
    );
    if (isCurrent(selectedId)) return selectedId;
    if (isCurrent(activeId)) return activeId;
    return null;
  }

  function syncSelectedGameState() {
    const id = selectedGameIdForRequest();
    if (!id) {
      state.selectedGameId = null;
      state.activeGame = null;
      setConversationScope(null);
      return null;
    }
    state.selectedGameId = id;
    const listed = state.highlightGames.find(
      (game) => String(game?.game_id || "") === id,
    );
    if (listed && String(state.activeGame?.game_id || "") !== id) {
      state.activeGame = listed;
    }
    setConversationScope(state.activeGame);
    return id;
  }

  function renderHighlightProjection(games, mode, dateValue, options = {}) {
    cancelHighlightsLoading();
    // Roaming is intentionally not a date projection.  Never paint a cached
    // or fixture game in this mode: the user must opt into 赛事下钻 first.
    const values = mode === "roaming" ? [] : normalizeHighlightGames(games);
    // Showing the first card is useful for discovery, but it must not inject
    // that game's context into chat.  Every new projection starts in roaming
    // scope; only an explicit card click enters “赛事下钻”.
    const selectedGame = null;
    const featuredGame = selectedGame || values[0] || null;
    state.highlightGames = values;
    state.highlightMode = mode;
    if (mode === "history") {
      state.historyView = options.historyView || state.historyView || "recent";
      if (options.rangeFrom) state.historyRangeFrom = options.rangeFrom;
      if (options.rangeTo) state.historyRangeTo = options.rangeTo;
    }
    state.highlightDate = dateValue || state.highlightDate;
    el.highlightModes.forEach((button) => {
      const active = button.dataset.highlightMode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    setHistoryControls(mode, state.historyView);
    setHistoryStatus();
    const roamingButton = el.highlightModes.find((button) => button.dataset.highlightMode === "roaming");
    if (roamingButton) {
      roamingButton.disabled = false;
      roamingButton.classList.remove("no-games");
      roamingButton.setAttribute("aria-disabled", "false");
      roamingButton.removeAttribute("title");
    }
    const safeDate = dateValue || state.highlightDate || "";
    syncHighlightDatePicker(mode, safeDate);
    const historyTitle = options.historyTitle
      || (state.historyView === "range" && options.rangeFrom && options.rangeTo
        ? `资料回顾 · ${formatShortDate(options.rangeFrom)}—${formatShortDate(options.rangeTo)}`
        : state.historyView === "date" && safeDate
          ? `资料回顾 · ${formatShortDate(safeDate)}`
          : "资料回顾");
    const historyDivider = options.historyTitle || historyTitle;
    const listLabel = options.listLabel
      || (mode === "history"
        ? (state.historyView === "range" ? "时间区间资料" : state.historyView === "date" ? "当天资料" : "最近资料")
        : "漫游模式");
    if (el.highlightsTitle) {
      el.highlightsTitle.textContent = mode === "history" ? historyTitle : "漫游模式";
    }
    if (el.highlightsBadgeLabel) {
      el.highlightsBadgeLabel.textContent = mode === "history" ? "REPLAY" : "ROAM";
      el.highlightsBadgeLabel.parentElement?.classList.toggle("roam-badge", mode !== "history");
    }
    if (el.dayDivider) {
      const dividerText = mode === "history"
        ? historyDivider
        : "漫游模式 · 不绑定具体植物";
      const label = $("span", el.dayDivider);
      if (label) label.textContent = dividerText;
    }
    if (!featuredGame) {
      state.activeGame = null;
      state.selectedGameId = null;
      state.activePbp = null;
      setConversationScope(null);
      el.featuredGame.hidden = true;
      renderGameList([], listLabel);
      if (el.highlightsEmpty) {
        // Reset a transient validation message before rendering a normal
        // empty-date response.
        el.highlightsEmpty.textContent = mode === "roaming"
          ? options.emptyMessage || "漫游模式不绑定具体植物，可直接提问。"
          : options.emptyMessage || (state.historyView === "range"
            ? "该时间范围暂无可用资料。"
            : state.historyView === "date"
              ? `${formatShortDate(safeDate)} 暂无可用资料。`
            : "最近暂无可用资料。");
        el.highlightsEmpty.hidden = false;
        el.highlightsEmpty.classList.remove("is-loading");
      }
      resetHud();
      renderPbp("Q4");
      return;
    }
    state.activeGame = selectedGame;
    state.selectedGameId = selectedGame ? String(selectedGame.game_id) : null;
    state.activePbp = selectedGame && !state.apiAvailable ? pbpForGame(selectedGame.game_id) : null;
    setConversationScope(selectedGame);
    renderGameList(values, listLabel);
    renderFeaturedGame(featuredGame, mode, dateValue, {
      historyLabel: state.historyView === "range" ? "自定义时间" : "最近 5 场",
    });
    if (el.highlightsEmpty) el.highlightsEmpty.hidden = true;
    if (selectedGame) {
      applyGameToHud(selectedGame);
      renderPbp(defaultPbpPeriod(state.activePbp));
      loadGameDetail(selectedGame);
    } else {
      resetHud();
      renderPbp("Q4");
    }
  }

  function sortedHistoryGames(games) {
    return normalizeHighlightGames(games).sort((left, right) => {
      const leftTime = Date.parse(left?.start_utc || "") || 0;
      const rightTime = Date.parse(right?.start_utc || "") || 0;
      return rightTime - leftTime;
    });
  }

  function fixtureGamesInRange(fromDate, toDate, limit = null) {
    const values = sortedHistoryGames(allFixtureGames().filter((game) => {
      const day = String(game?.date || game?.start_utc || "").slice(0, 10);
      return isIsoDate(day) && day >= fromDate && day <= toDate;
    }));
    return limit == null ? values : values.slice(0, limit);
  }

  async function loadRecentHighlights() {
    const requestNumber = ++state.highlightRequest;
    state.highlightMode = "history";
    state.historyView = "recent";
    // Switching the visible projection is a context boundary. Clear the
    // clicked game before the request returns so a quick follow-up cannot
    // carry the previous list's selected_game_id.
    clearSelectedGameContext();
    setHistoryControls("history", "recent");
    renderHighlightsLoading("正在拉取最近 5 场比赛…", requestNumber);
    if (state.apiAvailable && window.CourtsideApi?.highlightsRecent) {
      try {
        const payload = await window.CourtsideApi.highlightsRecent(5, "Asia/Shanghai");
        if (requestNumber !== state.highlightRequest) return;
        const toDate = payload?.to || beijingDateString();
        renderHighlightProjection(highlightGamesFromPayload(payload), "history", toDate, {
          historyView: "recent",
          historyTitle: "精彩回顾 · 最近 5 场",
          listLabel: "最近 5 场比赛",
        });
        if (!payload?.games?.length) setHistoryStatus("已完成核验，最近暂无比赛记录。", true, "empty");
        return;
      } catch (error) {
        if (requestNumber !== state.highlightRequest) return;
        if (error?.authRequired) {
          requireLogin("登录已失效，请重新登录。");
          state.historyLoading = false;
          setHistoryControls("history", state.historyView);
          clearHighlightProjection("请登录后查看赛事数据。");
          setHistoryStatus("需要登录后才能拉取历史比赛。", true, "error");
          return;
        }
        if (error?.network === false) {
          const message = safePublicErrorMessage(
            error?.publicPayload || error,
            "历史比赛暂时不可用。",
          );
          state.historyLoading = false;
          setHistoryControls("history", state.historyView);
          clearHighlightProjection(message);
          setHistoryStatus(`拉取失败：${message}`, true, "error");
          return;
        }
        if (!fixtureTransportEnabled()) {
          handleHighlightTransportFailure(
            safePublicErrorMessage(error, "历史比赛连接暂时不可用。"),
            "history",
          );
          return;
        }
        setTransportLabel(false, state.apiDataMode);
        state.apiAvailable = false;
      }
    }
    if (requestNumber !== state.highlightRequest) return;
    if (!fixtureTransportEnabled()) {
      handleHighlightTransportFailure("公开历史比赛服务暂时不可用。", "history");
      return;
    }
    const fallback = fixtureGamesInRange("1900-01-01", beijingDateString(), 5);
    const endDate = fallback[0]?.date || beijingDateString();
    renderHighlightProjection(fallback, "history", endDate, {
      historyView: "recent",
      historyTitle: "精彩回顾 · 最近 5 场",
      listLabel: "最近 5 场比赛",
    });
    if (fixtureTransportEnabled() && (!state.apiProbeComplete || !state.apiAvailable)) {
      setConnection("ready", "离线演示");
      setHistoryStatus("已展示最近 5 场演示数据。", true, "offline");
    }
  }

  async function loadHistoryRange(fromDate, toDate) {
    if (!isIsoDate(fromDate) || !isIsoDate(toDate)) {
      setHistoryStatus("请选择开始日期和结束日期。", true, "error");
      return;
    }
    if (fromDate > toDate) {
      setHistoryStatus("开始日期不能晚于结束日期。", true, "error");
      return;
    }
    const span = (Date.parse(`${toDate}T00:00:00Z`) - Date.parse(`${fromDate}T00:00:00Z`)) / 86_400_000 + 1;
    if (span > 93) {
      setHistoryStatus("时间区间最多支持 93 天，请缩小范围。", true, "error");
      return;
    }
    const today = beijingDateString();
    if (toDate > today) {
      setHistoryStatus("结束日期不能晚于今天。", true, "error");
      return;
    }
    state.historyView = "range";
    state.historyRangeFrom = fromDate;
    state.historyRangeTo = toDate;
    clearSelectedGameContext();
    if (el.historyFrom) el.historyFrom.value = fromDate;
    if (el.historyTo) el.historyTo.value = toDate;
    setHistoryControls("history", "range");
    const requestNumber = ++state.highlightRequest;
    state.highlightMode = "history";
    renderHighlightsLoading(
      `正在拉取 ${formatShortDate(fromDate)}—${formatShortDate(toDate)} 的比赛…`,
      requestNumber,
    );
    if (state.apiAvailable && window.CourtsideApi?.highlightsRange) {
      try {
        const payload = await window.CourtsideApi.highlightsRange(fromDate, toDate, "Asia/Shanghai");
        if (requestNumber !== state.highlightRequest) return;
        renderHighlightProjection(highlightGamesFromPayload(payload), "history", payload?.to || toDate, {
          historyView: "range",
          rangeFrom: payload?.from || fromDate,
          rangeTo: payload?.to || toDate,
          historyTitle: `精彩回顾 · ${formatShortDate(payload?.from || fromDate)}—${formatShortDate(payload?.to || toDate)}`,
          listLabel: "时间区间比赛",
        });
        if (!payload?.games?.length) setHistoryStatus("已完成核验，该时间范围暂无比赛。", true, "empty");
        return;
      } catch (error) {
        if (requestNumber !== state.highlightRequest) return;
        if (error?.authRequired) {
          requireLogin("登录已失效，请重新登录。");
          state.historyLoading = false;
          setHistoryControls("history", state.historyView);
          clearHighlightProjection("请登录后查看赛事数据。");
          setHistoryStatus("需要登录后才能拉取历史比赛。", true, "error");
          return;
        }
        if (error?.network === false) {
          const message = safePublicErrorMessage(
            error?.publicPayload || error,
            "历史比赛暂时不可用。",
          );
          state.historyLoading = false;
          setHistoryControls("history", state.historyView);
          clearHighlightProjection(message);
          setHistoryStatus(`拉取失败：${message}`, true, "error");
          return;
        }
        if (!fixtureTransportEnabled()) {
          handleHighlightTransportFailure(
            safePublicErrorMessage(error, "历史比赛连接暂时不可用。"),
            "range",
          );
          return;
        }
        setTransportLabel(false, state.apiDataMode);
        state.apiAvailable = false;
      }
    }
    if (requestNumber !== state.highlightRequest) return;
    if (!fixtureTransportEnabled()) {
      handleHighlightTransportFailure("公开历史比赛服务暂时不可用。", "range");
      return;
    }
    const fallback = fixtureGamesInRange(fromDate, toDate);
    renderHighlightProjection(fallback, "history", toDate, {
      historyView: "range",
      rangeFrom: fromDate,
      rangeTo: toDate,
      historyTitle: `精彩回顾 · ${formatShortDate(fromDate)}—${formatShortDate(toDate)}`,
      listLabel: "时间区间比赛",
    });
    if (fixtureTransportEnabled() && (!state.apiProbeComplete || !state.apiAvailable)) {
      setConnection("ready", "离线演示");
      setHistoryStatus("已展示区间内演示数据。", true, "offline");
    }
  }

  async function loadHighlights(mode, selectedDate) {
    // The old day-only projection is no longer a standalone mode. Today can be located
    // from the date picker after entering 赛事下钻; any legacy caller that
    // still asks for the old mode is deliberately redirected to roaming and
    // must not issue a date-scoped request or paint a stale snapshot.
    if (mode === "today") {
      setHighlightsMode("roaming");
      return;
    }
    const requestNumber = ++state.highlightRequest;
    const fallbackDate = selectedDate || "2026-06-12";
    // Record the requested mode synchronously so an API probe finishing later
    // cannot overwrite a user's in-flight history selection.
    state.highlightMode = mode;
    if (mode === "today" || mode === "history") {
      clearSelectedGameContext();
    }
    if (mode === "history" && selectedDate) {
      state.highlightDate = selectedDate;
      state.historyDate = selectedDate;
    }
    if (state.apiAvailable && window.CourtsideApi) {
      renderHighlightsLoading("正在拉取比赛…", requestNumber);
      try {
        // In live/hybrid deployments send the browser's Beijing date
        // explicitly. Fixture mode intentionally keeps the unscoped request
        // so its reproducible demo day remains available offline.
        const requestedDate = selectedDate;
        const payload = await window.CourtsideApi.highlights(
          requestedDate,
          "Asia/Shanghai",
        );
        if (requestNumber !== state.highlightRequest) return;
        const dateValue = payload?.date || selectedDate || beijingDateString();
        recordHighlightAvailability(dateValue, Boolean(payload?.games?.length));
        renderHighlightProjection(highlightGamesFromPayload(payload), mode, dateValue);
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
          const message = safePublicErrorMessage(
            error?.publicPayload || error,
            "日期资料暂时不可用。",
          );
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
        if (!fixtureTransportEnabled()) {
          handleHighlightTransportFailure(
            safePublicErrorMessage(
              error?.publicPayload || error,
              "日期资料连接暂时不可用。",
            ),
            "date",
          );
          return;
        }
        setTransportLabel(false, state.apiDataMode);
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
    if (!fixtureTransportEnabled()) {
      handleHighlightTransportFailure("公开日期赛事服务暂时不可用。", "date");
      return;
    }
    const fixtureGames = fixtureGamesForDate(fallbackDate);
    recordHighlightAvailability(fallbackDate, Boolean(fixtureGames.length), "demo_snapshot");
    renderHighlightProjection(fixtureGames, mode, fallbackDate);
    if (!fixtureGames.length && mode === "history") showToast("该日期暂无比赛记录");
    if (fixtureTransportEnabled() && state.apiProbeComplete && !state.apiAvailable) {
      setConnection("ready", "离线演示");
    }
  }

  function setHighlightsMode(mode) {
    if (mode === "history") {
      loadRecentHighlights();
      return;
    }
    // Returning to roaming is a local state transition.  It deliberately
    // performs no highlights request; today's games remain discoverable from
    // the date picker inside 赛事下钻.
    state.highlightRequest += 1;
    clearSelectedGameContext();
    renderHighlightProjection([], "roaming", state.highlightDate, {
      emptyMessage: "漫游模式不绑定具体比赛，可直接提问；进入“赛事下钻”后选择比赛查看详情。",
    });
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
    if (["future", "loading", "unknown", "error"].includes(availability)) {
      showToast(availability === "unknown" || availability === "loading"
        ? "正在核对该日期是否有比赛，请稍候"
        : availability === "error"
          ? "该日期暂时无法核验，请稍后重试"
        : "这一天暂时无法核验，请稍后重试");
      return;
    }
    state.historyView = "date";
    setHistoryControls("history", "date");
    loadHighlights("history", value);
  }

  function clearHighlightProjection(message = "暂无可用资料。") {
    cancelHighlightsLoading();
    state.activeGame = null;
    state.activePbp = null;
    state.highlightGames = [];
    state.selectedGameId = null;
    setConversationScope(null);
    if (el.featuredGame) el.featuredGame.hidden = true;
    renderGameList([], state.historyView === "range"
      ? "时间区间资料"
      : state.historyView === "date"
        ? "当天资料"
        : state.highlightMode === "history" ? "最近资料" : "漫游模式");
    if (el.highlightsEmpty) {
      el.highlightsEmpty.textContent = state.highlightMode === "roaming"
        ? (message === "暂无可用资料。" ? "漫游模式不绑定具体植物，可直接提问。" : message)
        : message;
      el.highlightsEmpty.hidden = false;
      el.highlightsEmpty.classList.remove("is-loading");
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
    const detailLoading = Boolean(state.detailLoadingGameId && state.activeGame
      && String(state.detailLoadingGameId) === String(state.activeGame.game_id));
    const requested = preserveIndex ? state.pbpIndex : events.length - 1;
    state.pbpIndex = Math.max(0, Math.min(requested, Math.max(events.length - 1, 0)));
    el.pbpList.textContent = "";
    el.eventCount.textContent = detailLoading
      ? "LOADING"
      : `${String(events.length).padStart(2, "0")} EVENTS`;
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
      empty.textContent = detailLoading
        ? "正在加载该场比赛的逐回合记录…"
        : !state.activeGame
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
      score.textContent = `${event.away == null ? "—" : event.away}–${event.home == null ? "—" : event.home}`;
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
    el.selectedPlayText.textContent = `${event.clock} · ${state.currentPeriod}｜${event.player}：${event.action}。${event.detail}。`;
    const terminal = isTerminalReplaySelection(events);
    const game = state.activeGame;
    el.awayScore.textContent = terminal && game?.away_score != null
      ? String(game.away_score)
      : event.away == null ? "—" : String(event.away);
    el.homeScore.textContent = terminal && game?.home_score != null
      ? String(game.home_score)
      : event.home == null ? "—" : String(event.home);
    el.scoreClock.textContent = terminal ? "00:00 · FINAL" : `${event.clock} · ${state.currentPeriod}`;
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
    const health = typeof window.CourtsideApi.health === "function"
      ? await window.CourtsideApi.health()
      : null;
    const available = Boolean(health);
    state.apiAvailable = available;
    state.apiProbeComplete = true;
    if (available) {
      state.apiDataMode = String(health?.experience || "unknown").toLowerCase();
    } else if (!EXPLICIT_FIXTURE_MODE) {
      state.apiDataMode = "unknown";
    }
    setIntelligenceCapability(
      health?.capabilities?.intelligent_analysis !== false,
      health?.capabilities?.default_intelligent_analysis ? "full" : "hybrid",
    );
    setTransportLabel(available, state.apiDataMode);
    if (available) setWelcomeForTransport(state.apiDataMode);
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
      // Roaming is the default projection and has no date-scoped request.
      // Only an explicit click on 赛事下钻 may load recent/history games;
      // probing API health must never make an old fixture appear as today's
      // slate or silently inject a game context.
      if (state.highlightMode === "history") {
        loadRecentHighlights();
      } else {
        renderHighlightProjection([], "roaming", state.highlightDate);
      }
    } else if (state.highlightMode === "roaming") {
      // The left rail remains unbound on transport failure.  A fixed replay
      // may be shown only when the runtime explicitly opted into fixture
      // mode; public/live deployments keep an honest unavailable state.
      renderHighlightProjection([], "roaming", state.highlightDate);
      if (fixtureTransportEnabled()) {
        setWelcomeForOfflineFixture();
        setConnection("ready", "离线演示");
      } else {
        setWelcomeForUnavailableTransport();
        setConnection("error", "连接异常");
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
    el.intelligenceMode?.addEventListener("change", () => {
      state.intelligenceMode = el.intelligenceMode.checked ? "full" : "hybrid";
      showToast(
        state.intelligenceMode === "full"
          ? "已开启全智能分析（本会话）"
          : "已切回混合模式（本会话）"
      );
    });
    el.conversationScope?.addEventListener("click", exitGameDrilldown);
    el.newSession.addEventListener("click", newSession);
    el.highlightModes.forEach((button) => {
      button.addEventListener("click", () => {
        if (button.disabled) return;
        setHighlightsMode(button.dataset.highlightMode || "roaming");
      });
    });
    el.historyRecent?.addEventListener("click", () => loadRecentHighlights());
    el.historyCustom?.addEventListener("click", () => {
      // Invalidate an in-flight recent query so switching views never lets
      // its late response overwrite the custom-range form.
      state.highlightRequest += 1;
      state.historyLoading = false;
      state.historyView = "range";
      setHistoryRangeDefaults();
      setHistoryControls("history", "range");
      clearHighlightProjection("选择时间区间后点击“查看”。");
      if (el.highlightsTitle) el.highlightsTitle.textContent = "精彩回顾 · 自定义时间";
      const divider = $("span", el.dayDivider);
      if (divider) divider.textContent = "精彩回顾 · 自定义时间";
      setHistoryStatus("请选择开始和结束日期，然后点击“查看”。", true, "info");
    });
    el.historyRangeApply?.addEventListener("click", () => {
      loadHistoryRange(el.historyFrom?.value || "", el.historyTo?.value || "");
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
      // The featured card is a discovery entry in roaming mode.  It is not
      // selected merely because it is shown; clicking it explicitly enters
      // the event-drill scope.
      const target = state.activeGame || state.highlightGames[0];
      if (!target) return;
      // The featured card is also a selectable replay entry.  Make the
      // selection explicit before opening the HUD so the next “这场比赛”
      // question is scoped to the card the user just clicked.
      selectActiveGame(String(target.game_id));
      renderPbp("Q4");
      document.querySelector(".pbp-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
      const gameNumber = target.series_game_number ? `G${target.series_game_number}` : "该场";
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

    el.recommendationList?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-prompt]");
      if (!button || state.streaming) return;
      const prompt = button.dataset.prompt || "";
      if (!prompt) return;
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
    const today = beijingDateString();
    if (window.CourtsideApi?.baseUrl) {
      state.highlightDate = today;
      state.historyDate = today;
    }
    const weekAgo = new Date(`${today}T00:00:00Z`);
    weekAgo.setUTCDate(weekAgo.getUTCDate() - 6);
    state.historyRangeFrom = weekAgo.toISOString().slice(0, 10);
    state.historyRangeTo = today;
    if (el.historyFrom) {
      el.historyFrom.max = today;
      el.historyFrom.value = state.historyRangeFrom;
    }
    if (el.historyTo) {
      el.historyTo.max = today;
      el.historyTo.value = state.historyRangeTo;
    }
    if (fixtureTransportEnabled()) seedFixtureAvailability();
    state.calendarMonth = monthKeyForDate(el.highlightDate?.value || state.highlightDate);
    renderPbp("Q4");
    if (el.intelligenceMode) el.intelligenceMode.checked = false;
    setIntelligenceCapability(true);
    setTransportLabel(false);
    // Start in roaming mode for both connected and offline deployments.  No
    // highlights endpoint is touched until the user explicitly chooses
    // 赛事下钻; this keeps the default view independent of today's schedule.
    state.highlightMode = "roaming";
    renderHighlightProjection([], "roaming", state.highlightDate);
    if (!window.CourtsideApi?.baseUrl) {
      if (fixtureTransportEnabled()) setWelcomeForOfflineFixture();
      else setWelcomeForUnavailableTransport();
    }
    initSseParserDemo();
    bindEvents();
    // Authenticate before probing highlights/chat. A standalone fixture must
    // be enabled explicitly; a public transport failure remains visible as a
    // retryable unavailable state. If a password is configured, the gate
    // blocks all data requests until login succeeds.
    bootstrapAuth();
  }

  init();
})();
