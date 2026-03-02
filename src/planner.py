"""Dispatch Protocol — Plan engine.

Analyzes discovery results + existing task schemas to propose ownership,
detect conflicts, and recommend changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from src.models import (
    Agent,
    AgentRegistry,
    AgentType,
    HealthContract,
    Platform,
    Priority,
    Task,
)
from src.cli import (
    Conflict,
    DiscoveredJob,
    DiscoveredTimer,
    DiscoveredWorkflow,
    DiscoveryResult,
)

console = Console()


# ── Ownership rules ──

# Ownership hierarchy from VISION.md:
# systemd: high-frequency (<15min), no reporting needed
# agent cron (openclaw): tasks needing decision-making, reporting, variable logic
# n8n: webhook-triggered flows, visual workflows, multi-step integrations

PLATFORM_SCORES = {
    "systemd": {"frequency": 3, "reporting": 0, "decisions": 0, "webhooks": 0, "reliability": 3},
    "n8n": {"frequency": 1, "reporting": 2, "decisions": 1, "webhooks": 3, "reliability": 1},
    "openclaw": {"frequency": 1, "reporting": 3, "decisions": 3, "webhooks": 0, "reliability": 2},
}


class ChangeType(str, Enum):
    KEEP = "keep"
    CREATE = "create"
    DEACTIVATE = "deactivate"
    FIX = "fix"
    CONVERT = "convert"  # e.g., convert to report-only


@dataclass
class TaskProposal:
    """A proposed ownership assignment for a task."""
    task_id: str
    task_name: str
    proposed_owner: str
    current_owners: list[dict[str, str]] = field(default_factory=list)
    change: ChangeType = ChangeType.KEEP
    reason: str = ""
    schedule: str = ""
    actions: list[str] = field(default_factory=list)  # specific changes to make


@dataclass
class Plan:
    """A complete ownership plan for all discovered tasks."""
    proposals: list[TaskProposal] = field(default_factory=list)
    health_additions: list[str] = field(default_factory=list)
    summary: str = ""


def _infer_task_needs(
    name: str,
    timer: DiscoveredTimer | None = None,
    workflow: DiscoveredWorkflow | None = None,
    job: DiscoveredJob | None = None,
) -> dict[str, int]:
    """Score a task's needs to determine best platform."""
    needs = {"frequency": 0, "reporting": 0, "decisions": 0, "webhooks": 0, "reliability": 0}
    name_lower = name.lower()

    # High frequency indicators
    if timer and timer.schedule:
        try:
            val = timer.schedule.replace("s", "").replace("m", "").replace("h", "")
            if "s" in timer.schedule or ("m" in timer.schedule and int(val) < 15):
                needs["frequency"] = 3
        except (ValueError, IndexError):
            pass

    # Reporting indicators
    if any(kw in name_lower for kw in ("report", "brief", "health", "monitor", "check")):
        needs["reporting"] = 2

    # Decision-making indicators
    if any(kw in name_lower for kw in ("enrich", "outreach", "draft", "reply", "approve")):
        needs["decisions"] = 2

    # Webhook indicators
    if workflow and workflow.trigger_type == "webhook":
        needs["webhooks"] = 3

    # Reliability — everything needs some
    needs["reliability"] = 2

    return needs


def _best_platform(needs: dict[str, int]) -> str:
    """Pick the best platform for a task based on its needs."""
    scores: dict[str, float] = {}
    for platform, capabilities in PLATFORM_SCORES.items():
        score = sum(needs.get(k, 0) * capabilities.get(k, 0) for k in needs)
        scores[platform] = score

    # Webhook tasks must go to n8n
    if needs.get("webhooks", 0) >= 3:
        return "n8n"

    return max(scores, key=lambda p: scores[p])


