/* WanderWarm 前端 —— 消费 POST /api/plan/stream 的真实 SSE 事件流。
   所有动态内容用 textContent 渲染, 不信任任何服务端文本中的 HTML。 */

const form = document.querySelector("#travel-form");
const planButton = document.querySelector("#plan-button");
const stopButton = document.querySelector("#stop-button");
const introView = document.querySelector("#intro-view");
const planningView = document.querySelector("#planning-view");
const resultView = document.querySelector("#result-view");
const errorView = document.querySelector("#error-view");
const errorMessage = document.querySelector("#error-message");
const timeline = document.querySelector("#agent-timeline");
const summary = document.querySelector("#stream-summary");
const progressBar = document.querySelector("#progress-bar");
const elapsedTime = document.querySelector("#elapsed-time");
const travelerCount = document.querySelector("#traveler-count");
const completedTrace = document.querySelector("#completed-trace");

let travelers = 2;
let activeController = null;
let elapsedTimer = null;
let completedAgents = new Set();
let finalPlan = null;

const AGENTS = [
  ["PreferenceAgent", "理解旅行偏好", "整理预算、日期与兴趣标签", "♡"],
  ["DestinationAgent", "推荐目的地", "RAG 检索知识库并匹配偏好", "⌖"],
  ["FlightAgent", "搜索往返航班", "与酒店、活动并行执行", "✈"],
  ["HotelAgent", "筛选舒适酒店", "平衡位置、评分与每晚价格", "⌂"],
  ["ActivityAgent", "编排每日活动", "组合兴趣与城市体验", "◇"],
  ["BudgetAgent", "校验旅行预算", "汇总费用并检查剩余空间", "¥"],
  ["ReplanAgent", "智能预算优化", "超预算时自主调用工具调整", "↻"],
];

function setMode(mode) {
  document.body.dataset.mode = mode;
  introView.hidden = mode !== "idle";
  planningView.hidden = mode !== "planning";
  resultView.hidden = mode !== "completed";
  errorView.hidden = mode !== "failed";
  stopButton.hidden = mode !== "planning";
  planButton.disabled = mode === "planning";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const yuan = (n) => `¥${Math.round(Number(n) || 0).toLocaleString("zh-CN")}`;

/* ── Agent 时间线 ───────────────────────────── */

function buildTimeline() {
  timeline.replaceChildren();
  AGENTS.forEach(([name, title, detail, icon]) => {
    const li = el("li", "agent-event");
    li.dataset.agent = name;
    const dot = el("span", "event-dot", icon);
    const copy = el("div", "event-copy");
    copy.append(el("strong", "", title), el("p", "", detail), el("small", "", "等待执行"));
    li.append(dot, copy);
    timeline.append(li);
  });
}

function markAgent(name, state, note) {
  const item = timeline.querySelector(`[data-agent="${name}"]`);
  if (!item) return;
  item.className = `agent-event ${state}`;
  if (note) item.querySelector("small").textContent = note;
  if (state === "completed") item.querySelector(".event-dot").textContent = "✓";
}

function addTrace(text, seconds) {
  const li = el("li");
  li.append(el("i", "", "✓"), document.createTextNode(text));
  if (seconds !== undefined) li.append(el("span", "", `${seconds.toFixed(1)}s`));
  completedTrace.append(li);
}

function updateProgress() {
  const pct = Math.min(88, 6 + completedAgents.size * 12);
  progressBar.style.width = `${pct}%`;
}

/* ── SSE 事件分发 ───────────────────────────── */

function handleEvent(event) {
  const { type, agent, message, data } = event;

  if (type === "agent_started") {
    markAgent(agent, "running", message || "执行中…");
  } else if (type === "agent_completed") {
    completedAgents.add(agent);
    markAgent(agent, "completed", message || "已完成");
    const secs = data && data.duration_ms !== undefined ? data.duration_ms / 1000 : undefined;
    addTrace(`${agent} · ${message || "已完成"}`, secs);
    updateProgress();
  } else if (type === "agent_failed") {
    markAgent(agent, "running", message || "已降级处理");
    addTrace(`${agent} · ${message || "已降级处理"}`);
  } else if (type === "rag_result") {
    const hits = (data && data.hits) || [];
    addTrace(`RAG 检索 · ${message}${hits.length ? `（${hits.slice(0, 3).join("、")}…）` : ""}`);
  } else if (type === "tool_called") {
    const args = data && data.arguments ? JSON.stringify(data.arguments) : "";
    markAgent("ReplanAgent", "running", `${message} ${args}`);
    addTrace(`ReplanAgent · ${message} ${args}`);
  } else if (type === "summary_delta") {
    const cursor = summary.querySelector(".typing-cursor");
    summary.insertBefore(document.createTextNode((data && data.delta) || ""), cursor);
    progressBar.style.width = "94%";
  } else if (type === "plan_completed") {
    finalPlan = data;
    progressBar.style.width = "100%";
  } else if (type === "error") {
    throw new Error(message || "规划失败");
  }
}

/* ── SSE 消费 (fetch + ReadableStream, 处理跨 chunk 分包) ── */

async function streamPlan(payload, signal) {
  const response = await fetch("/api/plan/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    let detail = `服务异常 (HTTP ${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : "请求参数有误，请检查表单";
    } catch (_) { /* 保持默认提示 */ }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = block.split("\n").find((l) => l.startsWith("data: "));
      if (dataLine) handleEvent(JSON.parse(dataLine.slice(6)));
    }
  }
  if (!finalPlan) throw new Error("连接中断，未收到完整结果");
}

