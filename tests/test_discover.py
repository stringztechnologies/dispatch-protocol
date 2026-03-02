"""Tests for Dispatch Protocol Phase 1 — schemas, parsers, conflict detection."""

import json
import tempfile
from pathlib import Path

import yaml
import pytest

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
    detect_conflicts,
    generate_agents_yaml,
    generate_health_yaml,
    generate_task_stubs,
    parse_n8n_workflows,
    parse_openclaw_jobs,
    parse_systemd_timers,
    parse_systemd_unit,
    DiscoveryResult,
)


# ── Schema validation ──

class TestSchemaValidation:
    def test_agents_schema_loads(self):
        registry = AgentRegistry.from_yaml("schema/agents.schema.yaml")
        assert "openclaw" in registry.agents
        assert "systemd" in registry.agents
        assert "n8n" in registry.agents
        assert registry.agents["openclaw"].type == AgentType.AUTONOMOUS
        assert registry.agents["systemd"].platform == Platform.SYSTEMD

    def test_agents_roundtrip(self):
        registry = AgentRegistry.from_yaml("schema/agents.schema.yaml")
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            registry.to_yaml(f.name)
            loaded = AgentRegistry.from_yaml(f.name)
        assert set(loaded.agents.keys()) == set(registry.agents.keys())
        assert loaded.agents["openclaw"].type == AgentType.AUTONOMOUS

    def test_task_schema_loads(self):
        task = Task.from_yaml("schema/task.schema.yaml")
        assert task.id == "enrich-curators"
        assert task.owner == "openclaw"
        assert task.priority == Priority.CRITICAL
        assert task.execute.timeout == "600s"
        assert len(task.conflicts) == 1
        assert task.conflicts[0].agent == "n8n"

    def test_task_roundtrip(self):
        task = Task.from_yaml("schema/task.schema.yaml")
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            task.to_yaml(f.name)
            loaded = Task.from_yaml(f.name)
        assert loaded.id == task.id
        assert loaded.owner == task.owner

    def test_health_schema_loads(self):
        health = HealthContract.from_yaml("schema/health.schema.yaml")
        assert len(health.agent_checks) == 3
        assert len(health.task_checks) == 3
        assert health.escalation.warn == "telegram"
        assert health.escalation.critical == "claude_code"

    def test_invalid_agent_type_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"agents": {"bad": {"type": "invalid", "platform": "openclaw"}}}, f)
            f.flush()
            with pytest.raises(ValueError):
                AgentRegistry.from_yaml(f.name)


# ── systemd parser ──

class TestSystemdParser:
    MOCK_OUTPUT = """NEXT                        LEFT          LAST                        PASSED       UNIT                         ACTIVATES
Mon 2026-03-02 04:06:06 UTC 1min 44s left Sun 2026-03-02 04:04:06 UTC 17s ago      music-promo-queue.timer      music-promo-queue.service
Mon 2026-03-02 04:10:00 UTC 5min left     Sun 2026-03-02 04:05:00 UTC 1min ago     music-promo-check-replies.timer music-promo-check-replies.service

2 timers listed.
Pass --all to see loaded but inactive timers, too.
"""

    def test_parse_timers(self):
        timers = parse_systemd_timers(self.MOCK_OUTPUT)
        assert len(timers) == 2
        assert timers[0].name == "music-promo-queue"
        assert timers[1].name == "music-promo-check-replies"
        assert all(t.active for t in timers)

    def test_parse_empty(self):
        assert parse_systemd_timers("") == []
        assert parse_systemd_timers("0 timers listed.") == []

    def test_parse_unit_exec_start(self):
        unit_output = """[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /root/scripts/enrich.py --limit 200
WorkingDirectory=/root/workspace
"""
        cmd = parse_systemd_unit(unit_output)
        assert "enrich.py" in cmd
        assert "--limit 200" in cmd

    def test_parse_unit_no_exec(self):
        assert parse_systemd_unit("[Timer]\nOnCalendar=*:0/2\n") == ""


# ── n8n parser ──

class TestN8nParser:
    MOCK_WORKFLOWS = [
        {
            "id": "abc123",
            "name": "Playlist Pitch Reply Checker",
            "active": True,
            "nodes": [
                {"type": "n8n-nodes-base.scheduleTrigger", "name": "Schedule"},
                {"type": "n8n-nodes-base.httpRequest", "name": "Fetch"},
            ],
        },
        {
            "id": "def456",
            "name": "AI Draft Reply",
            "active": True,
            "nodes": [
                {"type": "n8n-nodes-base.webhook", "name": "Webhook"},
            ],
        },
        {
            "id": "ghi789",
            "name": "Warmup Scheduler",
            "active": False,
            "nodes": [],
        },
    ]

    def test_parse_workflows(self):
        workflows = parse_n8n_workflows(self.MOCK_WORKFLOWS)
        assert len(workflows) == 3
        assert workflows[0].name == "Playlist Pitch Reply Checker"
        assert workflows[0].active is True
        assert workflows[0].trigger_type == "cron"
        assert workflows[1].trigger_type == "webhook"
        assert workflows[2].active is False

    def test_parse_empty(self):
        assert parse_n8n_workflows([]) == []


