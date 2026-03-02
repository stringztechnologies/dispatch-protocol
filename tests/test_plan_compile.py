"""Tests for Phase 2 — plan + compile."""

import tempfile
from pathlib import Path

import yaml
import pytest

from src.cli import (
    Conflict,
    DiscoveredJob,
    DiscoveredTimer,
    DiscoveredWorkflow,
    DiscoveryResult,
)
from src.planner import generate_plan, save_plan, Plan
from src.compiler import compile_orchestrator
from src.models import (
    AgentRegistry,
    AgentType,
    Agent,
    AgentConnection,
    Platform,
    HealthContract,
    Task,
)


# ── Planner tests ──

class TestPlanner:
    def _make_result(self, **kwargs) -> DiscoveryResult:
        return DiscoveryResult(**kwargs)

    def test_plan_single_owner_no_changes(self):
        result = self._make_result(
            timers=[DiscoveredTimer(name="music-promo-queue", schedule="2m", active=True)],
            jobs=[DiscoveredJob(name="outreach-daily", enabled=True, schedule_kind="cron", schedule_value="16:30")],
        )
        plan = generate_plan(result)
        assert len(plan.proposals) == 2
        # No conflicts → no deactivations
        assert all(p.change.value == "keep" for p in plan.proposals)

    def test_plan_resolves_conflicts(self):
        result = self._make_result(
            timers=[DiscoveredTimer(name="check-replies", schedule="5m", active=True)],
            jobs=[DiscoveredJob(name="check-replies-monitor", enabled=True)],
            conflicts=[Conflict(
                task_name="check-replies",
                owners=[
                    {"platform": "systemd", "name": "check-replies"},
                    {"platform": "openclaw", "name": "check-replies-monitor"},
                ],
                reason="Name similarity: 80%",
            )],
        )
        plan = generate_plan(result)
        conflict_proposals = [p for p in plan.proposals if len(p.current_owners) > 1]
        assert len(conflict_proposals) >= 1
        # Should have actions
        assert any(len(p.actions) > 0 for p in conflict_proposals)

    def test_plan_webhook_goes_to_n8n(self):
        result = self._make_result(
            workflows=[DiscoveredWorkflow(id="1", name="ai-draft-reply", active=True, trigger_type="webhook")],
        )
        plan = generate_plan(result)
        wh_proposals = [p for p in plan.proposals if "draft" in p.task_name.lower()]
        assert len(wh_proposals) == 1
        assert wh_proposals[0].proposed_owner == "n8n"

    def test_plan_filters_system_timers(self):
        result = self._make_result(
            timers=[
                DiscoveredTimer(name="apt-daily", schedule="6h", active=True),
                DiscoveredTimer(name="music-promo-queue", schedule="2m", active=True),
            ],
        )
        plan = generate_plan(result)
        names = [p.task_name for p in plan.proposals]
        assert "apt-daily" not in names
        assert "music-promo-queue" in names

    def test_plan_health_additions(self):
        result = self._make_result(
            jobs=[DiscoveredJob(name="enrichment", enabled=True)],
        )
        plan = generate_plan(result)
        assert len(plan.health_additions) >= 1

    def test_save_plan_yaml(self, tmp_path):
        plan = Plan(
            proposals=[],
            summary="test",
        )
        out = tmp_path / "plan.yaml"
        save_plan(plan, out)
        with open(out) as f:
            data = yaml.safe_load(f)
        assert "plan" in data
        assert data["plan"]["summary"] == "test"


# ── Compiler tests ──

