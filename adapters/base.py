"""Base adapter interface for Dispatch Protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AdapterResult:
    success: bool
    action: str  # created, updated, deactivated, verified, failed
    target: str  # what was acted on
    details: str = ""


class BaseAdapter(ABC):
    """Interface all platform adapters must implement."""

    @abstractmethod
    def read_config(self) -> list[dict[str, Any]]:
        """Read current configuration from the platform."""
        ...

    @abstractmethod
    def create_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Create a new task/job on the platform."""
        ...

    @abstractmethod
    def update_task(self, task_id: str, config: dict[str, Any]) -> AdapterResult:
        """Update an existing task/job."""
        ...

    @abstractmethod
    def deactivate_task(self, task_id: str) -> AdapterResult:
        """Deactivate/disable a task without deleting."""
        ...

    @abstractmethod
    def verify_task(self, task_id: str) -> AdapterResult:
        """Verify a task is running correctly."""
        ...
