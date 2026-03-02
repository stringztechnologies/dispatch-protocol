"""OpenClaw adapter — read/write /root/.openclaw/cron/jobs.json."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from adapters.base import AdapterResult, BaseAdapter


class OpenClawAdapter(BaseAdapter):
    """Manages OpenClaw cron jobs via direct config file manipulation."""

    def __init__(
        self,
        jobs_path: str = "/root/.openclaw/cron/jobs.json",
        ssh_host: str | None = None,
        default_model: str = "anthropic/claude-sonnet-4-20250514",
        default_timeout: int = 600,
    ):
        self.jobs_path = Path(jobs_path)
        self.ssh_host = ssh_host
        self.default_model = default_model
        self.default_timeout = default_timeout

    def _run(self, cmd: str) -> str:
        if self.ssh_host:
            import paramiko
            parts = self.ssh_host.split("@")
            user, host = (parts[0], parts[1]) if len(parts) == 2 else ("root", parts[0])
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, username=user, timeout=10)
                _, stdout, _ = client.exec_command(cmd, timeout=30)
                return stdout.read().decode()
            finally:
                client.close()
        else:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return result.stdout

    def _read_jobs(self) -> list[dict[str, Any]]:
        content = self._run(f"cat {self.jobs_path} 2>/dev/null")
        if not content.strip():
            return []
        try:
            data = json.loads(content)
            return data if isinstance(data, list) else data.get("jobs", [])
        except json.JSONDecodeError:
            return []

    def _write_jobs(self, jobs: list[dict[str, Any]]) -> None:
        content = json.dumps(jobs, indent=2)
        if self.ssh_host:
            # Backup then write via SSH
            self._run(f"cp {self.jobs_path} {self.jobs_path}.bak 2>/dev/null")
            import paramiko
            parts = self.ssh_host.split("@")
            user, host = (parts[0], parts[1]) if len(parts) == 2 else ("root", parts[0])
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, username=user, timeout=10)
                sftp = client.open_sftp()
                with sftp.open(str(self.jobs_path), "w") as f:
                    f.write(content)
            finally:
                client.close()
        else:
            # Local: backup then write
            if self.jobs_path.exists():
                shutil.copy2(self.jobs_path, str(self.jobs_path) + ".bak")
            self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
            self.jobs_path.write_text(content)

    def read_config(self) -> list[dict[str, Any]]:
        return self._read_jobs()

    def create_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        jobs = self._read_jobs()

        # Check if already exists
        for j in jobs:
            if j.get("name") == task_id:
                return AdapterResult(
                    success=False,
                    action="failed",
                    target=task_id,
                    details=f"Job '{task_id}' already exists. Use update instead.",
                )

        # Build job entry
        schedule = config.get("schedule", {})
        sched_kind = schedule.get("type", "every")
        sched_config: dict[str, Any] = {"kind": sched_kind}

        if sched_kind == "every" or sched_kind == "interval":
            every = schedule.get("every", "6h")
            ms = self._parse_duration_to_ms(every)
            sched_config = {"kind": "every", "everyMs": ms}
        elif sched_kind == "cron":
            sched_config = {"kind": "cron", "cron": schedule.get("cron", "0 */6 * * *")}
        elif sched_kind == "daily":
            sched_config = {"kind": "daily", "time": schedule.get("time", "08:00")}

        message = config.get("message", f"Read ORCHESTRATOR.md first. Then execute task: {task_id}")
        timeout = config.get("timeout", self.default_timeout)
        model = config.get("model", self.default_model)

        job = {
            "name": task_id,
            "enabled": True,
            "schedule": sched_config,
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": message,
                "timeoutSeconds": timeout,
                "model": model,
            },
            "delivery": {"mode": "none"},
        }

        jobs.append(job)
        self._write_jobs(jobs)

        return AdapterResult(
            success=True,
            action="created",
            target=task_id,
            details=f"Created OpenClaw job: {task_id} ({sched_config})",
        )

    def update_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        jobs = self._read_jobs()

        found = False
        for j in jobs:
            if j.get("name") == task_id:
                found = True
                # Update fields from config
                if "enabled" in config:
                    j["enabled"] = config["enabled"]
                if "schedule" in config:
                    schedule = config["schedule"]
                    sched_kind = schedule.get("type", "every")
                    if sched_kind in ("every", "interval"):
                        every = schedule.get("every", "6h")
                        j["schedule"] = {"kind": "every", "everyMs": self._parse_duration_to_ms(every)}
                    elif sched_kind == "cron":
                        j["schedule"] = {"kind": "cron", "cron": schedule.get("cron")}
                if "message" in config:
                    j.setdefault("payload", {})["message"] = config["message"]
                if "model" in config:
                    j.setdefault("payload", {})["model"] = config["model"]
                break

        if not found:
            return AdapterResult(success=False, action="failed", target=task_id, details="Job not found")

        self._write_jobs(jobs)
        return AdapterResult(success=True, action="updated", target=task_id, details=f"Updated OpenClaw job: {task_id}")

    def deactivate_task(self, task_id: str) -> AdapterResult:
        jobs = self._read_jobs()
        for j in jobs:
            if j.get("name") == task_id:
                j["enabled"] = False
                self._write_jobs(jobs)
                return AdapterResult(success=True, action="deactivated", target=task_id, details=f"Disabled OpenClaw job: {task_id}")
        return AdapterResult(success=False, action="failed", target=task_id, details="Job not found")

    def verify_task(self, task_id: str) -> AdapterResult:
        jobs = self._read_jobs()
        for j in jobs:
            if j.get("name") == task_id:
                enabled = j.get("enabled", False)
                status = "enabled" if enabled else "disabled"
                return AdapterResult(
                    success=enabled,
                    action="verified",
                    target=task_id,
                    details=f"OpenClaw job '{task_id}' is {status}",
                )
        return AdapterResult(success=False, action="failed", target=task_id, details="Job not found")

    @staticmethod
    def _parse_duration_to_ms(duration: str) -> int:
        duration = duration.strip().lower()
        if duration.endswith("ms"):
            return int(duration[:-2])
        if duration.endswith("h"):
            return int(float(duration[:-1]) * 3600000)
        elif duration.endswith("m"):
            return int(float(duration[:-1]) * 60000)
        elif duration.endswith("s"):
            return int(float(duration[:-1]) * 1000)
        else:
            try:
                return int(duration) * 1000  # assume seconds
            except ValueError:
                return 21600000  # default 6h
