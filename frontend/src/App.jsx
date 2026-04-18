import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";
const sampleTask =
  "Compare the top open-source LLMs for coding and recommend one for a small backend team.";

const emptyRun = {
  taskId: "",
  statusUrl: "",
  streamUrl: "",
  lifecycle: "idle",
  steps: [],
  finalResult: "",
  error: "",
};

function formatEventLabel(name) {
  return name.replaceAll("_", " ");
}

function summarizeEvent(type, payload) {
  switch (type) {
    case "plan_ready":
      return {
        title: "Plan ready",
        detail: `${payload.steps?.length ?? 0} step${payload.steps?.length === 1 ? "" : "s"} prepared for execution.`,
      };
    case "step_started":
      return {
        title: `${payload.agent} started`,
        detail: `The ${payload.step_id} task is now running.`,
      };
    case "step_done":
      return {
        title: `${payload.agent} finished`,
        detail: payload.result_preview || `${payload.step_id} completed successfully.`,
      };
    case "task_complete":
      return {
        title: "Task complete",
        detail: "The workflow finished and the final answer is ready.",
      };
    case "task_failed":
      return {
        title: "Task failed",
        detail: payload.reason || "The workflow stopped before completion.",
      };
    default:
      return {
        title: formatEventLabel(type),
        detail: "Progress update received.",
      };
  }
}