def generate_plan(
    result: DiscoveryResult,
    existing_tasks: dict[str, Task] | None = None,
) -> Plan:
    """Generate an ownership plan from discovery results."""
    plan = Plan()
    existing_tasks = existing_tasks or {}

    # Index all discovered items by normalized name
    timer_map: dict[str, DiscoveredTimer] = {}
    for t in result.timers:
        # Skip system timers
        if any(skip in t.name for skip in (
            "apt-", "sysstat", "motd", "dpkg", "logrotate", "systemd-tmp",
            "man-db", "e2scrub", "fstrim", "update-notif", "apport", "snapd", "ua-timer",
        )):
            continue
        timer_map[t.name] = t

    workflow_map: dict[str, DiscoveredWorkflow] = {w.name: w for w in result.workflows}
    job_map: dict[str, DiscoveredJob] = {j.name: j for j in result.jobs}

    # Build a unified task list from all sources
    seen_ids: set[str] = set()

    # Process conflicts first — these need explicit resolution
    for conflict in result.conflicts:
        task_name = conflict.task_name
        task_id = task_name.lower().replace(" ", "-")
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)

        # Determine needs from all owners
        timer = None
        workflow = None
        job = None
        for owner in conflict.owners:
            if owner["platform"] == "systemd":
                timer = timer_map.get(owner["name"])
            elif owner["platform"] == "n8n":
                workflow = workflow_map.get(owner["name"])
            elif owner["platform"] == "openclaw":
                job = job_map.get(owner["name"])

        needs = _infer_task_needs(task_name, timer, workflow, job)
        best = _best_platform(needs)

        proposal = TaskProposal(
            task_id=task_id,
            task_name=task_name,
            proposed_owner=best,
            current_owners=conflict.owners,
            change=ChangeType.KEEP,
            reason=f"Best fit based on task needs (frequency={needs['frequency']}, reporting={needs['reporting']}, decisions={needs['decisions']})",
        )

        # Generate specific actions for each current owner
        for owner in conflict.owners:
            if owner["platform"] == best:
                proposal.actions.append(f"✓ {owner['platform']} ({owner['name']}) — KEEP as sole owner")
            else:
                proposal.actions.append(f"✗ {owner['platform']} ({owner['name']}) — DEACTIVATE")
                proposal.change = ChangeType.DEACTIVATE

        plan.proposals.append(proposal)

    # Process remaining timers
    for name, timer in timer_map.items():
        tid = name.lower().replace(" ", "-")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        needs = _infer_task_needs(name, timer=timer)
        best = _best_platform(needs)
        plan.proposals.append(TaskProposal(
            task_id=tid,
            task_name=name,
            proposed_owner=best if best != "systemd" else "systemd",
            current_owners=[{"platform": "systemd", "name": name}],
            change=ChangeType.KEEP,
            reason="Single owner, no conflict",
            schedule=timer.schedule,
        ))

    # Process remaining workflows
    for name, wf in workflow_map.items():
        tid = name.lower().replace(" ", "-")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        needs = _infer_task_needs(name, workflow=wf)
        best = _best_platform(needs)
        plan.proposals.append(TaskProposal(
            task_id=tid,
            task_name=name,
            proposed_owner=best,
            current_owners=[{"platform": "n8n", "name": name}],
            change=ChangeType.KEEP,
            reason="Single owner, no conflict",
            schedule=wf.trigger_type,
        ))

    # Process remaining jobs — these are already assigned to openclaw, keep them there
    # unless there's a strong reason to move (e.g., high-frequency polling)
    for name, job in job_map.items():
        tid = name.lower().replace(" ", "-")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        sched = f"{job.schedule_kind} {job.schedule_value}".strip()

        # Jobs already on openclaw stay on openclaw — they were configured there for a reason
        # Only consider moving if it's a high-frequency polling task (< 5min interval)
        owner = "openclaw"
        reason = "Single owner, no conflict — keeping on existing platform"
        if job.schedule_kind == "every" and job.schedule_value:
            try:
                val = job.schedule_value.rstrip("hmsd")
                unit = job.schedule_value[-1] if job.schedule_value[-1] in "hms" else ""
                mins = float(val) * (60 if unit == "h" else 1 if unit == "m" else 1/60 if unit == "s" else 1)
                if mins < 5:
                    owner = "systemd"
                    reason = f"High-frequency task ({job.schedule_value}) — better on systemd"
            except (ValueError, IndexError):
                pass

        plan.proposals.append(TaskProposal(
            task_id=tid,
            task_name=name,
            proposed_owner=owner,
            current_owners=[{"platform": "openclaw", "name": name}],
            change=ChangeType.KEEP,
            reason=reason,
            schedule=sched,
        ))

    # Check for tasks that need health monitoring but don't have it
    for p in plan.proposals:
        if p.proposed_owner in ("openclaw", "n8n"):
            plan.health_additions.append(f"Add task check for: {p.task_name}")

    conflicts_count = len(result.conflicts)
    total = len(plan.proposals)
    changes = sum(1 for p in plan.proposals if p.change != ChangeType.KEEP)
    plan.summary = f"{total} tasks analyzed, {conflicts_count} conflicts, {changes} changes proposed"

    return plan


def print_plan(plan: Plan) -> None:
    """Print a rich-formatted plan."""
    console.print()
    console.print(Panel.fit(
        "[bold white]Dispatch Protocol[/] — Ownership Plan",
        border_style="blue",
    ))
    console.print()
    console.print("[bold]PROPOSED TASK OWNERSHIP:\n")

    for p in plan.proposals:
        # Header
        owner_style = {
            "systemd": "bold red",
            "n8n": "bold yellow",
            "openclaw": "bold cyan",
            "claude_code": "bold magenta",
        }.get(p.proposed_owner, "bold white")

        console.print(f"  [white]{p.task_name}[/] ──→ [{owner_style}]{p.proposed_owner}[/]", end="")
        if p.schedule:
            console.print(f" [dim]({p.schedule})[/]")
        else:
            console.print()

        # Current owners
        if len(p.current_owners) > 1:
            owners_str = ", ".join(f"{o['platform']} ({o['name']})" for o in p.current_owners)
            console.print(f"    [dim]Currently:[/] {owners_str}")

        # Actions
        for action in p.actions:
            if action.startswith("✓"):
                console.print(f"    [green]{action}[/]")
            elif action.startswith("✗"):
                console.print(f"    [red]{action}[/]")
            else:
                console.print(f"    [yellow]{action}[/]")

        if p.reason and p.change != ChangeType.KEEP:
            console.print(f"    [dim]Reason: {p.reason}[/]")
        console.print()

    # Health
    if plan.health_additions:
        console.print("[bold]HEALTH MONITORING:\n")
        for h in plan.health_additions[:5]:
            console.print(f"  [dim]+ {h}[/]")
        if len(plan.health_additions) > 5:
            console.print(f"  [dim]... and {len(plan.health_additions) - 5} more[/]")
        console.print()

    console.print(f"[bold green]{plan.summary}[/]\n")


def save_plan(plan: Plan, path: Path) -> None:
    """Save plan to YAML for review/editing before deploy."""
    out: dict[str, Any] = {"plan": {"proposals": [], "summary": plan.summary}}
    for p in plan.proposals:
        entry: dict[str, Any] = {
            "task_id": p.task_id,
            "task_name": p.task_name,
            "proposed_owner": p.proposed_owner,
            "current_owners": p.current_owners,
            "change": p.change.value,
            "actions": p.actions,
        }
        if p.reason:
            entry["reason"] = p.reason
        if p.schedule:
            entry["schedule"] = p.schedule
        out["plan"]["proposals"].append(entry)
    with open(path, "w") as f:
        yaml.dump(out, f, default_flow_style=False, sort_keys=False)
