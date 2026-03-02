"""Dispatch Protocol CLI — discover, plan, deploy."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import click
import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.models import (
    Agent,
    AgentCheck,
    AgentConnection,
    AgentRegistry,
    AgentType,
    EscalationConfig,
    ExecuteBlock,
    HealthContract,
    Platform,
    Priority,
    ScheduleBlock,
    ScheduleType,
    Task,
    TaskCheck,
)

console = Console()


# ── Discovery data structures ──

@dataclass
class DiscoveredTimer:
    name: str
    schedule: str
    active: bool
    exec_start: str = ""
    unit_file: str = ""


@dataclass
class DiscoveredWorkflow:
    id: str
    name: str
    active: bool
    trigger_type: str = "unknown"
    last_status: str = "unknown"
    recent_failures: int = 0


@dataclass
class DiscoveredJob:
    name: str
    enabled: bool
    schedule_kind: str = ""
    schedule_value: str = ""
    message: str = ""


@dataclass
class DiscoveredScript:
    path: str
    modified: str = ""


@dataclass
class Conflict:
    task_name: str
    owners: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""


@dataclass
class DiscoveryResult:
    timers: list[DiscoveredTimer] = field(default_factory=list)
    workflows: list[DiscoveredWorkflow] = field(default_factory=list)
    jobs: list[DiscoveredJob] = field(default_factory=list)
    scripts: list[DiscoveredScript] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)


# ── Parsers ──

def parse_systemd_timers(output: str) -> list[DiscoveredTimer]:
    """Parse `systemctl list-timers --all --no-pager` output."""
    timers = []
    lines = output.strip().split("\n")
    for line in lines:
        # Skip header and footer lines
        if not line.strip() or line.startswith("NEXT") or line.startswith("Pass") or "timers listed" in line:
            continue
        # Format: NEXT LEFT LAST PASSED UNIT ACTIVATES
        parts = line.split()
        if len(parts) < 2:
            continue
        # Find .timer in the parts
        timer_name = ""
        for p in parts:
            if p.endswith(".timer"):
                timer_name = p
                break
        if not timer_name:
            continue

        # Extract schedule from NEXT/LEFT columns (approximate)
        schedule = ""
        for i, p in enumerate(parts):
            if p in ("left", "ago") and i > 0:
                schedule = parts[i - 1]
                break

        timers.append(DiscoveredTimer(
            name=timer_name.replace(".timer", ""),
            schedule=schedule,
            active=True,
        ))
    return timers


def parse_systemd_unit(output: str) -> str:
    """Extract ExecStart from `systemctl cat <unit>` output."""
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("ExecStart="):
            return line.split("=", 1)[1].strip()
    return ""


def parse_n8n_workflows(data: list[dict[str, Any]]) -> list[DiscoveredWorkflow]:
    """Parse n8n API workflow list response."""
    workflows = []
    for wf in data:
        trigger = "unknown"
        nodes = wf.get("nodes", [])
        for node in nodes:
            ntype = node.get("type", "").lower()
            if "webhook" in ntype:
                trigger = "webhook"
                break
            elif "cron" in ntype or "schedule" in ntype:
                trigger = "cron"
                break

        workflows.append(DiscoveredWorkflow(
            id=str(wf.get("id", "")),
            name=wf.get("name", "Unknown"),
            active=wf.get("active", False),
            trigger_type=trigger,
        ))
    return workflows


def parse_openclaw_jobs(data: list[dict[str, Any]]) -> list[DiscoveredJob]:
    """Parse OpenClaw jobs.json entries."""
    jobs = []
    for job in data:
        sched = job.get("schedule", {})
        kind = sched.get("kind", "")
        value = ""
        if kind == "every":
            ms = sched.get("everyMs", 0)
            if ms >= 3600000:
                value = f"{ms // 3600000}h"
            elif ms >= 60000:
                value = f"{ms // 60000}m"
            else:
                value = f"{ms}ms"
        elif kind == "cron":
            value = sched.get("cron", "")
        elif kind == "daily":
            value = sched.get("time", "")

        msg = ""
        payload = job.get("payload", {})
        if isinstance(payload, dict):
            msg = payload.get("message", "")

        jobs.append(DiscoveredJob(
            name=job.get("name", "unnamed"),
            enabled=job.get("enabled", True),
            schedule_kind=kind,
            schedule_value=value,
            message=msg[:100],
        ))
    return jobs


# ── Conflict Detection ──

def _normalize(name: str) -> str:
    """Normalize a name for fuzzy comparison."""
    name = name.lower()
    name = re.sub(r"[-_.\s]+", " ", name)
    # Remove common prefixes
    for prefix in ("music promo ", "playlist ", "towji "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip()


def detect_conflicts(
    timers: list[DiscoveredTimer],
    workflows: list[DiscoveredWorkflow],
    jobs: list[DiscoveredJob],
) -> list[Conflict]:
    """Detect multiple platforms owning the same logical task."""
    # Build a list of (normalized_name, platform, original_name)
    all_tasks: list[tuple[str, str, str]] = []
    for t in timers:
        all_tasks.append((_normalize(t.name), "systemd", t.name))
    for w in workflows:
        all_tasks.append((_normalize(w.name), "n8n", w.name))
    for j in jobs:
        all_tasks.append((_normalize(j.name), "openclaw", j.name))

    conflicts: list[Conflict] = []
    seen: set[tuple[int, int]] = set()

    for i in range(len(all_tasks)):
        for j in range(i + 1, len(all_tasks)):
            if (i, j) in seen:
                continue
            n1, p1, orig1 = all_tasks[i]
            n2, p2, orig2 = all_tasks[j]
            if p1 == p2:
                continue
            ratio = SequenceMatcher(None, n1, n2).ratio()
            if ratio > 0.65:
                seen.add((i, j))
                # Check if conflict already exists for this group
                found = False
                for c in conflicts:
                    existing_names = [_normalize(o["name"]) for o in c.owners]
                    if any(SequenceMatcher(None, n1, en).ratio() > 0.6 for en in existing_names):
                        if not any(o["name"] == orig2 for o in c.owners):
                            c.owners.append({"platform": p2, "name": orig2})
                        found = True
                        break
                if not found:
                    conflicts.append(Conflict(
                        task_name=orig1,
                        owners=[
                            {"platform": p1, "name": orig1},
                            {"platform": p2, "name": orig2},
                        ],
                        reason=f"Name similarity: {ratio:.0%}",
                    ))
    return conflicts


# ── Runner ──

def run_local(cmd: str) -> str:
    """Run a command locally and return stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout
    except (subprocess.TimeoutExpired, Exception):
        return ""


