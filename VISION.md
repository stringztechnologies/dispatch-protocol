# Dispatch Protocol

**Three commands. Your AI agents run themselves.**

```bash
dispatch discover --ssh root@my-vps
dispatch plan
dispatch deploy
```

---

## What Happened at 3am

March 1, 2026. A music promotion engine had been silently dying for five days.

An n8n variable feature stopped working. Nobody noticed. The enrichment pipeline that finds curator contact info — dead. The reply checker that monitors incoming emails — dead. The follow-up system that nudges curators — dead. The safety net that ensures outreach still happens — dead.

Four of six automated workflows failed. Zero alerts. Zero escalation. The system was running, green lights everywhere, doing absolutely nothing.

When a human finally looked, the damage:
- **6,560 curators** sitting in a queue that wasn't moving
- **0.2% enrichment yield** — 16 out of 7,546 curators had been processed
- **5 days of missed outreach** to 153 curators who had contact info ready
- **8 conversations** waiting for replies that were never surfaced
- **3 duplicate systems** all "owning" the same reply-checking task — and all three broken

The root cause? A single platform feature (`$vars`) stopped resolving. Every workflow that touched the database used it. None had fallbacks. None had health checks. None told anyone they were failing.

**This is what happens without Dispatch Protocol.**

A human had to SSH in, read logs, cross-reference six different automation systems, diagnose each failure individually, fix eight workflows, backfill missing data, clean up duplicates, reconfigure cron jobs, and hand-write a runbook for the agent that would take over.

It took four hours. It should have taken three commands.

---

## The Gap

The AI agent ecosystem standardized two layers in 2025:

| Layer | Standard | What it solves |
|-------|----------|---------------|
| Tool access | **MCP** (Anthropic) | How an agent uses external capabilities |
| Agent communication | **A2A** (Google/Linux Foundation) | How agents exchange messages |
| **Operational delegation** | **Nothing** | How an agent takes over and runs a pipeline |

MCP is the nervous system — it lets an agent call tools.
A2A is the language — it lets agents talk to each other.

Neither answers the question: **"I have scripts, workflows, and cron jobs across four platforms. How do I hand this to an AI agent and walk away?"**

That's operational delegation. And nobody owns it.

---

## Why Existing Tools Don't Solve This

### Try it with A2A alone

A2A defines Agent Cards and task messages. So you register your OpenClaw agent, your n8n instance, your systemd timers. Now what?

- A2A doesn't discover what's already running on your server
- A2A doesn't detect that three agents are polling the same inbox
- A2A doesn't generate runbooks for agents that wake up with no memory
- A2A doesn't know that n8n's `$vars` feature is broken on community edition
- A2A doesn't escalate to Claude Code when enrichment fails three times

A2A is a communication layer. It tells agents how to exchange messages. It doesn't tell them how to take over operations.

### Try it with CrewAI

CrewAI orchestrates agents within a single Python process. Your agents are functions that call each other.

But your infrastructure isn't a Python process. It's systemd timers on a VPS, n8n workflows in Docker, OpenClaw cron jobs in a JSON config, and Claude Code on your laptop. CrewAI can't SSH into your server. It can't reconfigure n8n via API. It can't detect that your systemd timer and your n8n workflow are duplicating work.

CrewAI is in-process orchestration. Your problem is cross-platform delegation.

### Try it with LangGraph

LangGraph gives you stateful graph workflows with checkpointing. Powerful for building complex agent logic within an application.

But it doesn't help when your VPS has a broken enrichment pipeline. It doesn't generate ORCHESTRATOR.md files for zero-context agents. It doesn't resolve ownership conflicts between systemd and n8n. It's a workflow engine, not an operational delegation protocol.

### The pattern

Every existing tool solves agent orchestration **within a controlled environment**. None solve the messy, heterogeneous, multi-platform reality of production infrastructure where agents come and go, platforms have quirks, and things fail silently.

---

## Dispatch Protocol

### The Thesis

Dispatch Protocol is the operational delegation layer for autonomous AI agents. It sits between the communication layer (A2A) and the agent platforms (OpenClaw, n8n, systemd, Claude Code). It handles the work that no protocol or framework currently addresses:

1. **Discovery** — What's running? Where? Who owns what?
2. **Conflict Resolution** — Multiple agents doing the same thing? Pick one owner.
3. **Runbook Generation** — Compile machine-readable task schemas into AI-readable instructions.
4. **Cross-Platform Deployment** — Push configs to OpenClaw, n8n, systemd from one place.
5. **Health Monitoring** — Verify tasks are working, not just running.
6. **Self-Healing Escalation** — When things break, route to the agent that can fix it.

