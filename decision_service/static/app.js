const $ = (selector) => document.querySelector(selector);
let catalog = {};
let selectionMode = "multi";
let caseSamples = [];
let selectedCaseTask = "";
let currentState = null;
let currentStructured = null;
let stateRequest = 0;
let modelStreamChannel = null;
let modelStreamPending = "";
let modelStreamFrame = null;

const SAMPLE = [
  {event_time: "2026-09-11T12:36:14.040000", station: "姑苏", host: "P221VCC2", system_alarm: "B", device: "极2低端阀1许继阀冷B2", content: "极2低端阀1许继阀冷B2_E4.M01风机运行消失 消失", alarm_level: "3"},
  {event_time: "2026-09-11T12:36:14.858000", station: "姑苏", host: "P221VCC2", system_alarm: "A", device: "极2低端阀1许继阀冷A2", content: "极2低端阀1许继阀冷A2_E4.M01风机运行消失 消失", alarm_level: "3"},
  {event_time: "2026-09-11T12:36:14.929000", station: "姑苏", host: "P221VCC2", system_alarm: "A", device: "极2低端阀1许继阀冷A2", content: "极2低端阀1许继阀冷A2_泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现 出现", alarm_level: "3"}
];

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
    return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch];
  });
}
function showNotice(message) {
  const node = $("#notice");
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
}
function renderCaseSamples() {
  const select = $("#case-sample");
  if (!select) return;
  select.innerHTML = "<option value=''>选择一个与训练案例对应的合成日志…</option>" +
    caseSamples.map(function (sample) {
      return "<option value='" + esc(sample.case_id) + "'>" + esc(sample.case_id + " · " + sample.title + " · gold: " + sample.gold_decision) + "</option>";
    }).join("");
}
function loadCaseSample(caseId) {
  const sample = caseSamples.find(function (item) { return item.case_id === caseId; });
  if (!sample) return;
  selectedCaseTask = sample.task_id;
  $("#logs").value = sample.raw_tsv;
  $("#station").value = "姑苏";
  $("#task").value = "换流站监盘：" + sample.family_id + " " + sample.title + "，根据原始日志归纳任务级 State 并判断当前规程分支";
  $("#logs").dispatchEvent(new Event("input"));
  $("#case-sample-hint").textContent = "已载入 " + sample.case_id + "；对应 gold 分支仅用于离线对照：" + sample.gold_decision;
  syncTaskSelection();
  $("#state-section").classList.add("hidden");
  $("#decision-section").classList.add("hidden");
  setFlow("input");
  showNotice("");
}
function syncTaskSelection() {
  if (selectionMode !== "single" || !selectedCaseTask) return;
  document.querySelectorAll("#tasks input").forEach(function (input) {
    input.checked = input.value === selectedCaseTask;
  });
}
async function api(url, options) {
  const opts = Object.assign({headers: {"Content-Type": "application/json"}}, options || {});
  const response = await fetch(url, opts);
  const body = await response.json().catch(function () { return {}; });
  if (!response.ok) throw new Error(body.detail || "请求失败");
  return body;
}
function setFlow(step) {
  const order = ["input", "state", "decision"];
  document.querySelectorAll(".flow-item").forEach(function (node) {
    const index = order.indexOf(node.dataset.flow);
    node.classList.toggle("active", node.dataset.flow === step);
    node.classList.toggle("done", index < order.indexOf(step));
  });
}
function stage(message, busy) {
  $("#progress").classList.toggle("hidden", !busy);
  $("#progress-text").textContent = message;
}
function resetModelStream() {
  modelStreamChannel = null;
  modelStreamPending = "";
  if (modelStreamFrame) cancelAnimationFrame(modelStreamFrame);
  modelStreamFrame = null;
  $("#glm-stream-output").textContent = "";
  $("#glm-stream-status").textContent = "连接 GLM…";
}
function flushModelStream() {
  modelStreamFrame = null;
  if (!modelStreamPending) return;
  const output = $("#glm-stream-output");
  output.textContent += modelStreamPending;
  modelStreamPending = "";
  output.scrollTop = output.scrollHeight;
}
function appendModelStream(data) {
  const channel = data.channel || "content";
  const labels = {reasoning: "推理片段", content: "模型输出"};
  if (modelStreamChannel !== channel) {
    modelStreamChannel = channel;
    const output = $("#glm-stream-output");
    if (output.textContent) output.textContent += "\n\n";
    output.textContent += "【" + (labels[channel] || channel) + "】\n";
  }
  modelStreamPending += data.text || "";
  if (!modelStreamFrame) modelStreamFrame = requestAnimationFrame(flushModelStream);
  $("#glm-stream-status").textContent = "GLM 输出中…";
}
function renderDetails(data) {
  const s = data || {};
  const w = s.window || {};
  const quality = s.data_quality || {};
  const list = function (title, values, empty) {
    const items = (values || []).map(function (value) {
      const text = typeof value === "string" ? value :
        (value && typeof value === "object" ? (value.description || value.text || "") : String(value || ""));
      return "<li>" + esc(text) + "</li>";
    }).join("");
    return "<article class='detail-card'><h3>" + title + "</h3>" +
      (items ? "<ul>" + items + "</ul>" : "<p>" + esc(empty || "暂无") + "</p>") + "</article>";
  };
  return "<article class='detail-card'><h3>时间窗口</h3><p>" + esc(w.start || "unknown") + " → " +
    esc(w.end || "unknown") + " · " + esc(w.event_count || 0) + " 条事件</p></article>" +
    list("观察事实", s.observations, "没有可核对的观察事实") +
    list("状态变化", s.transitions, "没有单独记录的状态变化") +
    list("缺失证据", s.missing_evidence, "模型没有列出缺失证据") +
    list("不确定性", s.uncertainties, "暂无额外不确定性") +
    "<article class='detail-card'><h3>数据质量</h3><p>未知事件 " + esc(quality.unknown_event_count || 0) +
    " 条 · 初始全状态 " + (quality.has_initial_full_state ? "有" : "无") +
    " · 末端全状态 " + (quality.has_terminal_full_state ? "有" : "无") + "</p></article>";
}
function fallbackState(data) {
  const s = data || {}, w = s.window || {};
  const facts = (s.observations || []).map(function (item) {
    return typeof item === "string" ? item : (item.description || "");
  }).filter(Boolean);
  const changes = (s.transitions || []).map(function (item) {
    return typeof item === "string" ? item : (item.description || "");
  }).filter(Boolean);
  const missing = (s.missing_evidence || []).filter(Boolean);
  if (!facts.length && !changes.length && !missing.length) return "";
  const windowText = (w.start || "unknown") + " 至 " + (w.end || "unknown") +
    "，共 " + (w.event_count || 0) + " 条事件";
  return [
    "任务级 State：窗口 " + windowText + "。",
    facts.length ? "观察事实：" + facts.join("；") + "。" : "",
    changes.length ? "状态变化：" + changes.join("；") + "。" : "",
    missing.length ? "缺失证据：" + missing.join("；") + "。" : "",
    "该状态只描述日志证据，不等同于物理故障确认或操作指令。"
  ].filter(Boolean).join(" ");
}
function applyState(data) {
  const structured = data && data.structured_state;
  const text = (data && typeof data.state === "string" ? data.state.trim() : "") || fallbackState(structured);
  if (!text) throw new Error("State 服务返回了空内容，请检查日志格式后重试");
  currentState = text;
  currentStructured = structured || {};
  $("#state-text").value = text;
  $("#state-details").innerHTML = renderDetails(currentStructured);
  $("#state-model").textContent = data && data.model ? "生成模型 · " + data.model : "";
  if (data && (data.warnings || []).length) {
    $("#state-warning").textContent = data.warnings.join("；");
    $("#state-warning").classList.remove("hidden");
  }
}
function renderTasks() {
  const entries = Object.entries(catalog);
  $("#tasks").innerHTML = entries.map(function (entry, index) {
    const id = entry[0], task = entry[1];
    const inputType = selectionMode === "single" ? "radio" : "checkbox";
    const inputName = selectionMode === "single" ? " name='procedure-task'" : "";
    const checked = selectionMode === "single" ? ((selectedCaseTask && id === selectedCaseTask) || (!selectedCaseTask && index === 0) ? " checked" : "") : (index < 4 ? " checked" : "");
    return "<label class='task-item'><input type='" + inputType + "'" + inputName + " value='" + esc(id) + "'" +
      checked + "><span><strong>" + esc(task.title) + "</strong>" +
      "<span>" + esc(task.description) + "</span><span class='task-type'>choice · " +
      esc(Object.keys(task.criteria || {}).length) + " 个候选选项</span></span></label>";
  }).join("");
}
function questionsFromSelection() {
  const questions = {};
  document.querySelectorAll("#tasks input:checked").forEach(function (input) {
    const task = catalog[input.value];
    // The explicit procedure adapter was trained with one question key:
    // `decision`. The UI family selector therefore becomes the question
    // content while preserving that key.
    const key = selectionMode === "single" ? "decision" : input.value;
    questions[key] = {type: "choice", instructions: task.instructions, criteria: task.criteria};
  });
  return questions;
}
function renderAnswer(id, answer, question) {
  const probs = Object.entries(answer.probabilities || {}).sort(function (a, b) { return b[1] - a[1]; });
  const choice = answer.choice || answer.answer || answer.label || answer.score;
  const label = question && question.criteria && question.criteria[choice] ? question.criteria[choice] : choice;
  const confidence = Number(answer.confidence || (probs[0] && probs[0][1]) || 0);
  return "<article class='result-card'><div class='result-top'><span>" + esc(id) +
    " · " + esc(answer.type || "choice") + "</span><span class='confidence'>" +
    Math.round(confidence * 100) + "%</span></div><h3>" + esc(label || "未返回选项") +
    "</h3><div class='result-choice'>" + esc(choice || "unknown") + "</div><div class='result-bar'><i style='width:" +
    Math.round(confidence * 100) + "%'></i></div><div class='probs'>" +
    probs.map(function (item) {
      const text = question && question.criteria && question.criteria[item[0]] ? question.criteria[item[0]] : item[0];
      return "<div class='prob'><span>" + esc(text) + "</span><b>" + (Number(item[1]) * 100).toFixed(1) + "%</b></div>";
    }).join("") + "</div></article>";
}
function parseSSE(onEvent) {
  return async function (response) {
    if (!response.body) throw new Error("浏览器不支持 State 流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const consume = function (block) {
      if (!block.trim()) return;
      let event = "message";
      const data = [];
      block.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf("event:") === 0) event = line.slice(6).trim();
        if (line.indexOf("data:") === 0) data.push(line.slice(5).trimStart());
      });
      if (!data.length) return;
      let parsed;
      try { parsed = JSON.parse(data.join("\n")); }
      catch (error) { throw new Error("State 流返回了无法解析的事件"); }
      onEvent(event, parsed);
    };
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        buffer += decoder.decode();
        consume(buffer);
        break;
      }
      buffer += decoder.decode(chunk.value, {stream: true});
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop();
      blocks.forEach(consume);
    }
  };
}
async function buildState() {
  const logs = $("#logs").value.trim();
  if (!logs) { showNotice("请先输入原始日志"); return; }
  const requestId = ++stateRequest;
  $("#build").disabled = true;
  $("#state-section").classList.remove("hidden");
  $("#decision-section").classList.add("hidden");
  $("#state-warning").classList.add("hidden");
  setFlow("state");
  resetModelStream();
  stage("准备读取原始日志…", true);
  try {
    const response = await fetch("/v1/state/stream", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({logs: logs, task: $("#task").value.trim(), station: $("#station").value.trim() || "姑苏"})
    });
    if (!response.ok) throw new Error("State 服务请求失败");
    let receivedDone = false;
    let receivedState = false;
    let streamError = "";
    try {
      await parseSSE(function (event, data) {
        if (requestId !== stateRequest) return;
        if (event === "stage") stage(data.message || "处理中…", true);
        if (event === "model_delta") appendModelStream(data);
        if (event === "model_done") $("#glm-stream-status").textContent = "GLM 输出完成，正在解析 State…";
        if (event === "state") {
          applyState(data);
          receivedState = true;
          $("#glm-stream-status").textContent = "已接收任务级 State，正在补充详情…";
        }
        if (event === "state_details") {
          applyState({state: currentState, structured_state: data.structured_state,
            warnings: data.warnings || [], model: data.model});
          receivedState = true;
          $("#glm-stream-status").textContent = "已解析为任务级 State";
        }
        if (event === "done") {
          receivedDone = true;
          applyState(data);
          receivedState = true;
          $("#glm-stream-status").textContent = "已解析为任务级 State";
          stage("State 已生成，请核对后继续", false);
        }
        // The stream may end with an upstream GLM parsing/connection error.
        // Keep the diagnostic text, then let the synchronous fallback below
        // make one complete request before reporting failure to the user.
        if (event === "error") streamError = data.message || "State 流式构建失败";
      });
    } catch (error) {
      streamError = error.message || "State 流式构建失败";
    }
    flushModelStream();
    if (!receivedState) {
      // Some browser/proxy combinations buffer or cut a long SSE response.
      // Retry through the ordinary JSON endpoint so a valid State is still
      // usable; the stream panel keeps the diagnostic status visible.
      $("#glm-stream-status").textContent = "正在通过同步接口补全 State…";
      stage("正在通过同步接口补全 State…", true);
      let fallback;
      try {
        fallback = await api("/v1/state", {
          method: "POST",
          body: JSON.stringify({logs: logs, task: $("#task").value.trim(), station: $("#station").value.trim() || "姑苏"})
        });
      } catch (error) {
        throw new Error(error.message || streamError || "State 服务未返回可用内容，请检查日志格式后重试");
      }
      applyState(fallback);
      receivedState = true;
      $("#glm-stream-status").textContent = "已通过同步接口解析为任务级 State";
    }
    if (!receivedDone) stage("State 已生成，请核对后继续", false);
  } catch (error) {
    stage("State 构建失败", false);
    showNotice(error.message);
  } finally {
    $("#build").disabled = false;
  }
}
function confirmState() {
  const state = $("#state-text").value.trim();
  if (!state) { showNotice("State 不能为空"); return; }
  currentState = state;
  $("#decision-section").classList.remove("hidden");
  $("#decision-state-ref").textContent = state.length.toLocaleString() + " 字符";
  setFlow("decision");
  $("#decision-section").scrollIntoView({behavior: "smooth", block: "start"});
}
async function runDecision() {
  const state = $("#state-text").value.trim();
  const questions = questionsFromSelection();
  if (!state) { showNotice("请先生成并确认 State"); return; }
  if (!Object.keys(questions).length) { showNotice("至少选择一个决策问题"); return; }
  $("#run-decision").disabled = true;
  $("#decision-progress").classList.remove("hidden");
  $("#results").classList.add("hidden");
  $("#decision-note").classList.add("hidden");
  showNotice("");
  try {
    const data = await api("/v1/decision", {method: "POST", body: JSON.stringify({state: state, questions: questions})});
    $("#results").innerHTML = Object.entries(data.answers || {}).map(function (entry) {
      return renderAnswer(entry[0], entry[1], questions[entry[0]]);
    }).join("");
    $("#results").classList.remove("hidden");
    const usage = data.usage || {}, limits = data.limits || {};
    $("#decision-meta").textContent = (usage.input_tokens || 0).toLocaleString() + " input tokens · " +
      (limits.over_training_length ? "超过训练长度，结果仅作实验观察" : "在训练长度内") + " · " + (data.backend || "");
    $("#decision-note").classList.remove("hidden");
  } catch (error) {
    showNotice(error.message);
  } finally {
    $("#run-decision").disabled = false;
    $("#decision-progress").classList.add("hidden");
  }
}
async function load() {
  try {
    const responses = await Promise.all([api("/health"), api("/api/tasks"), api("/static/samples.json")]);
    const health = responses[0], tasks = responses[1];
    catalog = tasks.tasks || {};
    selectionMode = tasks.selection_mode || "multi";
    caseSamples = responses[2] || [];
    renderCaseSamples();
    renderTasks();
    syncTaskSelection();
    $("#health").textContent = health.ready ? "Decision Model 已就绪 · " + (health.gpu || "GPU") : "Decision Model 未就绪";
    $("#health").classList.toggle("ready", !!health.ready);
    if (!health.ready && health.error) showNotice("Decision Model 当前未就绪：" + health.error);
  } catch (error) {
    $("#health").textContent = "服务连接失败";
    showNotice(error.message);
  }
}
$("#logs").addEventListener("input", function () {
  $("#log-count").textContent = $("#logs").value.length.toLocaleString() + " 字符";
});
$("#sample").addEventListener("click", function () {
  selectedCaseTask = "";
  $("#case-sample").value = "";
  $("#case-sample-hint").textContent = "已载入内置阀冷快速示例；也可以从上方选择规程案例日志。";
  $("#logs").value = JSON.stringify(SAMPLE, null, 2);
  $("#logs").dispatchEvent(new Event("input"));
});
$("#case-sample").addEventListener("change", function () {
  if (this.value) loadCaseSample(this.value);
});
$("#clear").addEventListener("click", function () {
  selectedCaseTask = "";
  $("#case-sample").value = "";
  $("#case-sample-hint").textContent = "选择后会自动填入日志、任务描述，并在决策阶段选中对应规程条款。";
  $("#logs").value = "";
  $("#logs").dispatchEvent(new Event("input"));
  $("#state-section").classList.add("hidden");
  $("#decision-section").classList.add("hidden");
  setFlow("input");
  showNotice("");
});
$("#build").addEventListener("click", buildState);
$("#regenerate").addEventListener("click", buildState);
$("#confirm").addEventListener("click", confirmState);
$("#run-decision").addEventListener("click", runDecision);
load();
