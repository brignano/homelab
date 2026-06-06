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

## Current constraints
- CPU-only
- 16GB RAM
- no discrete GPU
- local models will be slower and less reliable than Claude

## Upgrade path
- evaluate RAM upgrade
- consider GPU-capable machine later
- use Claude Max only if local offloading still does not reduce usage enough