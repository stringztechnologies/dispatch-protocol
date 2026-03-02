"""Dispatch Protocol — Data models for agents, tasks, and health contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


# ── Enums ──

class AgentType(str, Enum):
    AUTONOMOUS = "autonomous"
    DAEMON = "daemon"
    EVENT_DRIVEN = "event_driven"
    INTERACTIVE = "interactive"


class Platform(str, Enum):
    OPENCLAW = "openclaw"
    N8N = "n8n"
    SYSTEMD = "systemd"
    CLAUDE_CODE = "claude-code"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScheduleType(str, Enum):
    INTERVAL = "interval"
    CRON = "cron"
    WEBHOOK = "webhook"


# ── Agent Registry ──

@dataclass
class AgentConnection:
    method: str
    config_path: str | None = None
    api_url: str | None = None


@dataclass
class Agent:
    name: str
    type: AgentType
    platform: Platform
    capabilities: list[str] = field(default_factory=list)
    constraints: list[str | dict[str, Any]] = field(default_factory=list)
    schedule: str = "cron"
    connection: AgentConnection | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Agent:
        conn = None
        if "connection" in data:
            conn = AgentConnection(**data["connection"])
        return cls(
            name=name,
            type=AgentType(data["type"]),
            platform=Platform(data["platform"]),
            capabilities=data.get("capabilities", []),
            constraints=data.get("constraints", []),
            schedule=data.get("schedule", "cron"),
            connection=conn,
        )


@dataclass
class AgentRegistry:
    agents: dict[str, Agent] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentRegistry:
        with open(path) as f:
            raw = yaml.safe_load(f)
        agents = {}
        for name, data in raw.get("agents", {}).items():
            agents[name] = Agent.from_dict(name, data)
        return cls(agents=agents)

    def to_yaml(self, path: str | Path) -> None:
        out: dict[str, Any] = {"agents": {}}
        for name, agent in self.agents.items():
            entry: dict[str, Any] = {
                "type": agent.type.value,
                "platform": agent.platform.value,
                "capabilities": agent.capabilities,
                "constraints": agent.constraints,
                "schedule": agent.schedule,
            }
            if agent.connection:
                conn: dict[str, Any] = {"method": agent.connection.method}
                if agent.connection.config_path:
                    conn["config_path"] = agent.connection.config_path
                if agent.connection.api_url:
                    conn["api_url"] = agent.connection.api_url
                entry["connection"] = conn
            out["agents"][name] = entry
        with open(path, "w") as f:
            yaml.dump(out, f, default_flow_style=False, sort_keys=False)


# ── Task Contracts ──

@dataclass
class ExecuteBlock:
    command: str
    workdir: str = "."
    pre_run: str | None = None
    timeout: str = "300s"
    isolation: bool = True


@dataclass
class ScheduleBlock:
    type: ScheduleType
    every: str | None = None
    cron: str | None = None


@dataclass
class ReportConfig:
    channel: str = "telegram"
    template: str = ""


@dataclass
class SuccessBlock:
    metric: str = ""
    report: ReportConfig = field(default_factory=ReportConfig)


@dataclass
class EscalateConfig:
    to: str = "claude_code"
    context: str = ""


@dataclass
class FailureBlock:
    threshold: int = 3
    report: ReportConfig = field(default_factory=ReportConfig)
    escalate: EscalateConfig | None = None


@dataclass
class ConflictEntry:
    agent: str
    workflow_id: str | None = None
    resolution: str = "deactivated"
    reason: str = ""


@dataclass
class Task:
    id: str
    name: str
    owner: str
    priority: Priority = Priority.MEDIUM
    execute: ExecuteBlock = field(default_factory=lambda: ExecuteBlock(command="echo noop"))
    schedule: ScheduleBlock = field(default_factory=lambda: ScheduleBlock(type=ScheduleType.INTERVAL))
    success: SuccessBlock = field(default_factory=SuccessBlock)
    failure: FailureBlock = field(default_factory=FailureBlock)
    conflicts: list[ConflictEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        td = data.get("task", data)

        execute = ExecuteBlock(**td["execute"]) if "execute" in td else ExecuteBlock(command="echo noop")

        sched_data = td.get("schedule", {"type": "interval"})
        schedule = ScheduleBlock(
            type=ScheduleType(sched_data["type"]),
            every=sched_data.get("every"),
            cron=sched_data.get("cron"),
        )

        success = SuccessBlock()
        if "success" in td:
            sr = td["success"].get("report", {})
            success = SuccessBlock(
                metric=td["success"].get("metric", ""),
                report=ReportConfig(**sr) if sr else ReportConfig(),
            )

        failure = FailureBlock()
        if "failure" in td:
            fd = td["failure"]
            fr = fd.get("report", {})
            esc = None
            if "escalate" in fd:
                esc = EscalateConfig(**fd["escalate"])
            failure = FailureBlock(
                threshold=fd.get("threshold", 3),
                report=ReportConfig(**fr) if fr else ReportConfig(),
                escalate=esc,
            )

        conflicts = []
        for c in td.get("conflicts", []):
            conflicts.append(ConflictEntry(
                agent=c["agent"],
                workflow_id=c.get("workflow_id"),
                resolution=c.get("resolution", "deactivated"),
                reason=c.get("reason", ""),
            ))

        return cls(
            id=td["id"],
            name=td["name"],
            owner=td["owner"],
            priority=Priority(td.get("priority", "medium")),
            execute=execute,
            schedule=schedule,
            success=success,
            failure=failure,
            conflicts=conflicts,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> Task:
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))

    def to_yaml(self, path: str | Path) -> None:
        out: dict[str, Any] = {
            "task": {
                "id": self.id,
                "name": self.name,
                "owner": self.owner,
                "priority": self.priority.value,
                "execute": {
                    "workdir": self.execute.workdir,
                    "command": self.execute.command,
                    "timeout": self.execute.timeout,
                    "isolation": self.execute.isolation,
                },
                "schedule": {"type": self.schedule.type.value},
            }
        }
        if self.execute.pre_run:
            out["task"]["execute"]["pre_run"] = self.execute.pre_run
        if self.schedule.every:
            out["task"]["schedule"]["every"] = self.schedule.every
        if self.schedule.cron:
            out["task"]["schedule"]["cron"] = self.schedule.cron
        with open(path, "w") as f:
            yaml.dump(out, f, default_flow_style=False, sort_keys=False)


# ── Health Contracts ──

@dataclass
class AgentCheck:
    agent: str
    check: str
    expect: str


@dataclass
class TaskCheck:
    task: str
    check: str
    warn_below: int | None = None
    critical_below: int | None = None


@dataclass
class EscalationConfig:
    warn: str = "telegram"
    critical: str = "claude_code"


@dataclass
class HealthContract:
    agent_checks: list[AgentCheck] = field(default_factory=list)
    task_checks: list[TaskCheck] = field(default_factory=list)
    escalation: EscalationConfig = field(default_factory=EscalationConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> HealthContract:
        with open(path) as f:
            raw = yaml.safe_load(f)
        h = raw.get("health", {})
        agent_checks = [AgentCheck(**ac) for ac in h.get("agent_checks", [])]
        task_checks = [TaskCheck(**tc) for tc in h.get("task_checks", [])]
        esc = h.get("escalation", {})
        return cls(
            agent_checks=agent_checks,
            task_checks=task_checks,
            escalation=EscalationConfig(**esc) if esc else EscalationConfig(),
        )

    def to_yaml(self, path: str | Path) -> None:
        out: dict[str, Any] = {
            "health": {
                "agent_checks": [
                    {"agent": ac.agent, "check": ac.check, "expect": ac.expect}
                    for ac in self.agent_checks
                ],
                "task_checks": [],
                "escalation": {
                    "warn": self.escalation.warn,
                    "critical": self.escalation.critical,
                },
            }
        }
        for tc in self.task_checks:
            entry: dict[str, Any] = {"task": tc.task, "check": tc.check}
            if tc.warn_below is not None:
                entry["warn_below"] = tc.warn_below
            if tc.critical_below is not None:
                entry["critical_below"] = tc.critical_below
            out["health"]["task_checks"].append(entry)
        with open(path, "w") as f:
            yaml.dump(out, f, default_flow_style=False, sort_keys=False)
