"""Tests for Phase 3 — platform adapters + deployer."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml
import pytest

from adapters.openclaw import OpenClawAdapter
from adapters.n8n import N8nAdapter
from adapters.systemd import SystemdAdapter
from adapters.base import AdapterResult


# ── OpenClaw Adapter ──

class TestOpenClawAdapter:
    def _make_adapter(self, tmp_path, jobs=None):
        jobs_path = tmp_path / "jobs.json"
        jobs_path.write_text(json.dumps(jobs or []))
        return OpenClawAdapter(jobs_path=str(jobs_path))

    def test_read_empty(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [])
        assert adapter.read_config() == []

    def test_read_jobs(self, tmp_path):
        jobs = [{"name": "test-job", "enabled": True, "schedule": {"kind": "every", "everyMs": 3600000}}]
        adapter = self._make_adapter(tmp_path, jobs)
        config = adapter.read_config()
        assert len(config) == 1
        assert config[0]["name"] == "test-job"

    def test_create_task(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [])
        result = adapter.create_task("new-job", {
            "schedule": {"type": "interval", "every": "6h"},
            "message": "Run enrichment",
        })
        assert result.success
        assert result.action == "created"
        # Verify it was written
        config = adapter.read_config()
        assert len(config) == 1
        assert config[0]["name"] == "new-job"
        assert config[0]["schedule"]["everyMs"] == 21600000

    def test_create_duplicate_fails(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "existing", "enabled": True}])
        result = adapter.create_task("existing", {})
        assert not result.success
        assert "already exists" in result.details

    def test_update_task(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "test", "enabled": True, "payload": {}}])
        result = adapter.update_task("test", {"enabled": False})
        assert result.success
        config = adapter.read_config()
        assert config[0]["enabled"] is False

    def test_deactivate_task(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "test", "enabled": True}])
        result = adapter.deactivate_task("test")
        assert result.success
        config = adapter.read_config()
        assert config[0]["enabled"] is False

    def test_deactivate_missing(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [])
        result = adapter.deactivate_task("ghost")
        assert not result.success

    def test_verify_active(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "test", "enabled": True}])
        result = adapter.verify_task("test")
        assert result.success
        assert "enabled" in result.details

    def test_verify_inactive(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "test", "enabled": False}])
        result = adapter.verify_task("test")
        assert not result.success

    def test_backup_on_write(self, tmp_path):
        adapter = self._make_adapter(tmp_path, [{"name": "original"}])
        adapter.create_task("new-job", {"schedule": {"type": "interval", "every": "1h"}})
        backup = Path(str(adapter.jobs_path) + ".bak")
        assert backup.exists()
        backup_data = json.loads(backup.read_text())
        assert len(backup_data) == 1
        assert backup_data[0]["name"] == "original"

    def test_parse_durations(self):
        assert OpenClawAdapter._parse_duration_to_ms("6h") == 21600000
        assert OpenClawAdapter._parse_duration_to_ms("30m") == 1800000
        assert OpenClawAdapter._parse_duration_to_ms("120s") == 120000
        assert OpenClawAdapter._parse_duration_to_ms("5000ms") == 5000


# ── n8n Adapter ──

class TestN8nAdapter:
    def test_read_config_success(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "1", "name": "test", "active": True}]}
        with patch("httpx.get", return_value=mock_resp):
            config = adapter.read_config()
        assert len(config) == 1

    def test_read_config_failure(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        with patch("httpx.get", side_effect=Exception("connection refused")):
            config = adapter.read_config()
        assert config == []

    def test_deactivate_success(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp):
            result = adapter.deactivate_task("wf123")
        assert result.success
        assert result.action == "deactivated"

    def test_deactivate_failure(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.post", return_value=mock_resp):
            result = adapter.deactivate_task("wf123")
        assert not result.success

    def test_verify_active(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "1", "name": "test", "active": True}
        with patch("httpx.get", return_value=mock_resp):
            result = adapter.verify_task("1")
        assert result.success
        assert "active" in result.details

    def test_verify_inactive(self):
        adapter = N8nAdapter(api_url="http://fake:5678")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "1", "name": "test", "active": False}
        with patch("httpx.get", return_value=mock_resp):
            result = adapter.verify_task("1")
        assert not result.success


# ── systemd Adapter ──

class TestSystemdAdapter:
    def test_read_config(self):
        adapter = SystemdAdapter()
        mock_output = "NEXT LEFT LAST UNIT ACTIVATES\ntest.timer test.service\n1 timers listed."
        with patch.object(adapter, "_run", return_value=(mock_output, 0)):
            config = adapter.read_config()
        assert len(config) == 1
        assert config[0]["name"] == "test.timer"

    def test_verify_active(self):
        adapter = SystemdAdapter()
        with patch.object(adapter, "_run", return_value=("active", 0)):
            result = adapter.verify_task("test")
        assert result.success

    def test_verify_inactive(self):
        adapter = SystemdAdapter()
        with patch.object(adapter, "_run", return_value=("inactive", 3)):
            result = adapter.verify_task("test")
        assert not result.success

    def test_deactivate(self):
        adapter = SystemdAdapter()
        with patch.object(adapter, "_run", return_value=("", 0)):
            result = adapter.deactivate_task("test")
        assert result.success
        assert result.action == "deactivated"

    def test_create_task(self):
        adapter = SystemdAdapter()
        calls = []
        def mock_run(cmd):
            calls.append(cmd)
            return ("", 0)
        with patch.object(adapter, "_run", side_effect=mock_run):
            result = adapter.create_task("my-task", {
                "command": "/usr/bin/python3 script.py",
                "schedule": "*:0/5",
                "workdir": "/root/workspace",
            })
        assert result.success
        assert "daemon-reload" in str(calls)
        assert any("enable" in c for c in calls)


# ── Deployer ──

class TestDeployer:
    def test_dry_run(self, tmp_path):
        from src.deployer import deploy as run_deploy

        # Create minimal plan
        plan = {
            "plan": {
                "proposals": [
                    {
                        "task_id": "test-task",
                        "task_name": "Test Task",
                        "proposed_owner": "openclaw",
                        "current_owners": [{"platform": "openclaw", "name": "test-task"}],
                        "change": "create",
                        "actions": [],
                    }
                ],
                "summary": "1 task",
            }
        }
        plan_path = tmp_path / "plan.yaml"
        with open(plan_path, "w") as f:
            yaml.dump(plan, f)

        # Create minimal agents/tasks/health
        from src.models import AgentRegistry, Agent, AgentType, Platform, AgentConnection, HealthContract
        registry = AgentRegistry(agents={
            "openclaw": Agent(name="openclaw", type=AgentType.AUTONOMOUS, platform=Platform.OPENCLAW, connection=AgentConnection(method="ssh+config")),
        })
        registry.to_yaml(tmp_path / "agents.yaml")

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        health = HealthContract()
        health.to_yaml(tmp_path / "health.yaml")

        results = run_deploy(
            plan_path=plan_path,
            agents_path=tmp_path / "agents.yaml",
            tasks_dir=tasks_dir,
            health_path=tmp_path / "health.yaml",
            orchestrator_output=tmp_path / "ORCHESTRATOR.md",
            openclaw_jobs_path=str(tmp_path / "jobs.json"),
            dry_run=True,
        )

        assert any("dry-run" in r.action for r in results)
        assert (tmp_path / "ORCHESTRATOR.md").exists()