/* ── 结果渲染 ───────────────────────────────── */

function tile(icon, small, title, desc, price) {
  const article = el("article", "result-tile");
  const body = el("div");
  body.append(el("small", "", small), el("h3", "", title), el("p", "", desc));
  article.append(el("span", "tile-icon", icon), body, el("strong", "", price));
  return article;
}

function renderResult(plan) {
  const dest = plan.destination || {};
  const budget = plan.budget || {};
  const days = plan.days || [];

  document.querySelector("#result-title").textContent =
    `${dest.city || "未知目的地"} · ${days.length}日行程`;
  document.querySelector("#result-subtitle").textContent = dest.description || plan.reasoning || "";
  document.querySelector("#result-total").textContent = yuan(budget.total);
  document.querySelector("#result-budget").textContent = `总预算 ${yuan(budget.budget)}`;
  document.querySelector("#result-duration").textContent =
    `${days.length} 天 ${Math.max(days.length - 1, 0)} 夜`;

  const tiles = document.querySelector("#result-tiles");
  tiles.replaceChildren();
  const out = plan.flight && plan.flight.outbound;
  if (out) {
    tiles.append(tile("✈", "往返航班", `${out.airline} ${out.flight_no}`,
      `${out.departure_city} → ${out.arrival_city}${out.stops ? ` · ${out.stops}次中转` : " · 直飞"}`,
      yuan(plan.flight.total)));
  }
  if (plan.hotel) {
    tiles.append(tile("⌂", `推荐酒店 · ${plan.hotel.nights} 晚`, plan.hotel.name,
      `${plan.hotel.star_rating}星 · 用户评分 ${plan.hotel.user_rating}`, yuan(plan.hotel.total)));
  }

  const highlights = document.querySelector("#result-highlights");
  highlights.replaceChildren();
  (dest.highlights || []).slice(0, 4).forEach((h) => highlights.append(el("span", "", h)));

  const bars = document.querySelector("#result-budget-bars");
  bars.replaceChildren();
  const rows = [
    ["航班", budget.flight], ["酒店", budget.hotel], ["活动", budget.activity],
    ["剩余", Math.max(budget.remaining || 0, 0)],
  ];
  rows.forEach(([label, value]) => {
    const p = el("p");
    const track = el("i");
    const fill = el("b");
    fill.style.width = `${Math.min(100, Math.max(2, (value / (budget.budget || 1)) * 100)).toFixed(1)}%`;
    track.append(fill);
    p.append(el("span", "", label), track, el("strong", "", yuan(value)));
    bars.append(p);
  });

  const warnings = document.querySelector("#result-warnings");
  warnings.textContent = (plan.warnings || []).join("；");

  const dayList = document.querySelector("#result-days");
  dayList.replaceChildren();
  days.forEach((day, index) => {
    const article = el("article");
    const body = el("div");
    const names = (day.activities || []).map((a) => a.name).join(" · ") || "自由活动";
    body.append(el("h3", "", day.date), el("p", "", names));
    article.append(el("span", "", `DAY ${index + 1}`), body, el("strong", "", yuan(day.day_cost)));
    dayList.append(article);
  });
}

/* ── 主流程 ─────────────────────────────────── */

