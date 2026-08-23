# AI Strategy

## Goal
Reduce Claude Code token usage by offloading low-risk, high-token tasks to local LLMs.

## The rule that decides everything
**Is anyone waiting on the answer?**

That question matters more than the task type. A CPU-only 3B at ~16 tok/s is
unusable when you're watching it stream and perfectly fine when the result shows
up on its own. The local model was underused not because it lacked suitable
work, but because Open WebUI is a *pull* interface — you have to go there, type,
and wait, which loses to Claude every time.

So: **synchronous → Claude. Asynchronous → local.**

The `assistant` stack ([`docker/assistant/`](../docker/assistant/),
[TSD](design/tsd-local-llm-discord-jobs.md)) exists to make the asynchronous half
real — it runs local jobs and pushes results to Discord, so latency stops being
felt. Open WebUI remains for the times you genuinely want interactive chat.

## Local LLM responsibilities
Best delivered as background jobs, not chat:
- scheduled digests (daily homelab health — shipped)
- log summaries
- repo Q&A
- README/docs generation
- simple scripts
- boilerplate
- test scaffolding
- first-pass refactors

The load-bearing constraint: **only one generation at a time.** The tuned model
pins `num_thread 4` of the LXC's 6-core quota, so two concurrent generations
contend for the same cores and memory bandwidth — both crawl and the other
stacks starve. Anything driving Ollama must serialize its requests.

## Claude responsibilities
- architecture
- hard debugging
- multi-file changes
- security-sensitive changes
- agent planning
- final review

## Personal / general use (non-coding)
Same split applies beyond code.

**Local `llama3.2:3b` — cheap, private, offline, simple:**
- quick facts from training knowledge
- summarizing / rewriting text you paste in
- drafting boilerplate (emails, messages, configs, commit messages)
- private notes you don't want to send to a cloud
- low-stakes brainstorming

Reach these via Discord (`/ask`, `/summarize`, or right-click a message →
*Summarize message*) rather than `chat.home` — fire it off, get on with
something else, read the reply when it lands. That also works off-tailnet,
which `chat.home` cannot.

**Claude — reasoning, current data, accuracy that matters:**
- trip / weather / climbing & bouldering planning (needs live forecasts + judgment)
- anything needing up-to-date web data
- multi-step reasoning or planning
- anything where being wrong has a real cost

## Decision log
- **2026-08-22 — the bottleneck was the interface, not the model.** Two prior
  attempts to get more out of local inference tried bigger models and retrieval;
  both failed on hardware limits. The unexamined assumption in both was that
  someone is *waiting* for the output. Removing it — pushing results to Discord
  instead of pulling them from a chat page — makes the existing 3B genuinely
  useful with no hardware change. Shipped as
  [`docker/assistant/`](../docker/assistant/); see
  [`tsd-local-llm-discord-jobs.md`](design/tsd-local-llm-discord-jobs.md). Also
  established: anything factual is computed in Python and merely *narrated* by
  the model, and a model failure degrades to facts-only rather than to silence.
- **2026-06-07 — no web search on local.** Tried a self-hosted SearXNG +
  `qwen2.5:7b` for web-augmented answers. Two problems on this hardware: the 7B
  was too slow and RAM-hungry (CPU-only, memory-bandwidth bound), and a 3B can't
  faithfully use retrieved sources regardless (it ignored fetched results and
  answered from its training prior). Removed both; consolidated to a single
  resident `llama3.2:3b`. Live-data / reasoning tasks go to Claude. See the
  shelved [`tsd-ai-homelab-assistant.md`](design/tsd-ai-homelab-assistant.md).

## Current constraints
- CPU-only
- 16GB RAM
- no discrete GPU
- local models are slower and less reliable than Claude
- local inference is memory-bandwidth bound — a 7B is impractically slow here;
  3B is the comfortable ceiling
- one concurrent generation; more is slower in total, not faster
- a 3B cannot be trusted with tool calling or with staying faithful to retrieved
  sources — give it finished facts and ask only for phrasing

## Upgrade path
- evaluate RAM upgrade
- consider GPU-capable machine later (would make 7B+ and web-augmented use viable)
- use Claude Max only if local offloading still does not reduce usage enough