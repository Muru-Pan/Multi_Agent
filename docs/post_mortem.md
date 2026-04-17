# Post-Mortem

## One Scaling Issue Encountered

The main scaling concern is Redis becoming a bottleneck because it handles task dispatch, result collection, and event streaming in one place. Under heavier concurrency, a single Redis instance can become the limiting factor before worker CPU is exhausted.

### Mitigation

- split task streams by agent type
- keep event data lightweight
- expire task metadata with TTL
- future improvement: partition task streams further by shard key

## One Design Decision I Would Change

I would separate background worker processes from the API process earlier in the project. Right now the workers are started by the same backend service for simplicity. That is good for development, but a cleaner production-style design would run:

- one API service
- one or more retriever worker processes
- one or more writer worker processes

This would make scaling, deployment, and failure isolation better.

## Trade-offs Made During Development

### Redis Streams vs RabbitMQ or Kafka

I chose Redis Streams because it is much easier to run locally and still supports consumer groups, pending messages, and retries. The trade-off is that Kafka or RabbitMQ would offer stronger scaling or messaging semantics in larger systems.

### SSE vs WebSockets

I chose SSE because the requirement only needs server-to-client streaming. SSE is lighter and simpler than WebSockets, but it is less flexible if two-way real-time communication is needed later.

### Retriever Web Search vs Full RAG

I used lightweight web search and scraping instead of a vector database. This avoids extra infrastructure and is enough for the assignment, but it is less reliable than a proper indexed retrieval system.

### Merged Analyzer Into Writer

I kept the system simpler by merging analysis into the Writer agent. This reduces moving parts, but a dedicated Analyzer agent would improve modularity for more complex tasks.