# ── OpenClaw parser ──

class TestOpenClawParser:
    MOCK_JOBS = [
        {
            "name": "playlist-outreach-daily",
            "enabled": True,
            "schedule": {"kind": "daily", "time": "16:30"},
            "payload": {"kind": "agentTurn", "message": "Run outreach batch"},
        },
        {
            "name": "music-promo-enrichment",
            "enabled": True,
            "schedule": {"kind": "every", "everyMs": 21600000},
            "payload": {"kind": "agentTurn", "message": "Run enrichment"},
        },
        {
            "name": "check-replies",
            "enabled": False,
            "schedule": {"kind": "every", "everyMs": 3600000},
            "payload": {},
        },
    ]

    def test_parse_jobs(self):
        jobs = parse_openclaw_jobs(self.MOCK_JOBS)
        assert len(jobs) == 3
        assert jobs[0].name == "playlist-outreach-daily"
        assert jobs[0].enabled is True
        assert jobs[0].schedule_kind == "daily"
        assert jobs[1].schedule_value == "6h"
        assert jobs[2].enabled is False
        assert jobs[2].schedule_value == "1h"

    def test_parse_empty(self):
        assert parse_openclaw_jobs([]) == []


# ── Conflict detection ──

class TestConflictDetection:
    def test_detects_name_similarity(self):
        timers = [DiscoveredTimer(name="music-promo-check-replies", schedule="5m", active=True)]
        workflows = [DiscoveredWorkflow(id="1", name="Playlist Pitch Reply Checker", active=True)]
        jobs = [DiscoveredJob(name="Check playlist pitch replies", enabled=True)]
        conflicts = detect_conflicts(timers, workflows, jobs)
        # Should detect at least one conflict — multiple owners for reply checking
        assert len(conflicts) >= 1
        # At least 2 platforms involved in some conflict
        max_owners = max(len(c.owners) for c in conflicts)
        assert max_owners >= 2

    def test_no_conflict_different_tasks(self):
        timers = [DiscoveredTimer(name="backup-database", schedule="1h", active=True)]
        workflows = [DiscoveredWorkflow(id="1", name="Send welcome email", active=True)]
        jobs = [DiscoveredJob(name="health-monitor", enabled=True)]
        conflicts = detect_conflicts(timers, workflows, jobs)
        assert len(conflicts) == 0

    def test_no_conflict_same_platform(self):
        timers = [
            DiscoveredTimer(name="check-replies-v1", schedule="5m", active=True),
            DiscoveredTimer(name="check-replies-v2", schedule="10m", active=True),
        ]
        conflicts = detect_conflicts(timers, [], [])
        assert len(conflicts) == 0  # Same platform, not a cross-platform conflict


# ── File generation ──

class TestFileGeneration:
    def test_generate_agents_yaml(self, tmp_path):
        result = DiscoveryResult(
            timers=[DiscoveredTimer(name="test", schedule="1m", active=True)],
            workflows=[DiscoveredWorkflow(id="1", name="test", active=True)],
            jobs=[DiscoveredJob(name="test", enabled=True)],
        )
        out = tmp_path / "agents.yaml"
        generate_agents_yaml(result, out)
        loaded = AgentRegistry.from_yaml(out)
        assert "systemd" in loaded.agents
        assert "n8n" in loaded.agents
        assert "openclaw" in loaded.agents

    def test_generate_task_stubs(self, tmp_path):
        result = DiscoveryResult(
            timers=[DiscoveredTimer(name="enrich-curators", schedule="6h", active=True, exec_start="python3 enrich.py")],
            jobs=[DiscoveredJob(name="outreach-daily", enabled=True, schedule_value="16:30")],
        )
        tasks_dir = tmp_path / "tasks"
        generate_task_stubs(result, tasks_dir)
        files = list(tasks_dir.glob("*.yaml"))
        assert len(files) == 2
        # Verify one loads
        task = Task.from_yaml(files[0])
        assert task.id

    def test_generate_health_yaml(self, tmp_path):
        result = DiscoveryResult(
            timers=[DiscoveredTimer(name="test-timer", schedule="1m", active=True)],
            workflows=[DiscoveredWorkflow(id="1", name="test-wf", active=True)],
        )
        out = tmp_path / "health.yaml"
        generate_health_yaml(result, out)
        health = HealthContract.from_yaml(out)
        assert len(health.agent_checks) >= 1
        assert health.escalation.warn == "telegram"