### Design Principles

**One owner per task.** The single rule that prevents chaos. Every recurring task has exactly one automation system responsible for it. Not two. Not "both handle it with different frequencies." One.

**Zero-context by default.** Every agent session is fresh. The ORCHESTRATOR.md is compiled, not hand-written, so it can never drift. The agent re-reads it every time. No assumptions about what it remembers.

**Contracts, not prompts.** A task schema isn't "please enrich some curators." It's a contract: this command, this timeout, this success metric, this escalation path. Verifiable. Auditable.

**Self-healing before human-alerting.** Most failures follow patterns. API keys expire. Rate limits hit. Dependencies change. An interactive AI agent can diagnose and fix most of these. Humans are the last resort, not the first responder.

---

## Core Concepts

### 1. Agent Registry

Every agent declares what it is, what it can do, and what it can't.

```yaml
agents:
  openclaw:
    type: autonomous
    platform: openclaw
    capabilities: [run_scripts, web_search, send_telegram, read_write_files]
    constraints:
      - session_ephemeral    # wakes up with no memory
      - max_concurrent: 2
    schedule: cron
    connection:
      method: ssh+config
      config_path: /root/.openclaw/cron/jobs.json

  systemd:
    type: daemon
    platform: systemd
    capabilities: [run_scripts, high_frequency]
    constraints:
      - no_reporting          # can execute, can't communicate
      - no_decisions          # runs exactly what's configured
      - no_context            # no AI reasoning
    schedule: timer

  n8n:
    type: event_driven
    platform: n8n
    capabilities: [webhooks, http_requests, conditional_logic, integrations]
    constraints:
      - no_shell_access
      - vars_unreliable       # $vars breaks on community edition
    schedule: triggers+cron

  claude_code:
    type: interactive
    platform: claude-code
    capabilities: [diagnose, fix_code, architect, generate_runbooks, deploy_configs]
    constraints:
      - human_in_loop
      - session_based         # not always-on
    schedule: on_demand        # invoked by escalation or human
```

The registry is version-controlled. It's the single source of truth for "what runs where." When you run `dispatch discover`, it auto-generates this from what it finds on your infrastructure.

### 2. Task Schema

A task is a **contract**, not a prompt.

```yaml
task:
  id: enrich-curators
  name: Curator Contact Enrichment
  owner: openclaw
  priority: critical

  execute:
    workdir: /root/.openclaw/workspace/music-promo-engine
    pre_run: git pull
    command: python3 scripts/enrich_football_curators.py --limit 200 --all
    timeout: 600s
    isolation: true

  schedule:
    type: interval
    every: 6h

  success:
    metric: enriched_count > 0 OR pending_count == 0
    report:
      channel: telegram
      template: |
        Enriched {enriched} curators. {ready} ready for outreach.

  failure:
    threshold: 3
    report:
      channel: telegram
      template: |
        Enrichment failed: {error}. {count} consecutive failures.
    escalate:
      to: claude_code
      context: |
        Enrichment pipeline failing. Last 3 errors attached.
        Diagnose root cause and fix. Push to git when done.

  conflicts:
    - agent: n8n
      workflow_id: 7Nql1taOzuZgjtbw
      resolution: deactivated
      reason: "$vars broke on community edition, openclaw now owns this"
```

What this captures that nothing else does:
- **Exact commands** — copy-paste executable, no ambiguity
- **Success metrics** — not "it ran" but "it produced results"
- **Escalation chain** — who gets called when it breaks, with what context
- **Conflict history** — who else tried to own this and why they don't anymore

### 3. Compiled Runbook

The ORCHESTRATOR.md is **generated**, not written.

```bash
dispatch compile --output ORCHESTRATOR.md
```

The runbook is compiled from agent registry + task schemas + health contracts. When you change a task, the runbook updates automatically. When you add an agent, the runbook reflects it.

This solves the drift problem permanently. Hand-written runbooks go stale the moment you push a code change. Compiled runbooks can't — they're regenerated from the source of truth.

### 4. Health Contracts

Every task declares how to verify it's working. Not "it ran" — "it produced the right outcome."

```yaml
health:
  agent_checks:
    - agent: systemd
      check: systemctl is-active music-promo-queue.timer
      expect: active

    - agent: n8n
      check: http_status https://n8n.stringztechnologies.com
      expect: 200

  task_checks:
    - task: enrich-curators
      check: sql "SELECT COUNT(*) FROM curators WHERE enriched_at > NOW() - INTERVAL '24 hours'"
      warn_below: 5
      critical_below: 0

    - task: send-outreach
      check: sql "SELECT COUNT(*) FROM outreach_log WHERE email_sent_at > NOW() - INTERVAL '24 hours'"
      warn_below: 1

  escalation:
    warn: telegram
    critical: claude_code
```

