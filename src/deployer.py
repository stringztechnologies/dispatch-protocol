"""Dispatch Protocol — Deploy engine.

Reads plan.yaml and pushes configurations to all platforms via adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from adapters.base import AdapterResult
from adapters.openclaw import OpenClawAdapter
from adapters.n8n import N8nAdapter
from adapters.systemd import SystemdAdapter
from src.compiler import compile_orchestrator

console = Console()


def load_plan(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def deploy(
    plan_path: Path,
    agents_path: Path,
    tasks_dir: Path,
    health_path: Path,
    project_name: str = "Project",
    project_path: str = "",
    orchestrator_output: Path | None = None,
    ssh_host: str | None = None,
    n8n_url: str = "http://localhost:5678",
    n8n_key: str | None = None,
    openclaw_jobs_path: str = "/root/.openclaw/cron/jobs.json",
    dry_run: bool = False,
) -> list[AdapterResult]:
    """Execute a deployment plan.

    1. Reads plan.yaml
    2. For each proposal, calls the appropriate adapter
    3. Compiles ORCHESTRATOR.md
    4. Runs verification
    """
    plan_data = load_plan(plan_path)
    proposals = plan_data.get("plan", {}).get("proposals", [])

    # Initialize adapters
    adapters = {
        "openclaw": OpenClawAdapter(jobs_path=openclaw_jobs_path, ssh_host=ssh_host),
        "n8n": N8nAdapter(api_url=n8n_url, api_key=n8n_key),
        "systemd": SystemdAdapter(ssh_host=ssh_host),
    }

    results: list[AdapterResult] = []

    for proposal in proposals:
        task_id = proposal["task_id"]
        task_name = proposal["task_name"]
        owner = proposal["proposed_owner"]
        change = proposal.get("change", "keep")
        actions = proposal.get("actions", [])

        if change == "keep" and not actions:
            # No changes needed
            continue

        adapter = adapters.get(owner)
        if not adapter:
            results.append(AdapterResult(
                success=False, action="failed", target=task_id,
                details=f"No adapter for platform: {owner}",
            ))
            continue

        # Process deactivation actions (for conflict resolution)
        for action_str in actions:
            if "DEACTIVATE" in action_str:
                # Parse which platform to deactivate
                for platform_name, adp in adapters.items():
                    if platform_name in action_str.lower():
                        if dry_run:
                            results.append(AdapterResult(
                                success=True, action="dry-run:deactivate",
                                target=f"{platform_name}:{task_id}",
                                details=f"Would deactivate {task_id} on {platform_name}",
                            ))
                        else:
                            r = adp.deactivate_task(task_id)
                            results.append(r)
                        break

        # Process creation/update for the proposed owner
        if change in ("create", "deactivate"):
            # Load task config if available
            task_file = tasks_dir / f"{task_id}.yaml"
            config: dict[str, Any] = {}
            if task_file.exists():
                with open(task_file) as f:
                    task_data = yaml.safe_load(f)
                td = task_data.get("task", task_data)
                config = {
                    "schedule": td.get("schedule", {}),
                    "command": td.get("execute", {}).get("command", ""),
                    "workdir": td.get("execute", {}).get("workdir", ""),
                    "message": f"Read ORCHESTRATOR.md first. Then execute: {task_name}",
                }

            if dry_run:
                results.append(AdapterResult(
                    success=True, action=f"dry-run:{change}",
                    target=f"{owner}:{task_id}",
                    details=f"Would {change} {task_id} on {owner}",
                ))
            else:
                r = adapter.create_task(task_id, config)
                results.append(r)

    # Compile ORCHESTRATOR.md
    if orchestrator_output and agents_path.exists():
        compile_orchestrator(
            agents_path=agents_path,
            tasks_dir=tasks_dir,
            health_path=health_path,
            project_name=project_name,
            project_path=project_path,
            output=orchestrator_output,
        )
        results.append(AdapterResult(
            success=True, action="compiled",
            target=str(orchestrator_output),
            details=f"Compiled ORCHESTRATOR.md",
        ))

    # Verification pass — use original platform names, not normalized IDs
    console.print("\n[bold]Running verification...[/]")
    for proposal in proposals:
        task_id = proposal["task_id"]
        task_name = proposal.get("task_name", task_id)
        owner = proposal["proposed_owner"]
        adapter = adapters.get(owner)
        if adapter:
            # Try the original name from current_owners first, then task_id
            verify_name = task_id
            for co in proposal.get("current_owners", []):
                if co.get("platform") == owner:
                    verify_name = co["name"]
                    break
            vr = adapter.verify_task(verify_name)
            if not vr.success and verify_name != task_id:
                vr = adapter.verify_task(task_id)
            if vr.success:
                console.print(f"  [green]✓[/] {task_name} ({owner}): {vr.details}")
            else:
                console.print(f"  [yellow]⚠[/] {task_name} ({owner}): {vr.details}")

    return results


def print_deploy_results(results: list[AdapterResult]) -> None:
    """Print deployment results."""
    console.print()
    for r in results:
        if r.success:
            icon = "✓" if "dry-run" not in r.action else "○"
            color = "green" if "dry-run" not in r.action else "yellow"
            console.print(f"  [{color}]{icon}[/] {r.details}")
        else:
            console.print(f"  [red]✗[/] {r.target}: {r.details}")

    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success)
    console.print()
    if fail_count:
        console.print(f"[yellow]Deployment: {success_count} succeeded, {fail_count} failed[/]")
    else:
        console.print(f"[green]Deployment complete: {success_count} actions[/]")
