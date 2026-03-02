"""systemd adapter — manage timers/services via systemctl."""

from __future__ import annotations

import subprocess
from typing import Any

from adapters.base import AdapterResult, BaseAdapter


class SystemdAdapter(BaseAdapter):
    """Manages systemd timers and services."""

    def __init__(self, ssh_host: str | None = None):
        self.ssh_host = ssh_host

    def _run(self, cmd: str) -> tuple[str, int]:
        if self.ssh_host:
            import paramiko
            parts = self.ssh_host.split("@")
            user, host = (parts[0], parts[1]) if len(parts) == 2 else ("root", parts[0])
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, username=user, timeout=10)
                _, stdout, _ = client.exec_command(cmd, timeout=30)
                output = stdout.read().decode()
                exit_code = stdout.channel.recv_exit_status()
                return output, exit_code
            finally:
                client.close()
        else:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return result.stdout + result.stderr, result.returncode

    def read_config(self) -> list[dict[str, Any]]:
        output, _ = self._run("systemctl list-timers --all --no-pager")
        timers = []
        for line in output.strip().split("\n"):
            if ".timer" in line:
                for part in line.split():
                    if part.endswith(".timer"):
                        timers.append({"name": part, "type": "timer"})
        return timers

    def create_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Create a systemd timer + service unit.

        config expects:
            command: str — the ExecStart command
            schedule: str — OnCalendar or OnUnitActiveSec value
            workdir: str — WorkingDirectory (optional)
            description: str (optional)
        """
        command = config.get("command", "")
        if not command:
            return AdapterResult(success=False, action="failed", target=task_id, details="No command specified")

        schedule = config.get("schedule", "*:0/5")  # default every 5 min
        workdir = config.get("workdir", "")
        description = config.get("description", f"Dispatch Protocol task: {task_id}")

        # Determine schedule type
        if any(c in schedule for c in ("*", "-", ":")):
            timer_directive = f"OnCalendar={schedule}"
        else:
            timer_directive = f"OnUnitActiveSec={schedule}"

        # Write service unit
        service_content = f"""[Unit]
Description={description}

[Service]
Type=oneshot
ExecStart={command}
"""
        if workdir:
            service_content += f"WorkingDirectory={workdir}\n"

        # Write timer unit
        timer_content = f"""[Unit]
Description={description} (timer)

[Timer]
{timer_directive}
Persistent=true

[Install]
WantedBy=timers.target
"""

        service_path = f"/etc/systemd/system/{task_id}.service"
        timer_path = f"/etc/systemd/system/{task_id}.timer"

        # Write files
        self._run(f"cat > {service_path} << 'UNIT'\n{service_content}UNIT")
        self._run(f"cat > {timer_path} << 'UNIT'\n{timer_content}UNIT")

        # Enable and start
        self._run("systemctl daemon-reload")
        output, code = self._run(f"systemctl enable --now {task_id}.timer")

        if code == 0:
            return AdapterResult(
                success=True, action="created", target=task_id,
                details=f"Created systemd timer: {task_id} ({timer_directive})",
            )
        return AdapterResult(
            success=False, action="failed", target=task_id,
            details=f"systemctl enable failed: {output[:200]}",
        )

    def update_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Update = recreate the unit files and reload."""
        # Stop first
        self._run(f"systemctl stop {task_id}.timer 2>/dev/null")
        result = self.create_task(task_id, config)
        if result.success:
            result.action = "updated"
            result.details = result.details.replace("Created", "Updated")
        return result

    def deactivate_task(self, task_id: str) -> AdapterResult:
        output, code = self._run(f"systemctl disable --now {task_id}.timer 2>&1")
        if code == 0:
            return AdapterResult(success=True, action="deactivated", target=task_id, details=f"Stopped and disabled {task_id}.timer")
        return AdapterResult(success=False, action="failed", target=task_id, details=f"Failed: {output[:200]}")

    def verify_task(self, task_id: str) -> AdapterResult:
        output, code = self._run(f"systemctl is-active {task_id}.timer 2>&1")
        status = output.strip()
        is_active = status == "active"
        return AdapterResult(
            success=is_active,
            action="verified",
            target=task_id,
            details=f"systemd timer '{task_id}' is {status}",
        )