This is what would have caught the March 1 failure in minutes, not days. The enrichment check would have fired: "0 curators enriched in 24 hours — critical." Escalation to Claude Code. Diagnosis. Fix. All before the next morning.

### 5. Escalation Chain

The self-healing hierarchy:

```
Level 0: Task runs normally
    ↓ failure
Level 1: Retry with backoff (agent handles internally)
    ↓ threshold exceeded  
Level 2: Dispatch runtime re-routes to backup agent
    ↓ no backup or backup also fails
Level 3: Escalate to interactive agent (Claude Code)
           → diagnoses root cause
           → fixes code
           → pushes to git
Level 4: Dispatch runtime re-deploys fixed task
    ↓ still failing
Level 5: Alert human with full context
```

Level 3 is the breakthrough. Most production failures are fixable by an AI that can read logs, understand code, and push fixes. The escalation chain encodes this — humans are the last resort, not the first call.

### 6. Conflict Resolution

The single hardest problem in multi-agent systems: **agents stepping on each other.**

```
$ dispatch conflicts --detect

CONFLICTS DETECTED:

  Task: check-replies
    Owner 1: systemd (music-promo-check-replies.timer) — every 5m
    Owner 2: n8n (Playlist Pitch Reply Checker) — every 15m  
    Owner 3: openclaw (Check playlist pitch replies) — every 6h

  Analysis:
    - systemd: highest frequency, most reliable for polling, no reporting
    - n8n: medium frequency, has reporting, but broken ($vars issue)
    - openclaw: lowest frequency, has reporting + AI reasoning

  Recommendation:
    systemd → sole owner (polling)
    openclaw → converted to status-report-only
    n8n → deactivated

$ dispatch conflicts --resolve
  ✓ Deactivated n8n workflow fIEPdXOBECYV5roI
  ✓ Updated openclaw job to report-only mode  
  ✓ systemd confirmed as sole owner of check-replies
```

This isn't theoretical. This exact conflict existed in production on March 1. Three systems were "handling" reply checking. All three were broken. Nobody noticed because each assumed the others were working.

---

## The Developer Experience

### Minute 0: You have a mess

Your VPS runs systemd timers, n8n in Docker, and OpenClaw. You have Python scripts for enrichment, outreach, and reply checking. Some are on cron. Some are triggered by n8n. Some are in the OpenClaw job queue. You're not sure which system owns what.

### Minute 1: Discovery

```
$ dispatch discover --ssh root@178.156.218.200

Scanning infrastructure...

  systemd timers:
    ✓ music-promo-queue.timer (every 2m) → execute_command.py
    ✓ music-promo-check-replies.timer (every 5m) → check_replies.py

  n8n workflows (9 found):
    ✓ AI Draft Reply (webhook) — active, last success: 2m ago
    ✓ Outreach Batch Approval (hourly) — active, last success: 30m ago
    ✗ Enrichment Scheduler (6h) — active, last 5 runs: ERROR
    ✗ Reply Checker (15m) — active, last 5 runs: ERROR
    ✗ Follow-up Nudger (daily) — active, last 3 runs: ERROR
    ✗ Safety Net (hourly) — active, last 2 runs: ERROR
    ○ Warmup Scheduler — inactive
    ✓ Telegram Bot (webhook) — active, no recent runs
    ✓ TowJI Content Pipeline — active, last success: 9h ago

  openclaw cron jobs (2 found):
    ✓ playlist-outreach-daily (daily 16:30) — last success: yesterday
    ✓ Check playlist pitch replies (6h) — last success: 6h ago

  scripts (in /root/.openclaw/workspace/music-promo-engine/scripts/):
    enrich_football_curators.py
    send_outreach_v2.py
    check_replies.py
    send_reply.py
    ...

  CONFLICTS DETECTED: 2
    check-replies: systemd + n8n + openclaw (3 owners)
    enrichment: n8n + none (n8n broken, no backup)

Generated: agents.yaml, tasks/ (6 tasks), health.yaml
```

One command. Full infrastructure audit. Conflicts identified. Schemas generated.

### Minute 2: Plan

