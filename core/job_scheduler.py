"""
LADOCK Job Scheduler
Manages a queue of docking jobs with configurable parallel execution.
Emits status signals compatible with both Qt (via callback) and CLI.
"""

import os
import uuid
import threading
import datetime
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class JobStatus(Enum):
    QUEUED   = "queued"
    RUNNING  = "running"
    FINISHED = "finished"
    FAILED   = "failed"
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


class JobScheduler:
    """
    Thread-safe job queue.

    Usage:
        scheduler = JobScheduler(max_workers=2)
        scheduler.on_status_change = my_callback   # optional
        job_id = scheduler.submit(params)
        scheduler.start()
    """

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers
        self._jobs: Dict[str, DockingJob] = {}
        self._lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: Dict[str, Future] = {}

        # Callbacks — set these from the GUI
        self.on_status_change: Optional[Callable[[DockingJob], None]] = None
        self.on_log: Optional[Callable[[str, str], None]] = None  # (job_id, message)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def submit(self, name: str, parameters: dict) -> str:
        """Add a job to the queue and return its job_id."""
        job_id = uuid.uuid4().hex[:8]
        job = DockingJob(
            job_id=job_id,
            name=name,
            parameters=parameters,
            created_at=datetime.datetime.now().isoformat()
        )
        with self._lock:
            self._jobs[job_id] = job
        self._notify(job)
        return job_id

    def start(self):
        """Start processing queued jobs."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        with self._lock:
            pending = [j for j in self._jobs.values() if j.status == JobStatus.QUEUED]
        for job in pending:
            future = self._executor.submit(self._run_job, job.job_id)
            self._futures[job.job_id] = future

    def cancel(self, job_id: str):
        """Cancel a queued job (running jobs cannot be interrupted yet)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            self._notify(job)

    def get_job(self, job_id: str) -> Optional[DockingJob]:
        return self._jobs.get(job_id)

    def all_jobs(self) -> List[DockingJob]:
        return list(self._jobs.values())

    def shutdown(self, wait: bool = True):
        if self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _run_job(self, job_id: str):
        with self._lock:
            job = self._jobs[job_id]
            if job.status != JobStatus.QUEUED:
                return
            job.status = JobStatus.RUNNING
            job.started_at = datetime.datetime.now().isoformat()
        self._notify(job)

        try:
            from engine.docking_engine import run_docking

            def log_cb(msg):
                if self.on_log:
                    self.on_log(job_id, msg)

            run_docking(log_callback=log_cb, **job.parameters)

            with self._lock:
                job.status = JobStatus.FINISHED
                job.progress = 100
                job.finished_at = datetime.datetime.now().isoformat()

        except Exception as e:
            with self._lock:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.finished_at = datetime.datetime.now().isoformat()

        self._notify(job)

    def _notify(self, job: DockingJob):
        if self.on_status_change:
            self.on_status_change(job)