def run_ssh(cmd: str, host: str) -> str:
    """Run a command via SSH and return stdout."""
    import paramiko

    parts = host.split("@")
    if len(parts) == 2:
        user, hostname = parts
    else:
        user, hostname = "root", parts[0]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname, username=user, timeout=10)
        _, stdout, _ = client.exec_command(cmd, timeout=30)
        return stdout.read().decode()
    except Exception:
        return ""
    finally:
        client.close()


def discover_infrastructure(
    ssh_host: str | None = None,
    n8n_url: str = "http://localhost:5678",
    n8n_api_key: str | None = None,
    openclaw_jobs_path: str = "/root/.openclaw/cron/jobs.json",
    script_dirs: list[str] | None = None,
) -> DiscoveryResult:
    """Discover all automation infrastructure."""
    run = run_local if ssh_host is None else lambda cmd: run_ssh(cmd, ssh_host)
    result = DiscoveryResult()

    # 1. systemd timers
    timer_output = run("systemctl list-timers --all --no-pager")
    result.timers = parse_systemd_timers(timer_output)

    # Enrich with ExecStart
    for timer in result.timers:
        unit_output = run(f"systemctl cat {timer.name}.service 2>/dev/null")
        timer.exec_start = parse_systemd_unit(unit_output)

    # 2. n8n workflows
    try:
        headers: dict[str, str] = {}
        if n8n_api_key:
            headers["X-N8N-API-KEY"] = n8n_api_key
        resp = httpx.get(f"{n8n_url}/api/v1/workflows", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            wf_list = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(wf_list, list):
                result.workflows = parse_n8n_workflows(wf_list)
    except Exception:
        pass

    # 3. OpenClaw cron jobs
    jobs_content = run(f"cat {openclaw_jobs_path} 2>/dev/null")
    if jobs_content.strip():
        try:
            jobs_data = json.loads(jobs_content)
            if isinstance(jobs_data, list):
                result.jobs = parse_openclaw_jobs(jobs_data)
            elif isinstance(jobs_data, dict) and "jobs" in jobs_data:
                result.jobs = parse_openclaw_jobs(jobs_data["jobs"])
        except json.JSONDecodeError:
            pass

    # 4. Scripts
    dirs = script_dirs or [
        "/root/.openclaw/workspace/music-promo-engine/scripts",
        "/root/.openclaw/workspace/scripts",
    ]
    for d in dirs:
        ls_output = run(f"find {d} -maxdepth 2 -name '*.py' -o -name '*.sh' 2>/dev/null")
        for line in ls_output.strip().split("\n"):
            if line.strip():
                result.scripts.append(DiscoveredScript(path=line.strip()))

    # 5. Conflict detection
    result.conflicts = detect_conflicts(result.timers, result.workflows, result.jobs)

    return result


# ── Output Generators ──

def generate_agents_yaml(result: DiscoveryResult, path: Path) -> None:
    """Auto-generate agents.yaml from discovery."""
    registry = AgentRegistry()

    if result.timers:
        registry.agents["systemd"] = Agent(
            name="systemd",
            type=AgentType.DAEMON,
            platform=Platform.SYSTEMD,
            capabilities=["run_scripts", "high_frequency"],
            constraints=["no_reporting", "no_decisions"],
            schedule="timer",
            connection=AgentConnection(method="systemctl"),
        )

    if result.workflows:
        registry.agents["n8n"] = Agent(
            name="n8n",
            type=AgentType.EVENT_DRIVEN,
            platform=Platform.N8N,
            capabilities=["webhooks", "http_requests", "conditional_logic"],
            constraints=["no_shell_access"],
            schedule="triggers+cron",
            connection=AgentConnection(method="rest_api", api_url="http://localhost:5678"),
        )

    if result.jobs:
        registry.agents["openclaw"] = Agent(
            name="openclaw",
            type=AgentType.AUTONOMOUS,
            platform=Platform.OPENCLAW,
            capabilities=["run_scripts", "web_search", "send_telegram", "read_write_files"],
            constraints=["session_ephemeral"],
            schedule="cron",
            connection=AgentConnection(method="ssh+config", config_path="/root/.openclaw/cron/jobs.json"),
        )

    registry.to_yaml(path)


def generate_task_stubs(result: DiscoveryResult, tasks_dir: Path) -> None:
    """Auto-generate task YAML stubs from discovery."""
    tasks_dir.mkdir(parents=True, exist_ok=True)

    all_items: list[tuple[str, str, str, str]] = []
    for t in result.timers:
        all_items.append((t.name, "systemd", t.exec_start or "# unknown command", t.schedule))
    for w in result.workflows:
        all_items.append((w.name, "n8n", f"# n8n workflow {w.id}", w.trigger_type))
    for j in result.jobs:
        all_items.append((j.name, "openclaw", j.message or "# see openclaw config", j.schedule_value))

    for name, owner, cmd, sched in all_items:
        task_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        task = Task(
            id=task_id,
            name=name,
            owner=owner,
            priority=Priority.MEDIUM,
            execute=ExecuteBlock(command=cmd),
            schedule=ScheduleBlock(type=ScheduleType.INTERVAL, every=sched or "6h"),
        )
        task.to_yaml(tasks_dir / f"{task_id}.yaml")


def generate_health_yaml(result: DiscoveryResult, path: Path) -> None:
    """Auto-generate health.yaml from discovery."""
    agent_checks = []
    if result.timers:
        for t in result.timers:
            agent_checks.append(AgentCheck(
                agent="systemd",
                check=f"systemctl is-active {t.name}.timer",
                expect="active",
            ))
    if result.workflows:
        agent_checks.append(AgentCheck(
            agent="n8n",
            check="http_status http://localhost:5678",
            expect="200",
        ))

    health = HealthContract(
        agent_checks=agent_checks,
        task_checks=[],
        escalation=EscalationConfig(warn="telegram", critical="claude_code"),
    )
    health.to_yaml(path)


# ── Rich Report ──

def print_report(result: DiscoveryResult) -> None:
    """Print a rich-formatted discovery report."""
    console.print()
    console.print(Panel.fit(
        "[bold white]Dispatch Protocol[/] — Infrastructure Discovery",
        border_style="blue",
    ))
    console.print()

    # systemd
    if result.timers:
        table = Table(title="systemd timers", title_style="bold cyan", border_style="dim")
        table.add_column("Name", style="white")
        table.add_column("Schedule", style="dim")
        table.add_column("ExecStart", style="dim", max_width=60)
        table.add_column("Status", justify="center")
        for t in result.timers:
            status = "[green]✓[/]" if t.active else "[red]✗[/]"
            table.add_row(t.name, t.schedule, t.exec_start[:60] or "—", status)
        console.print(table)
        console.print()

    # n8n
    if result.workflows:
        table = Table(title="n8n workflows", title_style="bold cyan", border_style="dim")
        table.add_column("Name", style="white")
        table.add_column("ID", style="dim")
        table.add_column("Trigger", style="dim")
        table.add_column("Status", justify="center")
        for w in result.workflows:
            status = "[green]✓ active[/]" if w.active else "[yellow]○ inactive[/]"
            if w.recent_failures > 0:
                status = f"[red]✗ {w.recent_failures} failures[/]"
            table.add_row(w.name, w.id, w.trigger_type, status)
        console.print(table)
        console.print()

    # OpenClaw
    if result.jobs:
        table = Table(title="OpenClaw cron jobs", title_style="bold cyan", border_style="dim")
        table.add_column("Name", style="white")
        table.add_column("Schedule", style="dim")
        table.add_column("Enabled", justify="center")
        for j in result.jobs:
            enabled = "[green]✓[/]" if j.enabled else "[red]✗[/]"
            sched_str = f"{j.schedule_kind} {j.schedule_value}".strip()
            table.add_row(j.name, sched_str, enabled)
        console.print(table)
        console.print()

    # Scripts
    if result.scripts:
        console.print(f"[bold cyan]Scripts found:[/] {len(result.scripts)}")
        for s in result.scripts[:10]:
            console.print(f"  [dim]{s.path}[/]")
        if len(result.scripts) > 10:
            console.print(f"  [dim]... and {len(result.scripts) - 10} more[/]")
        console.print()

    # Conflicts
    if result.conflicts:
        console.print(Panel(
            f"[bold red]CONFLICTS DETECTED: {len(result.conflicts)}[/]",
            border_style="red",
        ))
        for c in result.conflicts:
            owners_str = ", ".join(f"[bold]{o['platform']}[/] ({o['name']})" for o in c.owners)
            console.print(f"  [yellow]Task:[/] {c.task_name}")
            console.print(f"    Owners: {owners_str}")
            console.print(f"    Reason: {c.reason}")
            console.print()
    else:
        console.print("[green]No conflicts detected.[/]")
        console.print()


# ── CLI ──

@click.group()
def cli():
    """Dispatch Protocol — operational delegation for autonomous AI agents."""
    pass


@cli.command()
@click.option("--ssh", "ssh_host", default=None, help="SSH target (user@host)")
@click.option("--local", "is_local", is_flag=True, default=True, help="Discover on local machine (default)")
@click.option("--n8n-url", default="http://localhost:5678", help="n8n API URL")
@click.option("--n8n-key", default=None, help="n8n API key")
@click.option("--jobs-path", default="/root/.openclaw/cron/jobs.json", help="OpenClaw jobs.json path")
@click.option("--output", "-o", default=".", help="Output directory for generated files")
def discover(ssh_host: str | None, is_local: bool, n8n_url: str, n8n_key: str | None, jobs_path: str, output: str):
    """Discover infrastructure: systemd timers, n8n workflows, OpenClaw jobs, scripts."""
    console.print("[bold blue]Scanning infrastructure...[/]\n")

    host = ssh_host if ssh_host else None
    result = discover_infrastructure(
        ssh_host=host,
        n8n_url=n8n_url,
        n8n_api_key=n8n_key,
        openclaw_jobs_path=jobs_path,
    )

    print_report(result)

    # Generate files
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    generate_agents_yaml(result, out / "agents.yaml")
    console.print(f"[green]Generated:[/] {out / 'agents.yaml'}")

    generate_task_stubs(result, out / "tasks")
    task_count = len(list((out / "tasks").glob("*.yaml")))
    console.print(f"[green]Generated:[/] {out / 'tasks'}/ ({task_count} tasks)")

    generate_health_yaml(result, out / "health.yaml")
    console.print(f"[green]Generated:[/] {out / 'health.yaml'}")

    console.print()
    summary = Text()
    summary.append("Discovery complete. ", style="bold green")
    summary.append(f"{len(result.timers)} timers, {len(result.workflows)} workflows, {len(result.jobs)} jobs, {len(result.scripts)} scripts")
    if result.conflicts:
        summary.append(f", [bold red]{len(result.conflicts)} conflicts[/]")
    console.print(summary)


if __name__ == "__main__":
    cli()