function normalizeAnswerText(text) {
  return text
    .replace(/\r/g, "")
    .replace(/^\s*#{2,}\s*$/gm, "")
    .replace(/^\s*\*{2,}\s*$/gm, "")
    .replace(/^\s*[-*]\s*\*{2,}\s*$/gm, "")
    .replace(/###\s*/g, "\n## ")
    .replace(/##\s*/g, "\n## ")
    .replace(/\bOverview\b\s*:?\s*/gi, "\n## Overview\n")
    .replace(/\bComparison\b\s*:?\s*/gi, "\n## Comparison\n")
    .replace(/\bRecommendation\b\s*:?\s*/gi, "\n## Recommendation\n")
    .replace(/\bWhy\b\s*:?\s*/gi, "\n- **Why**: ")
    .replace(/\bLimitation\b\s*:?\s*/gi, "\n- **Limitation**: ")
    .replace(/\s+\*\s\*\*/g, "\n- **")
    .replace(/\s+\*\*/g, " **")
    .replace(/\*\*(.+?)\*\*:/g, "**$1**:")
    .replace(/\s-\s\*\*/g, "\n- **")
    .replace(/(?<!\n)-\s\*\*/g, "\n- **")
    .replace(/\s+#{2,}\s*$/gm, "")
    .replace(/\s+\*{2,}\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function extractSection(text, heading, stopHeadings) {
  const escapedHeading = heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const escapedStops = stopHeadings.map((item) => item.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const stopPattern = escapedStops.length ? `(?=\\b(?:${escapedStops.join("|")})\\b\\s*:?)` : "$";
  const regex = new RegExp(`(?:^|\\n)\\s*(?:##\\s*)?${escapedHeading}\\s*:?[\\s\\n]*([\\s\\S]*?)${stopPattern}`, "i");
  const match = text.match(regex);
  return match ? match[1].trim() : "";
}

function cleanSectionText(text) {
  return text
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/\*\*/g, "")
    .replace(/^\s*-\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function splitComparisonItems(text) {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const items = [];
  let current = null;

  for (const line of lines) {
    const normalized = line.replace(/^[*-]\s*/, "");
    const match = normalized.match(/^([A-Za-z0-9 ./+_-]{2,40}):\s*(.*)$/);

    if (match) {
      if (current) items.push(current);
      current = { title: match[1].trim(), body: match[2].trim() };
      continue;
    }

    if (current) {
      current.body = `${current.body} ${normalized}`.trim();
    } else {
      items.push({ title: "Key point", body: normalized });
    }
  }

  if (current) items.push(current);

  return items.filter((item) => item.body);
}

function parseRecommendation(text) {
  const cleaned = cleanSectionText(text);
  const lines = cleaned
    .split("\n")
    .map((line) => line.replace(/^[*-]\s*/, "").trim())
    .filter(Boolean);

  let bestChoice = "";
  let why = "";
  const extras = [];

  for (const line of lines) {
    if (/^best choice\s*:/i.test(line)) {
      bestChoice = line.replace(/^best choice\s*:/i, "").trim();
      continue;
    }
    if (/^why\s*:/i.test(line)) {
      why = line.replace(/^why\s*:/i, "").trim();
      continue;
    }
    extras.push(line);
  }

  if (!bestChoice && extras.length) {
    bestChoice = extras.shift();
  }
  if (!why && extras.length) {
    why = extras.join(" ");
  }

  return { bestChoice, why };
}

function parseStructuredAnswer(text) {
  const normalized = normalizeAnswerText(text);
  const overview = cleanSectionText(
    extractSection(normalized, "Overview", ["Comparison", "Recommendation", "Limitation"]),
  );
  const comparisonRaw = cleanSectionText(
    extractSection(normalized, "Comparison", ["Recommendation", "Limitation"]),
  );
  const recommendationRaw = cleanSectionText(
    extractSection(normalized, "Recommendation", ["Limitation"]),
  );
  const limitation = cleanSectionText(extractSection(normalized, "Limitation", []));

  return {
    overview,
    comparisonItems: splitComparisonItems(comparisonRaw),
    recommendation: parseRecommendation(recommendationRaw),
    limitation,
    raw: normalized,
  };
}

function renderInlineMarkdown(text) {
  const parts = text.split(/(\*\*.*?\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    const match = part.match(/^\*\*(.*)\*\*$/);
    if (match) {
      return <strong key={index}>{match[1]}</strong>;
    }
    return <span key={index}>{part}</span>;
  });
}

function renderStructuredAnswer(text) {
  const parsed = parseStructuredAnswer(text);

  const hasStructuredContent =
    parsed.overview || parsed.comparisonItems.length || parsed.recommendation.bestChoice || parsed.recommendation.why;

  if (!hasStructuredContent) {
    return <p>{renderInlineMarkdown(parsed.raw)}</p>;
  }

  return (
    <div className="answer-layout">
      {parsed.overview ? (
        <section className="answer-section">
          <h3>Overview</h3>
          <p>{renderInlineMarkdown(parsed.overview)}</p>
        </section>
      ) : null}

      {parsed.comparisonItems.length ? (
        <section className="answer-section">
          <h3>Comparison</h3>
          <div className="comparison-grid">
            {parsed.comparisonItems.map((item, index) => (
              <article className="comparison-card" key={`${item.title}-${index}`}>
                <h4>{item.title}</h4>
                <p>{renderInlineMarkdown(item.body)}</p>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {parsed.recommendation.bestChoice || parsed.recommendation.why ? (
        <section className="answer-section recommendation-box">
          <h3>Recommendation</h3>
          {parsed.recommendation.bestChoice ? (
            <p>
              <strong>Best choice:</strong> {renderInlineMarkdown(parsed.recommendation.bestChoice)}
            </p>
          ) : null}
          {parsed.recommendation.why ? (
            <p>
              <strong>Why:</strong> {renderInlineMarkdown(parsed.recommendation.why)}
            </p>
          ) : null}
        </section>
      ) : null}

      {parsed.limitation ? (
        <section className="answer-section limitation-box">
          <h3>Limitation</h3>
          <p>{renderInlineMarkdown(parsed.limitation)}</p>
        </section>
      ) : null}
    </div>
  );
}

function App() {
  const [task, setTask] = useState(sampleTask);
  const [run, setRun] = useState(emptyRun);
  const [events, setEvents] = useState([]);
  const [tokens, setTokens] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [connection, setConnection] = useState("checking");
  const eventSourceRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    async function checkHealth() {
      try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();
        if (!cancelled) {
          setConnection(data.redis === "connected" ? "connected" : "degraded");
        }
      } catch {
        if (!cancelled) {
          setConnection("offline");
        }
      }
    }

    checkHealth();
    const id = window.setInterval(checkHealth, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  async function submitTask(event) {
    event.preventDefault();
    if (!task.trim()) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    setSubmitting(true);
    setEvents([]);
    setTokens("");
    setRun(emptyRun);

    try {
      const response = await fetch(`${API_BASE}/task`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
      });

      if (!response.ok) {
        throw new Error("Failed to create task");
      }

      const data = await response.json();
      setRun({
        taskId: data.task_id,
        statusUrl: data.status_url,
        streamUrl: data.stream_url,
        lifecycle: data.status.toLowerCase(),
        steps: [],
        finalResult: "",
        error: "",
      });

      openStream(data.stream_url);
    } catch (error) {
      setRun((current) => ({
        ...current,
        lifecycle: "failed",
        error: error.message || "Unable to submit task",
      }));
    } finally {
      setSubmitting(false);
    }
  }

  function openStream(streamUrl) {
    const source = new EventSource(`${API_BASE}${streamUrl}`);
    eventSourceRef.current = source;

    const pushEvent = (type, payload) => {
      const summary = summarizeEvent(type, payload);
      setEvents((current) => [
        ...current,
        { id: `${type}-${current.length}-${Date.now()}`, type, payload, summary },
      ]);
    };

    source.addEventListener("plan_ready", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({
        ...current,
        lifecycle: "planning",
        steps: payload.steps.map((step) => ({
          id: step.id,
          agent: step.agent,
          status: "pending",
          resultPreview: "",
        })),
      }));
      pushEvent("plan_ready", payload);
    });

    source.addEventListener("step_started", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({
        ...current,
        lifecycle: "executing",
        steps: current.steps.map((step) =>
          step.id === payload.step_id ? { ...step, status: "in_progress" } : step,
        ),
      }));
      pushEvent("step_started", payload);
    });

    source.addEventListener("step_done", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({
        ...current,
        lifecycle: "executing",
        steps: current.steps.map((step) =>
          step.id === payload.step_id
            ? { ...step, status: "done", resultPreview: payload.result_preview }
            : step,
        ),
      }));
      pushEvent("step_done", payload);
    });

    source.addEventListener("stream_token", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({ ...current, lifecycle: "streaming" }));
      setTokens((current) => current + payload.token);
    });

    source.addEventListener("task_complete", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({
        ...current,
        lifecycle: "complete",
        finalResult: payload.full_result,
      }));
      pushEvent("task_complete", payload);
      source.close();
    });

    source.addEventListener("task_failed", (event) => {
      const payload = JSON.parse(event.data);
      setRun((current) => ({
        ...current,
        lifecycle: "failed",
        error: payload.reason || "Task failed",
      }));
      pushEvent("task_failed", payload);
      source.close();
    });

    source.onerror = () => {
      setRun((current) => {
        if (current.lifecycle === "complete") return current;
        return { ...current, lifecycle: current.lifecycle === "failed" ? "failed" : "disconnected" };
      });
      source.close();
    };
  }

  const statusTone = useMemo(() => {
    if (connection === "connected") return "good";
    if (connection === "degraded") return "warn";
    return "bad";
  }, [connection]);

  const finalAnswer = run.finalResult || tokens;

  return (
    <div className="page-shell">
      <div className="page-backdrop" />
      <main className="app-shell">
        <section className="hero-card">
          <div className="eyebrow-row">
            <span className="eyebrow">Agentic AI Console</span>
            <span className={`status-pill ${statusTone}`}>Backend {connection}</span>
          </div>
          <h1>Run a multi-agent workflow and watch it think in public.</h1>
          <p>
            This frontend talks to your FastAPI backend, submits a task, listens to SSE events,
            and turns the execution trace into a calm, demo-ready experience.
          </p>
        </section>

        <section className="workspace-grid">
          <form className="compose-card" onSubmit={submitTask}>
            <div className="card-heading">
              <h2>Start a Task</h2>
              <span>Planner → Retriever → Writer</span>
            </div>
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              placeholder="Describe the multi-step task you want the system to solve."
            />
            <div className="button-row">
              <button type="submit" disabled={submitting}>
                {submitting ? "Launching..." : "Launch Workflow"}
              </button>
              <button type="button" className="ghost-button" onClick={() => setTask(sampleTask)}>
                Use Sample
              </button>
            </div>
          </form>

          <section className="status-card">
            <div className="card-heading">
              <h2>Run Status</h2>
              <span>{run.taskId ? "Live session" : "No task yet"}</span>
            </div>
            <div className="stat-grid">
              <div>
                <label>Lifecycle</label>
                <strong>{run.lifecycle}</strong>
              </div>
              <div>
                <label>Events</label>
                <strong>{events.length}</strong>
              </div>
              <div>
                <label>Steps</label>
                <strong>{run.steps.length}</strong>
              </div>
              <div>
                <label>Answer</label>
                <strong>{finalAnswer ? "ready" : "waiting"}</strong>
              </div>
            </div>
            {run.error ? <p className="error-text">{run.error}</p> : null}
          </section>
        </section>

        <section className="panel-grid">
          <section className="panel-card">
            <div className="card-heading">
              <h2>Execution Timeline</h2>
              <span>Live task events</span>
            </div>
            <div className="timeline">
              {events.length === 0 ? (
                <div className="empty-state">Events will appear here once the workflow starts.</div>
              ) : (
                events.map((item) => (
                  <article className="timeline-item" key={item.id}>
                    <div className="timeline-dot" />
                    <div>
                      <h3>{item.summary.title}</h3>
                      <p>{item.summary.detail}</p>
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>

          <section className="panel-card">
            <div className="card-heading">
              <h2>Step Flow</h2>
              <span>Agent progress</span>
            </div>
            <div className="step-list">
              {run.steps.length === 0 ? (
                <div className="empty-state">No planned steps yet.</div>
              ) : (
                run.steps.map((step) => (
                  <article className="step-card" key={step.id}>
                    <div className="step-meta">
                      <span>{step.id.replace("_", " ")}</span>
                      <span className={`step-badge ${step.status}`}>{step.status}</span>
                    </div>
                    <h3>{step.agent}</h3>
                    <p>{step.resultPreview ? step.resultPreview.slice(0, 120) : "Waiting for output..."}</p>
                  </article>
                ))
              )}
            </div>
          </section>
        </section>

        <section className="result-card">
          <div className="card-heading">
            <h2>Streamed Answer</h2>
            <span>{tokens.length ? `${tokens.length} chars` : "No output yet"}</span>
          </div>
          <div className="result-surface">
            {finalAnswer ? (
              <div className="rich-answer">{renderStructuredAnswer(finalAnswer)}</div>
            ) : (
              <div className="empty-state">The writer output will stream here token by token.</div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
