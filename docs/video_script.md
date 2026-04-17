# 3-5 Minute Video Script

## 1. Intro

"This project is a backend-only agentic AI system for multi-step tasks. The goal is to accept a complex user request, break it into steps, assign those steps to specialized agents, coordinate them asynchronously with a message queue, and stream partial progress back to the user."

## 2. Architecture

"The main components are FastAPI, an orchestrator, Redis Streams, and three logical agents: Planner, Retriever, and Writer. The planner converts the user task into a DAG of steps. The orchestrator batches independent steps manually using dependency levels. Redis Streams act as the queue between the orchestrator and the workers."

## 3. Agent Flow

"The retriever agent gathers external context using web search and HTML parsing. The writer agent synthesizes the final answer from prior step outputs. While the writer is generating, it publishes token events, and the API streams those events to the client through SSE."

## 4. Reliability

"For reliability, the system retries failed LLM calls with exponential backoff and provider fallback. It also uses Redis consumer groups and pending message recovery so unacknowledged work can be reclaimed. Critical step failures fail the task, while non-critical failures allow partial completion."

## 5. Why This Design

"I avoided black-box agent frameworks and implemented manual orchestration so the agent boundaries and batching logic are visible and easy to explain. Redis Streams was chosen because it is simple to run locally and still supports async worker coordination."

## 6. Demo Idea

"For the demo, I would submit a complex task through the API, show the returned task ID, connect to the SSE stream, and show events like plan_ready, step_started, step_done, stream_token, and task_complete."

## 7. Close

"If I continued this project, I would separate workers into standalone processes, improve SSE replay, and add deeper observability and structured retrieval."
