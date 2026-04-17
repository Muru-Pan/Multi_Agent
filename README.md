# Agentic AI Backend

Backend-only multi-agent system for multi-step tasks using FastAPI, Redis Streams, async workers, and SSE.

## What it does

- accepts a complex task over HTTP
- plans dependency-aware steps
- dispatches work to specialized agents through Redis Streams
- retries failed work and falls back across LLM providers
- streams partial progress and writer tokens back over SSE

## Architecture

- `PlannerAgent`: converts the user task into a validated DAG
- `RetrieverAgent`: searches and scrapes lightweight web context
- `WriterAgent`: synthesizes the final answer and streams tokens
- `TaskOrchestrator`: owns planning, batching, retries, and task state
- `RedisQueue`: wraps Redis Streams, task hashes, and event persistence

## API

### `POST /task`

```json
{
  "task": "Compare the top 3 open source LLMs available in 2025 and suggest which is best for coding tasks"
}
```

Returns:

```json
{
  "task_id": "uuid",
  "status": "RECEIVED",
  "stream_url": "/task/<task_id>/stream",
  "status_url": "/task/<task_id>/status"
}
```

### `GET /task/{task_id}/stream`

Server-Sent Events stream with:

- `plan_ready`
- `step_started`
- `step_done`
- `stream_token`
- `step_failed`
- `task_complete`
- `task_failed`

### `GET /task/{task_id}/status`

Returns task lifecycle state and step statuses.

### `GET /health`

Returns service health plus Redis connectivity.

## Run locally

1. Copy `.env.example` to `.env`
2. Add your provider keys to `.env`
3. Start Redis locally on port `6379`
4. Install dependencies:

```powershell
& 'C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe' -m pip install -r requirements.txt
```

5. Start the API:

```powershell
& 'C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn app.main:app --reload
```

6. Create a task:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/task -ContentType 'application/json' -Body '{"task":"Compare the top open-source LLMs for coding and recommend one."}'
```

7. Check task status:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/task/<task_id>/status
```

8. Watch the SSE stream:

```powershell
curl.exe -N http://127.0.0.1:8000/task/<task_id>/stream
```

You should see events such as `plan_ready`, `step_started`, `step_done`, `stream_token`, and `task_complete`.

## Quick Local Demo Flow

1. Start Memurai so Redis is available on `127.0.0.1:6379`
2. Start the API
3. Submit a task and copy the returned `task_id`
4. In a second terminal, open:

```powershell
curl.exe -N http://127.0.0.1:8000/task/<task_id>/stream
```

5. In a third terminal, poll status if needed:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/task/<task_id>/status
```

## Tests

```bash
pytest
```

## Notes

- If no provider API key is configured, planner and writer fall back to deterministic local behavior so the system stays runnable for demos and tests.
- SSE replay is scaffolded through Redis event streams. Full `Last-Event-ID` replay can be enabled later.
- Together AI is intentionally placed last in the fallback order to avoid spending limited quota unless Groq and Gemini cannot serve the request.
