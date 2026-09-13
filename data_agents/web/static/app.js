// The turn card, driven by the Turn Event stream. One POST, one streamed response, one card fetched from the server when the
// Turn closes, so the live card and a reopened Session share their markup. Everything else on the page is HTMX.
const root = document.querySelector(".session");
const sid = root.dataset.session;
const cards = document.getElementById("cards");
const progress = document.getElementById("progress");
const send = document.getElementById("send");
const field = (id) => document.getElementById(id);
const value = (id) => field(id).value || null;

const LINES = {
  stage_started: (e) => `${e.stage} …`,
  sql_written: (e) => `  sql: ${e.sql.replace(/\s+/g, " ").slice(0, 110)}`,
  rows_fetched: (e) => `  rows: ${e.row_count}${e.truncated ? " (truncated)" : ""} in ${Math.round(e.latency_ms)} ms`,
  sql_failed: (e) => `  sql failed, retrying: ${e.reason.slice(0, 100)}`,
  model_call_done: (e) => `✓ ${e.stage}: ${e.requests} model call${e.requests === 1 ? "" : "s"} in ${(e.latency_ms / 1000).toFixed(1)} s`,
  check_fired: (e) => `  ${e.check} refused the answer, retrying: ${e.detail.slice(0, 80)}`,
  grain_probed: (e) => `  grain probe (${e.knob}): the two readings ${e.agreed ? "agree" : "differ"}`,
  reshape_requested: (e) => `  reshape: ${e.instruction}`,
  plan_chosen: (e) => e.plan === "present_only" ? "  presenting the previous result" : e.reason === "reshape" ? "  the chart asked for new data; running the analysis" : "  new analysis",
  chart_ready: (e) => `  chart: ${e.spec.chart_type} of ${e.spec.y} by ${e.spec.x}`,
  chart_skipped: (e) => `  no chart: ${e.reason}${e.value ? " — " + e.value : ""}`,
  turn_error: (e) => `✗ ${e.stage}: ${e.message}${e.detail ? "\n    " + e.detail : ""}`,  // detail arrives for admins only
};

function say(text) {
  progress.hidden = false;
  progress.textContent += text + "\n";
  progress.scrollTop = progress.scrollHeight;
}

function showTrace(metrics) {
  const panel = document.getElementById("trace");  // admins only; the drawer is a role check
  if (!panel) return;
  const stages = Object.entries(metrics.latency_ms).map(([k, ms]) => `${k} ${ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms) + " ms"}`);
  const cached = metrics.cache_read_tokens ? ` (${metrics.cache_read_tokens.toLocaleString()} cached)` : "";
  panel.textContent = [stages.join(" · "), `${metrics.requests} model calls`,
                       `tokens ${metrics.input_tokens.toLocaleString()} in${cached} / ${metrics.output_tokens.toLocaleString()} out`,
                       metrics.cost_usd === null ? "cost n/a" : `$${metrics.cost_usd.toFixed(4)}`].join("\n");
}

// Every card the server rendered carries the Vega-Lite of the chart its Turn drew, so reopening a Session redraws it.
function drawCharts(within) {
  within.querySelectorAll(".chart[data-spec]").forEach((target) => {
    if (target.dataset.drawn) return;
    target.dataset.drawn = "1";
    vegaEmbed(target, JSON.parse(target.dataset.spec), {actions: false, renderer: "svg"});
  });
}

// A question to put in the box, shown the way an editor shows a completion: greyed behind the cursor until Tab takes it.
let ghost = "";
const hint = document.getElementById("tab-hint");

async function suggest() {
  const box = field("question");
  ghost = "";
  hint.hidden = true;
  try {
    const text = (await (await fetch(`/workspace/sessions/${sid}/suggestion`)).text()).trim();
    if (!text) return;
    ghost = text;
    box.placeholder = text;
    hint.hidden = box.value.trim() !== "";
  } catch (e) {
    // no suggestion; the box keeps its own prompt
  }
}

// Server-sent events over a POST, which EventSource cannot do: read the body as it arrives and cut it on the blank line.
async function stream(url, body) {
  progress.textContent = "";
  send.disabled = true;
  let failed = false;
  try {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!response.ok || !response.body) {
      say(`✗ ${response.status}: ${(await response.text()).slice(0, 200)}`);
      return true;
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", cut;
    for (;;) {
      const {value: chunk, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(chunk, {stream: true});
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const event = JSON.parse(block.slice(block.indexOf("\ndata: ") + 7));
        if (event.kind === "turn_finished") showTrace(event.metrics);
        if (event.kind === "turn_error") failed = true;
        if (LINES[event.kind]) say(LINES[event.kind](event));
      }
    }
  } catch (e) {
    say(`✗ ${e}`);
    failed = true;
  } finally {
    send.disabled = false;
  }
  return failed;
}

field("question").addEventListener("keydown", (key) => {
  if (key.key === "Tab" && ghost && !key.target.value.trim()) {
    key.preventDefault();  // only when the box is empty and there is something to take; Tab still moves focus otherwise
    key.target.value = ghost;
    hint.hidden = true;
  }
});

field("question").addEventListener("input", (typing) => {
  hint.hidden = typing.target.value.trim() !== "" || !ghost;
});

field("ask").addEventListener("submit", async (submit) => {
  submit.preventDefault();
  const question = field("question").value.trim();
  if (!question) return;
  const hints = {chart_type: value("chart_type"), sort: value("sort")};
  const n = cards.querySelectorAll(".turn").length + 1;  // every Turn records a row, including a Clarification
  if (await stream(`/workspace/sessions/${sid}/turns`, {question, chart: field("chart").checked, hints: hints.chart_type || hints.sort ? hints : null})) return;
  field("question").value = "";
  const html = await (await fetch(`/workspace/sessions/${sid}/turns/${n}`)).text();
  cards.insertAdjacentHTML("beforeend", html);
  drawCharts(cards.lastElementChild);
  cards.lastElementChild.scrollIntoView({behavior: "smooth", block: "start"});
  suggest();
});

drawCharts(document);
suggest();