```
$ dispatch plan

PROPOSED TASK OWNERSHIP:

  enrich-curators ──→ openclaw (every 6h, limit 200)
    Currently: n8n (broken — $vars not resolving)
    Change: deactivate n8n, create openclaw cron job

  send-outreach ──→ openclaw (daily 16:30 UTC, --ramp)
    Currently: openclaw (wrong path: /tmp/music-promo-engine)
    Change: fix workdir to /root/.openclaw/workspace/music-promo-engine

  check-replies ──→ systemd (every 5m)
    Currently: systemd + n8n + openclaw (3 owners!)
    Change: keep systemd, deactivate n8n, convert openclaw to report-only

  health-check ──→ openclaw (daily 8:00 UTC)
    Currently: nothing
    Change: new job — morning briefing with pipeline stats

  ai-draft-reply ──→ n8n (webhook)
    Currently: n8n (working)
    Change: none

  follow-up-nudger ──→ n8n (daily 10am)
    Currently: n8n (broken — type validation error)
    Change: fix typeValidation strict→loose, fix telegram credential

HEALTH MONITORING:
  4 agent checks, 6 task checks
  Escalation: warn→telegram, critical→claude_code

Accept this plan? [y/n]
```

### Minute 3: Deploy

```
$ dispatch deploy

  ✓ Generated ORCHESTRATOR.md (read by openclaw each session)
  ✓ Created openclaw job: music-promo-enrichment (every 6h)
  ✓ Updated openclaw job: playlist-outreach-daily (fixed path)
  ✓ Updated openclaw job: Check playlist pitch replies (report-only)
  ✓ Created openclaw job: music-promo-health-check (daily 8am)
  ✓ Deactivated n8n workflow: Enrichment Scheduler
  ✓ Deactivated n8n workflow: Reply Checker
  ✓ Fixed n8n workflow: Follow-up Nudger (type + credential)
  ✓ Fixed n8n workflow: Safety Net (hardcoded Supabase URL)
  ✓ Deployed health.yaml monitoring
  
  Running verification...
  ✓ All agents responsive
  ✓ All tasks have exactly one owner
  ✓ Health checks passing (4/4 agent, 5/6 task)
  ⚠ enrich-curators: 0 enriched in 24h (expected: will improve after first run)

Deployment complete. Next enrichment run in 5h 42m.
```

Three commands. Four hours of manual work — automated.

---

## Architecture

```
                    ┌──────────────────────────┐
                    │      dispatch CLI         │
                    │  discover | plan | deploy │
                    │  compile | status | verify│
                    │  conflicts | escalate     │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────┴─────────────┐
                    │     Dispatch Runtime      │
                    │  ┌─────────────────────┐  │
                    │  │  Task Scheduler     │  │
                    │  │  Health Monitor     │  │
                    │  │  Conflict Detector  │  │
                    │  │  Escalation Manager │  │
                    │  │  Runbook Compiler   │  │
                    │  └─────────────────────┘  │
                    └──┬──────┬──────┬──────┬──┘
                       │      │      │      │
              ┌────────┴┐  ┌──┴──┐ ┌─┴────┐ ┌┴──────────┐
              │ OpenClaw│  │ n8n │ │system│ │Claude Code│
              │ Adapter │  │Adapt│ │Adapt │ │  Adapter  │
              └─────────┘  └─────┘ └──────┘ └───────────┘
                   │          │        │          │
                   ▼          ▼        ▼          ▼
              jobs.json    REST API  systemctl  /sc:agent-handoff
```

### Adapters

Each adapter knows how to read from and write to its platform:

| Adapter | Reads | Writes | Deactivates |
|---------|-------|--------|-------------|
| OpenClaw | `jobs.json` via SSH | Inserts/updates jobs | Sets `enabled: false` |
| n8n | `GET /workflows` | `PUT /workflows/{id}` | `POST /workflows/{id}/deactivate` |
| systemd | `systemctl list-timers` | `systemctl enable/disable` | `systemctl stop` |
| Claude Code | Session history | Generates /sc:agent-handoff | N/A (interactive) |

### File Structure

```
project/
├── dispatch.yaml              # Project config (which VPS, which agents)
├── agents.yaml                # Agent registry (auto-generated by discover)
├── tasks/
│   ├── enrich-curators.yaml   # Task contracts
│   ├── send-outreach.yaml
│   ├── check-replies.yaml
│   └── health-check.yaml
├── health.yaml                # Health contracts
└── ORCHESTRATOR.md            # Compiled runbook (never hand-edited)
```

---

## Competitive Position