class TestCompiler:
    @pytest.fixture
    def setup_dir(self, tmp_path):
        """Create a minimal schema set for compilation."""
        # agents.yaml
        registry = AgentRegistry(agents={
            "openclaw": Agent(
                name="openclaw",
                type=AgentType.AUTONOMOUS,
                platform=Platform.OPENCLAW,
                capabilities=["run_scripts", "send_telegram"],
                schedule="cron",
                connection=AgentConnection(method="ssh+config"),
            ),
            "systemd": Agent(
                name="systemd",
                type=AgentType.DAEMON,
                platform=Platform.SYSTEMD,
                capabilities=["run_scripts"],
                schedule="timer",
            ),
        })
        registry.to_yaml(tmp_path / "agents.yaml")

        # tasks/
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_data = {
            "task": {
                "id": "enrich-curators",
                "name": "Curator Enrichment",
                "owner": "openclaw",
                "priority": "critical",
                "execute": {
                    "workdir": "/root/workspace",
                    "pre_run": "git pull",
                    "command": "python3 enrich.py --limit 200",
                    "timeout": "600s",
                    "isolation": True,
                },
                "schedule": {"type": "interval", "every": "6h"},
                "success": {
                    "metric": "enriched_count > 0",
                    "report": {"channel": "telegram", "template": "Enriched {count}"},
                },
                "failure": {
                    "threshold": 3,
                    "report": {"channel": "telegram", "template": "Failed: {error}"},
                    "escalate": {"to": "claude_code", "context": "Pipeline broken"},
                },
                "conflicts": [
                    {"agent": "n8n", "workflow_id": "abc", "resolution": "deactivated", "reason": "broken"},
                ],
            }
        }
        with open(tasks_dir / "enrich-curators.yaml", "w") as f:
            yaml.dump(task_data, f)

        # health.yaml
        health_data = {
            "health": {
                "agent_checks": [
                    {"agent": "systemd", "check": "systemctl is-active test.timer", "expect": "active"},
                ],
                "task_checks": [
                    {"task": "enrich-curators", "check": "sql SELECT COUNT(*)", "warn_below": 5, "critical_below": 0},
                ],
                "escalation": {"warn": "telegram", "critical": "claude_code"},
            }
        }
        with open(tmp_path / "health.yaml", "w") as f:
            yaml.dump(health_data, f)

        return tmp_path

    def test_compile_produces_markdown(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
            project_name="Test Project",
            project_path="/root/test",
        )
        assert "# Test Project — Orchestrator Instructions" in content
        assert "Auto-generated by Dispatch Protocol" in content
        assert "/root/test" in content

    def test_compile_includes_tasks(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "Curator Enrichment" in content
        assert "python3 enrich.py --limit 200" in content
        assert "git pull" in content
        assert "🔴 Critical" in content

    def test_compile_includes_health(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "Health Checks" in content
        assert "systemctl is-active test.timer" in content
        assert "Escalation Chain" in content
        assert "telegram" in content
        assert "claude_code" in content

    def test_compile_includes_conflict_history(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "n8n" in content
        assert "deactivated" in content

    def test_compile_includes_escalation(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "Escalate to:" in content or "Escalate →" in content
        assert "claude_code" in content

    def test_compile_includes_rules(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "Important Rules" in content
        assert "git pull" in content.lower()

    def test_compile_writes_file(self, setup_dir, tmp_path):
        out = tmp_path / "ORCHESTRATOR.md"
        compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
            output=out,
        )
        assert out.exists()
        assert out.read_text().startswith("#")

    def test_compile_filters_system_tasks(self, setup_dir):
        # Add a system task
        sys_data = {
            "task": {
                "id": "apt-daily",
                "name": "apt daily update",
                "owner": "systemd",
                "execute": {"command": "apt update"},
                "schedule": {"type": "interval"},
            }
        }
        with open(setup_dir / "tasks" / "apt-daily.yaml", "w") as f:
            yaml.dump(sys_data, f)

        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "apt daily update" not in content
        assert "Curator Enrichment" in content

    def test_compile_agent_table(self, setup_dir):
        content = compile_orchestrator(
            agents_path=setup_dir / "agents.yaml",
            tasks_dir=setup_dir / "tasks",
            health_path=setup_dir / "health.yaml",
        )
        assert "Registered Agents" in content
        assert "openclaw" in content
        assert "systemd" in content
