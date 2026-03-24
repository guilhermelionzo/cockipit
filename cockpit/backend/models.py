"""Data models for the Finance Routine Cockpit."""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid


def gen_id(prefix="id"):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


@dataclass
class Variable:
    name: str
    value: str
    description: str = ""
    id: str = field(default_factory=lambda: gen_id("var"))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "value": self.value,
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            id=d.get("id", gen_id("var")),
            name=d["name"],
            value=d["value"],
            description=d.get("description", ""),
            created_at=d.get("created_at", datetime.now().isoformat()),
        )


ROUTINE_TYPES = ["python", "excel", "vba", "shell", "api", "group"]
RUN_CONDITIONS = ["always", "on_success", "on_failure"]
STATUS_OPTIONS = ["pending", "running", "success", "failed", "skipped", "stopped"]


@dataclass
class Routine:
    name: str
    type: str
    id: str = field(default_factory=lambda: gen_id("r"))
    description: str = ""
    command: str = ""
    working_dir: str = ""
    parameters: str = ""
    cell_values: str = ""   # VBA/Excel only — "Sheet1!A1={Data_Ref}\nB2=texto"
    enabled: bool = True
    timeout: int = 300
    retry: int = 0
    parent_id: Optional[str] = None
    order: int = 0
    depends_on: List[str] = field(default_factory=list)
    run_condition: str = "always"
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_run: Optional[str] = None
    last_status: str = "pending"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "command": self.command,
            "working_dir": self.working_dir,
            "parameters": self.parameters,
            "cell_values": self.cell_values,
            "enabled": self.enabled,
            "timeout": self.timeout,
            "retry": self.retry,
            "parent_id": self.parent_id,
            "order": self.order,
            "depends_on": self.depends_on,
            "run_condition": self.run_condition,
            "tags": self.tags,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "last_status": self.last_status,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            id=d.get("id", gen_id("r")),
            name=d["name"],
            description=d.get("description", ""),
            type=d.get("type", "python"),
            command=d.get("command", ""),
            working_dir=d.get("working_dir", ""),
            parameters=d.get("parameters", ""),
            cell_values=d.get("cell_values", ""),
            enabled=d.get("enabled", True),
            timeout=d.get("timeout", 300),
            retry=d.get("retry", 0),
            parent_id=d.get("parent_id"),
            order=d.get("order", 0),
            depends_on=d.get("depends_on", []),
            run_condition=d.get("run_condition", "always"),
            tags=d.get("tags", []),
            created_at=d.get("created_at", datetime.now().isoformat()),
            last_run=d.get("last_run"),
            last_status=d.get("last_status", "pending"),
        )


@dataclass
class Schedule:
    name: str
    routine_id: str
    cron: str
    id: str = field(default_factory=lambda: gen_id("sch"))
    description: str = ""
    enabled: bool = True
    timezone: str = "America/Sao_Paulo"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_triggered: Optional[str] = None
    next_run: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "routine_id": self.routine_id,
            "cron": self.cron,
            "description": self.description,
            "enabled": self.enabled,
            "timezone": self.timezone,
            "created_at": self.created_at,
            "last_triggered": self.last_triggered,
            "next_run": self.next_run,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            id=d.get("id", gen_id("sch")),
            name=d["name"],
            routine_id=d["routine_id"],
            cron=d["cron"],
            description=d.get("description", ""),
            enabled=d.get("enabled", True),
            timezone=d.get("timezone", "America/Sao_Paulo"),
            created_at=d.get("created_at", datetime.now().isoformat()),
            last_triggered=d.get("last_triggered"),
            next_run=d.get("next_run"),
        )


@dataclass
class LogEntry:
    routine_id: str
    routine_name: str
    message: str
    level: str = "INFO"
    id: str = field(default_factory=lambda: gen_id("log"))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    run_id: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "routine_id": self.routine_id,
            "routine_name": self.routine_name,
            "message": self.message,
            "level": self.level,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            id=d.get("id", gen_id("log")),
            routine_id=d["routine_id"],
            routine_name=d["routine_name"],
            message=d["message"],
            level=d.get("level", "INFO"),
            timestamp=d.get("timestamp", datetime.now().isoformat()),
            run_id=d.get("run_id", ""),
        )
