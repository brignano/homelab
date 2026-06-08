# AI Strategy

## Goal
Reduce Claude Code token usage by offloading low-risk, high-token tasks to local LLMs.

## Local LLM responsibilities
- repo Q&A
- log summaries
- README/docs generation
- simple scripts
- boilerplate
- test scaffolding
- first-pass refactors

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

**Claude — reasoning, current data, accuracy that matters:**
- trip / weather / climbing & bouldering planning (needs live forecasts + judgment)
- anything needing up-to-date web data
- multi-step reasoning or planning
- anything where being wrong has a real cost

## Decision log
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

## Upgrade path
- evaluate RAM upgrade
- consider GPU-capable machine later (would make 7B+ and web-augmented use viable)
- use Claude Max only if local offloading still does not reduce usage enough