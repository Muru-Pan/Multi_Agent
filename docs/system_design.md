# System Design

## Overview

This backend is an agentic AI system designed to handle multi-step user tasks by splitting work across specialized agents and coordinating them through an async, message-driven pipeline.

## Core Components

- `FastAPI API Layer`
  - accepts tasks
  - exposes status endpoint
  - streams live progress with SSE
- `TaskOrchestrator`
  - owns task lifecycle
  - validates plans
  - batches DAG levels manually
  - dispatches steps to workers
  - retries failures
- `PlannerAgent`
  - turns a user task into a dependency-aware step plan
- `RetrieverAgent`
  - performs web search and scrapes lightweight context
- `WriterAgent`
  - synthesizes retrieved context into the final response
- `Redis Streams`
  - queue between orchestrator and workers
  - separate task streams per agent
  - shared result stream for completed step outputs
  - per-task event stream for SSE
- `LLMRouter`
  - tries Groq first
  - falls back to Gemini
  - uses Together only as the last external fallback

## Request Flow

1. Client sends a task to `POST /task`.
2. API creates a `task_id` and starts background execution.
3. Orchestrator calls the planner.
4. Planner returns a step DAG.
5. Orchestrator groups steps by dependency level.
6. Independent steps in the same level are dispatched together.
7. Agent workers consume their Redis stream and process the step.
8. Workers publish results to the shared result stream.
9. Orchestrator collects results and unlocks dependent steps.
10. Writer publishes token events while generating the final answer.
11. API streams progress and tokens through SSE.

## Agent Boundaries

### Planner
- responsibility: break a complex task into executable steps
- input: raw user task
- output: validated JSON plan

### Retriever
- responsibility: gather external evidence
- input: search-style step input
- output: summaries, cleaned documents, source URLs

### Writer
- responsibility: final synthesis and answer generation
- input: original task plus all previous step outputs
- output: streamed final answer

## Async Orchestration

The orchestrator does not execute steps sequentially by default. It computes dependency levels from the DAG and runs all ready steps in a batch with `asyncio.gather()`. This satisfies the manual batching requirement without using a black-box agent framework.

## Queue Design

### Streams
- `task_stream:retriever`
- `task_stream:writer`
- `result_stream`
- `event_stream:{task_id}`

### Why Redis Streams
- simple local setup
- consumer groups for horizontal worker scaling
- pending entry tracking for retry recovery
- low operational overhead for an assignment-scale system

## Failure Handling

- planner failure: fallback to a default 2-step plan
- provider failure: retry with exponential backoff, then fall back to the next provider
- step failure: retry before marking failed
- critical step failure: fail the task
- non-critical step failure: continue with partial context
- stale queue messages: reclaimed from pending entries

## Streaming Design

SSE events include:
- `plan_ready`
- `step_started`
- `step_done`
- `stream_token`
- `step_failed`
- `task_complete`
- `task_failed`

The writer never writes to HTTP directly. It emits token events into Redis, and the API layer streams those events to the client.

## Scalability Notes

- worker count can scale independently by agent type
- Redis is the main bottleneck at higher throughput
- event and task state are stored with TTL to avoid unbounded growth

## Why No Separate Analyzer Agent

The assignment suggests agents such as Retriever, Analyzer, and Writer. In this MVP, analysis is logically merged into the Writer to keep orchestration simple while preserving clear planning, retrieval, and synthesis boundaries. If task complexity increases, Analyzer can be split out later without changing the queue-first architecture.
