# Dispatch Protocol

**Three commands. Your AI agents run themselves.**

```bash
dispatch discover    # What's running? Where? Who owns what?
dispatch plan        # Propose ownership. Resolve conflicts.
dispatch deploy      # Push configs. Compile runbook. Verify.
```

---

## The Problem

You have scripts on systemd timers, workflows in n8n, cron jobs in OpenClaw, and Claude Code on your laptop. Something breaks. Three systems were "handling" the same task. All three were broken. Nobody noticed for five days.

**There's no standard for operational delegation between AI agents.**

MCP handles tool access. A2A handles agent communication. Neither answers: *"I built a pipeline — how do I hand it to an AI agent and walk away?"*

## What Dispatch Protocol Does

| Command | What happens |
|---------|-------------|
| `dispatch discover` | SSH into your server. Find every systemd timer, n8n workflow, OpenClaw job, and script. Detect conflicts. Auto-generate schemas. |
| `dispatch plan` | Analyze the infrastructure. Assign one owner per task. Propose changes. Save to `plan.yaml` for review. |
| `dispatch compile` | Generate `ORCHESTRATOR.md` — the runbook your agent re-reads every session. Compiled from schemas, never hand-written, can't drift. |
| `dispatch deploy` | Push configs to OpenClaw, n8n, and systemd. Compile the runbook. Run verification. `--dry-run` to preview. |

## Quick Start

```bash
pip install dispatch-protocol

# Discover what's running on your server
dispatch discover --local -o ./infra

# Review and plan ownership
dispatch plan -o plan.yaml

# Preview what would change
dispatch deploy --plan plan.yaml --dry-run

# Deploy for real
dispatch deploy --plan plan.yaml \
  --project-name "My Project" \
  --project-path "/root/workspace/my-project"
```

## What Gets Generated

```
project/
├── agents.yaml          # Who can do what (auto-discovered)
├── tasks/               # One YAML contract per task
│   ├── enrich-curators.yaml
│   ├── send-outreach.yaml
│   └── check-replies.yaml
├── health.yaml          # Health checks + escalation config
├── plan.yaml            # Ownership plan (review before deploy)
└── ORCHESTRATOR.md      # Compiled runbook (never hand-edit)
```

### Task Contract Example

```yaml
task:
  id: enrich-curators
  name: Curator Contact Enrichment
  owner: openclaw
  priority: critical
  execute:
    workdir: /root/workspace/music-promo-engine
    pre_run: git pull
    command: python3 scripts/enrich.py --limit 200
    timeout: 600s
  schedule:
    type: interval
    every: 6h
  success:
    metric: "enriched_count > 0"
    report:
      channel: telegram
      template: "Enriched {enriched} curators."
  failure:
    threshold: 3
    escalate:
      to: claude_code
      context: "Pipeline failing. Diagnose and fix."
```

Not a prompt. A **contract** — exact command, timeout, success metric, escalation path.

## Core Principle

**One owner per task.** Every recurring task has exactly one automation system responsible for it. Not two. Not "both handle it." One.

Dispatch Protocol discovers when multiple platforms own the same task (systemd AND n8n AND OpenClaw all "checking replies") and resolves the conflict.

## Ownership Hierarchy

| Platform | Best for | Capabilities |
|----------|----------|-------------|
| **systemd** | High-frequency polling (< 5 min) | Reliable, no reporting, no decisions |
| **n8n** | Webhook-triggered flows | Visual workflows, integrations, conditional logic |
| **OpenClaw** | Tasks needing AI reasoning | Decision-making, reporting, variable logic |
| **Claude Code** | Escalation / diagnosis | Code fixes, architecture, one-off repairs |

## Platform Adapters

### OpenClaw
Reads/writes `jobs.json` directly. Creates isolated-session cron jobs with model selection and timeouts. Auto-backs up config before writes.

### n8n
REST API integration. Activate, deactivate, and verify workflows. Retrieve execution history for failure detection.

### systemd
Creates timer + service unit files. Manages via `systemctl` — daemon-reload, enable, disable, verify.

All adapters support local execution or SSH for remote servers.

## The Compiled Runbook

`ORCHESTRATOR.md` is **generated**, not written.

```bash
dispatch compile --output ORCHESTRATOR.md
```

It's compiled from `agents.yaml` + `tasks/*.yaml` + `health.yaml`. When you change a task, regenerate. The runbook can't drift because it's never hand-edited.

Your agent re-reads it every session. Zero-context agents (OpenClaw, cron-based) wake up fresh — this file IS their memory.

**Includes:**
- Registered agents with capabilities
- Every task with copy-paste executable commands
- Success/failure metrics and thresholds
- Escalation chain (retry → alert → AI diagnosis → human)
- Health checks
- Important rules (git pull first, one owner per task, etc.)

## Architecture

```
                    ┌──────────────────────────┐
                    │      dispatch CLI         │
                    │  discover | plan | deploy │
                    │  compile                  │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────┴─────────────┐
                    │     Dispatch Runtime      │
                    │  ┌─────────────────────┐  │
                    │  │  Planner            │  │
                    │  │  Compiler           │  │
                    │  │  Deployer           │  │
                    │  │  Conflict Detector  │  │
                    │  └─────────────────────┘  │
                    └──┬──────┬──────┬──────┬──┘
                       │      │      │      │
              ┌────────┴┐  ┌──┴──┐ ┌─┴────┐ ┌┴──────────┐
              │ OpenClaw│  │ n8n │ │system│ │Claude Code│
              │ Adapter │  │Adapt│ │Adapt │ │  Adapter  │
              └─────────┘  └─────┘ └──────┘ └───────────┘
```

## Development

```bash
git clone https://github.com/stringztechnologies/dispatch-protocol
cd dispatch-protocol
pip install -e ".[dev]"
pytest tests/ -v
```

**58 tests** covering schemas, parsers, conflict detection, all three adapters, planner, compiler, and deployer.

## Roadmap

- [x] Phase 1: YAML schemas + `dispatch discover`
- [x] Phase 2: `dispatch plan` + `dispatch compile`
- [x] Phase 3: Platform adapters + `dispatch deploy`
- [ ] Phase 4: Health check runner + escalation engine
- [ ] Phase 5: Self-healing loop (fail → detect → diagnose → fix → redeploy)

## Origin

Built from a real production failure. March 1, 2026 — a music promotion pipeline silently died for five days. Four of six automated workflows failed. Zero alerts. Three systems duplicating the same task. All three broken.

Diagnosis took four hours. It should have taken three commands.

**See [VISION.md](VISION.md) for the full story and design document.**

---

*Built by [Stringz Technologies](https://github.com/stringztechnologies)*
