from dataclasses import dataclass, field
from typing import List


@dataclass
class AgentListing:
    agent_id: str
    name: str
    description: str
    capability_tags: List[str]
    cost_per_run: int
    reputation_score: float
    total_runs: int = 0
    successful_runs: int = 0


@dataclass
class TaskRequest:
    task_id: str
    capability: str
    input: dict
    caller: str
    budget: int
    version: int = 1


@dataclass
class TaskResult:
    task_id: str
    status: str
    output: dict
    execution_time_ms: int
    credits_used: int
    version: int = 1
