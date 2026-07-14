"""
LADOCK Job Models
Defines DockingJob and JobStatus used by the GUI Job Manager and the docking
panels (Redocking / Lig Test), which run their own QThread workers.
"""

import datetime
from dataclasses import dataclass
from enum import Enum


class JobStatus(Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    FINISHED  = "finished"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class DockingJob:
    job_id: str
    name: str
    parameters: dict
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0          # 0-100
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result_csv: str = ""

    def elapsed(self) -> str:
        if not self.started_at:
            return "-"
        start = datetime.datetime.fromisoformat(self.started_at)
        if self.finished_at:
            end = datetime.datetime.fromisoformat(self.finished_at)
        else:
            end = datetime.datetime.now()
        delta = end - start
        m, s = divmod(int(delta.total_seconds()), 60)
        return f"{m}m {s}s"