function resetPlanningView() {
  buildTimeline();
  completedAgents = new Set();
  finalPlan = null;
  completedTrace.replaceChildren();
  summary.replaceChildren(el("span", "typing-cursor"));
  progressBar.style.width = "4%";
  let seconds = 0;
  elapsedTime.textContent = "00:00";
  clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    seconds += 1;
    const m = String(Math.floor(seconds / 60)).padStart(2, "0");
    const s = String(seconds % 60).padStart(2, "0");
    elapsedTime.textContent = `${m}:${s}`;
  }, 1000);
}

function collectPayload() {
  const interests = [...form.querySelectorAll(".interest-options input:checked")].map((i) => i.value);
  return {
    budget: Number(form.elements.budget.value),
    departure_city: form.elements.departure_city.value.trim(),
    start_date: form.elements.start_date.value,
    end_date: form.elements.end_date.value,
    travel_style: form.elements.travel_style.value,
    num_travelers: travelers,
    interests,
    notes: form.elements.notes.value.trim(),
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const start = new Date(form.elements.start_date.value);
  const end = new Date(form.elements.end_date.value);
  if (end <= start) { alert("返回日期需要晚于出发日期"); return; }
  if ((end - start) / 86400000 > 14) { alert("演示版最长支持 14 天行程"); return; }

  activeController?.abort();
  activeController = new AbortController();
  setMode("planning");
  resetPlanningView();
  try {
    await streamPlan(collectPayload(), activeController.signal);
    // 未执行的 Agent (如预算内无需 Replan) 标注说明
    timeline.querySelectorAll(".agent-event:not(.completed)").forEach((item) => {
      item.querySelector("small").textContent = "本次无需执行";
    });
    renderResult(finalPlan);
    setMode("completed");
  } catch (error) {
    if (error.name === "AbortError") {
      setMode("idle");
    } else {
      errorMessage.textContent = error.message || "连接似乎中断了，请稍后重新尝试。";
      setMode("failed");
    }
  } finally {
    clearInterval(elapsedTimer);
    activeController = null;
  }
});

stopButton.addEventListener("click", () => activeController?.abort());
document.querySelector("#restart-button").addEventListener("click", () => setMode("idle"));
document.querySelector("#retry-button").addEventListener("click", () => setMode("idle"));

document.querySelectorAll("[data-count]").forEach((button) => {
  button.addEventListener("click", () => {
    travelers = button.dataset.count === "plus" ? Math.min(10, travelers + 1) : Math.max(1, travelers - 1);
    travelerCount.textContent = String(travelers);
  });
});

document.querySelectorAll("[data-result-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-result-tab]").forEach((tab) => tab.classList.toggle("active", tab === button));
    document.querySelectorAll("[data-result-panel]").forEach((panel) => { panel.hidden = panel.dataset.resultPanel !== button.dataset.resultTab; });
  });
});

/* ── 粒子背景 (装饰层, 遵循 reduced-motion) ───── */

function startParticles() {
  const canvas = document.querySelector("#particle-canvas");
  const context = canvas.getContext("2d");
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let particles = [];
  let frame = 0;

  const makeParticle = () => ({ x: Math.random() * innerWidth, y: Math.random() * innerHeight, radius: 1 + Math.random() * 2.6, vx: -.12 + Math.random() * .24, vy: -.18 - Math.random() * .2, alpha: .06 + Math.random() * .16, color: ["231,137,80", "242,199,170", "201,95,57"][Math.floor(Math.random() * 3)] });
  function resize() { const ratio = Math.min(devicePixelRatio || 1, 2); canvas.width = innerWidth * ratio; canvas.height = innerHeight * ratio; canvas.style.width = `${innerWidth}px`; canvas.style.height = `${innerHeight}px`; context.setTransform(ratio, 0, 0, ratio, 0, 0); particles = Array.from({ length: innerWidth < 700 ? 22 : 46 }, makeParticle); }
  function draw() { context.clearRect(0, 0, innerWidth, innerHeight); particles.forEach((p) => { p.x += p.vx; p.y += p.vy; if (p.y < -10) { p.y = innerHeight + 10; p.x = Math.random() * innerWidth; } context.beginPath(); context.fillStyle = `rgba(${p.color},${p.alpha})`; context.arc(p.x, p.y, p.radius, 0, Math.PI * 2); context.fill(); }); frame = requestAnimationFrame(draw); }
  function visibility() { if (document.hidden) cancelAnimationFrame(frame); else draw(); }
  addEventListener("resize", resize); document.addEventListener("visibilitychange", visibility); resize(); draw();
}

startParticles();
setMode("idle");
