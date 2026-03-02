"""n8n adapter — manage workflows via REST API."""

from __future__ import annotations

from typing import Any

import httpx

from adapters.base import AdapterResult, BaseAdapter


class N8nAdapter(BaseAdapter):
    """Manages n8n workflows via the n8n REST API."""

    def __init__(
        self,
        api_url: str = "http://localhost:5678",
        api_key: str | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self.headers["X-N8N-API-KEY"] = api_key

    def _get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.api_url}{path}", headers=self.headers, timeout=15)

    def _post(self, path: str, data: dict | None = None) -> httpx.Response:
        return httpx.post(f"{self.api_url}{path}", headers=self.headers, json=data, timeout=15)

    def _put(self, path: str, data: dict | None = None) -> httpx.Response:
        return httpx.put(f"{self.api_url}{path}", headers=self.headers, json=data, timeout=15)

    def read_config(self) -> list[dict[str, Any]]:
        try:
            resp = self._get("/api/v1/workflows")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", data) if isinstance(data, dict) else data
        except Exception:
            pass
        return []

    def create_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Create a new n8n workflow. Config should include 'nodes' and 'connections'."""
        try:
            payload = {
                "name": config.get("name", task_id),
                "nodes": config.get("nodes", []),
                "connections": config.get("connections", {}),
                "settings": config.get("settings", {"executionOrder": "v1"}),
            }
            resp = self._post("/api/v1/workflows", payload)
            if resp.status_code in (200, 201):
                wf = resp.json()
                wf_id = wf.get("id", "unknown")
                return AdapterResult(
                    success=True,
                    action="created",
                    target=task_id,
                    details=f"Created n8n workflow: {task_id} (id: {wf_id})",
                )
            return AdapterResult(
                success=False, action="failed", target=task_id,
                details=f"n8n API returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return AdapterResult(success=False, action="failed", target=task_id, details=str(e)[:200])

    def update_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Update an existing workflow. task_id should be the n8n workflow ID."""
        try:
            payload: dict[str, Any] = {}
            if "name" in config:
                payload["name"] = config["name"]
            if "nodes" in config:
                payload["nodes"] = config["nodes"]
            if "connections" in config:
                payload["connections"] = config["connections"]
            if "settings" in config:
                payload["settings"] = config["settings"]

            resp = self._put(f"/api/v1/workflows/{task_id}", payload)
            if resp.status_code == 200:
                return AdapterResult(success=True, action="updated", target=task_id, details=f"Updated n8n workflow {task_id}")
            return AdapterResult(
                success=False, action="failed", target=task_id,
                details=f"n8n API returned {resp.status_code}",
            )
        except Exception as e:
            return AdapterResult(success=False, action="failed", target=task_id, details=str(e)[:200])

    def deactivate_task(self, task_id: str) -> AdapterResult:
        """Deactivate a workflow by ID."""
        try:
            resp = self._post(f"/api/v1/workflows/{task_id}/deactivate")
            if resp.status_code == 200:
                return AdapterResult(success=True, action="deactivated", target=task_id, details=f"Deactivated n8n workflow {task_id}")
            return AdapterResult(
                success=False, action="failed", target=task_id,
                details=f"n8n API returned {resp.status_code}",
            )
        except Exception as e:
            return AdapterResult(success=False, action="failed", target=task_id, details=str(e)[:200])

    def activate_task(self, task_id: str) -> AdapterResult:
        """Activate a workflow by ID."""
        try:
            resp = self._post(f"/api/v1/workflows/{task_id}/activate")
            if resp.status_code == 200:
                return AdapterResult(success=True, action="activated", target=task_id, details=f"Activated n8n workflow {task_id}")
            return AdapterResult(success=False, action="failed", target=task_id, details=f"n8n returned {resp.status_code}")
        except Exception as e:
            return AdapterResult(success=False, action="failed", target=task_id, details=str(e)[:200])

    def verify_task(self, task_id: str) -> AdapterResult:
        """Check if a workflow exists and its activation status."""
        try:
            resp = self._get(f"/api/v1/workflows/{task_id}")
            if resp.status_code == 200:
                wf = resp.json()
                active = wf.get("active", False)
                name = wf.get("name", task_id)
                return AdapterResult(
                    success=active,
                    action="verified",
                    target=task_id,
                    details=f"n8n workflow '{name}' is {'active' if active else 'inactive'}",
                )
            return AdapterResult(success=False, action="failed", target=task_id, details=f"Workflow {task_id} not found")
        except Exception as e:
            return AdapterResult(success=False, action="failed", target=task_id, details=str(e)[:200])

    def get_recent_executions(self, workflow_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Get recent execution history for a workflow."""
        try:
            resp = self._get(f"/api/v1/executions?workflowId={workflow_id}&limit={limit}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", data) if isinstance(data, dict) else data
        except Exception:
            pass
        return []