| Capability | A2A | CrewAI | LangGraph | OpenAI Swarm | **Dispatch** |
|-----------|-----|--------|-----------|-------------|-------------|
| Agent communication | ✅ | — | — | — | Uses A2A |
| In-process orchestration | — | ✅ | ✅ | ✅ | — |
| Cross-platform delegation | ✅ | — | — | — | ✅ |
| Infrastructure discovery | — | — | — | — | **✅** |
| Conflict detection | — | — | — | — | **✅** |
| Compiled runbooks | — | — | — | — | **✅** |
| Health monitoring | — | — | — | — | **✅** |
| Self-healing escalation | — | — | — | — | **✅** |
| Zero-context agents | — | — | — | — | **✅** |
| Heterogeneous platforms | Partial | — | — | — | **✅** |
| Works without code changes | — | — | — | — | **✅** |

**The key differentiator**: Every other tool assumes agents are software components in a controlled environment. Dispatch Protocol assumes agents are **independent systems on heterogeneous infrastructure** that wake up with no memory, run tasks, and disappear.

This is the reality of production AI operations in 2026.

---

## The Economics

Without Dispatch Protocol, the March 1 failure cost:

| Impact | Cost |
|--------|------|
| 5 days of missed enrichment (6,560 curators stalled) | ~500 potential outreach contacts lost |
| 5 days of broken reply checking | Unknown missed replies |
| 4 hours of manual diagnosis and repair | $200+ of developer time |
| Missed outreach during peak ramp period (day 8-13) | Delayed campaign by a week |
| Duplicate outreach entries created by conflicting systems | Data cleanup required |
| Damaged sender reputation (inconsistent sending patterns) | Long-term deliverability risk |

The total cost of one undetected failure cascade: **a week of pipeline progress, hours of manual work, and compounding downstream effects.**

A health check running every 24 hours with escalation to an AI agent would have caught this within a day. The fix (hardcoding values that `$vars` couldn't resolve) would have taken an AI agent 5 minutes.

**Cost of Dispatch Protocol: one config file and three commands.**
**Cost of not having it: measured in weeks.**

---

## Relationship to Existing Projects

```
stringztechnologies/
│
├── dispatch-protocol/              # The framework
│   ├── src/
│   │   ├── cli.py                  # dispatch CLI
│   │   ├── runtime.py              # Scheduler, monitor, escalation
│   │   └── compiler.py             # Runbook generator
│   ├── adapters/
│   │   ├── openclaw.py             # ← absorbs dispatchnow
│   │   ├── n8n.py
│   │   ├── systemd.py
│   │   └── claude_code.py
│   ├── schema/
│   │   ├── agents.schema.yaml
│   │   ├── task.schema.yaml
│   │   └── health.schema.yaml
│   └── examples/
│       └── music-promo-engine/     # Reference implementation
│
├── openclaw-skills/                # Skills collection
│   ├── SKILL.md                    # /sc:agent-handoff (speaks Dispatch Protocol)
│   └── skills/medium-publisher/
│
├── music-promo-engine/             # First consumer
│   ├── dispatch.yaml
│   ├── agents.yaml
│   ├── tasks/
│   └── ORCHESTRATOR.md             # Generated by dispatch compile
```

`dispatchnow` merges into `dispatch-protocol/adapters/openclaw.py`.
`agent-handoff` becomes the Claude Code adapter that generates Dispatch Protocol configs.
`music-promo-engine` becomes the reference implementation — proof the protocol works on real infrastructure.

---

## Implementation Phases

### Phase 1: Schema + Discovery (Week 1-2)
Define YAML schemas. Build `dispatch discover` — SSH into a VPS, detect systemd timers, n8n workflows, OpenClaw jobs, and scripts. Auto-generate `agents.yaml` and task stubs.

### Phase 2: Planning + Compilation (Week 3-4)
Build `dispatch plan` — analyze discovered infrastructure, detect conflicts, propose ownership. Build `dispatch compile` — generate ORCHESTRATOR.md from schemas.

### Phase 3: Deployment Adapters (Week 5-6)
Build adapters for OpenClaw (jobs.json), n8n (REST API), and systemd (systemctl). Build `dispatch deploy` — push configurations to all platforms from one command.

### Phase 4: Health + Escalation (Week 7-8)
Build health check runner. Build failure detection with consecutive-failure tracking. Build escalation chain with Claude Code integration. Build Telegram reporter.

### Phase 5: Self-Healing Loop (Week 9-10)
Connect the full loop: task fails → health check detects → escalation fires → Claude Code diagnoses → fix pushed to git → dispatch re-deploys → task recovers. The complete autonomous operations cycle.

---

*Dispatch Protocol. Built by Stringz Technologies.*
*Extracted from real production failures. Designed so they never happen again.*
