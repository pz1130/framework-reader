"""Drafting on the web is a long job: one model call per control, and dozens of controls take
minutes to run.

So it cannot run to completion inside the request — the browser would just show a spinning blank
page, the user cannot tell whether it is running or hung, and a refresh would mean clicking
again and paying again. The approach: run it on a background thread and let the page poll for
progress.

State is in-process and never persisted: restarting `fr serve` loses these records, and that is
fine — the drafting results live in the user database; all that is lost is "which control it got
to". Real persistence would mean introducing a job table and reclamation logic, which is not
worth the price for a local single-user workbench.
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Job:
    framework_id: str
    total: int
    status: str = "running"          # running / done / error
    written: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    error: str = ""

    @property
    def running(self) -> bool:
        return self.status == "running"


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def get(framework_id: str) -> Job | None:
    with _lock:
        return _jobs.get(framework_id)


def start(framework_id: str, total: int, runner: Callable[[str], object]) -> Job:
    """If the same framework is already running, return that job — refreshing the page must not
    mean paying again."""
    with _lock:
        existing = _jobs.get(framework_id)
        if existing is not None and existing.running:
            return existing
        job = Job(framework_id=framework_id, total=total)
        _jobs[framework_id] = job

    def work() -> None:
        try:
            report = runner(framework_id)
        except Exception as exc:                      # noqa: BLE001
            # Missing key, changed model endpoint, deleted framework — all of it becomes one
            # human-readable sentence here, instead of the background thread dying silently and
            # the page staying on "drafting" forever.
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
            return
        job.written = len(report.written)
        job.failed = [(f.control_id, f.reason) for f in report.failed]
        job.status = "done"

    threading.Thread(target=work, name=f"draft:{framework_id}", daemon=True).start()
    return job


def reset() -> None:
    """For tests: clear the in-process job table."""
    with _lock:
        _jobs.clear()
        _outlines.clear()
        _by_framework.clear()


def running_count() -> int:
    """Number of draft jobs still running. The spending concurrency gate takes its reading from
    this."""
    with _lock:
        return sum(1 for job in _jobs.values() if job.running)


@dataclass
class OutlineJob:
    """One document-splitting job. See the 2026-08-25 AI import design §5.3

    **Does not reuse the `Job` above.** Their fields mean different things — over there it is
    "how many controls were written", here it is "how many chunks finished"; cramming both into
    one dataclass makes both hard to read.

    In-process and not persisted, same reason as drafting: **the result (the draft) is in the
    user database**; all that is lost is "which chunk it got to".
    """

    job_id: str
    framework_id: str
    total: int
    status: str = "running"          # running / done / error
    done: int = 0
    draft_id: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        self._finished = threading.Event()

    @property
    def running(self) -> bool:
        return self.status == "running"

    def wait(self, timeout: float | None = None) -> bool:
        """For tests: wait for it to finish. In production the page refreshes itself."""
        return self._finished.wait(timeout)


_outlines: dict[str, OutlineJob] = {}
_by_framework: dict[str, str] = {}


def get_outline(job_id: str) -> OutlineJob | None:
    with _lock:
        return _outlines.get(job_id)


def start_outline(framework_id: str, total: int,
                  runner: Callable[[Callable[[int, int], None]], str]) -> OutlineJob:
    """Starts a splitting job. `runner` receives a `report(done, total)` callback.

    **If the same framework is already running, return that job** — refreshing the page would
    spend money again, the most expensive bug this page can have.
    """
    import uuid

    with _lock:
        existing = _outlines.get(_by_framework.get(framework_id, ""))
        if existing is not None and existing.running:
            return existing
        job = OutlineJob(job_id=uuid.uuid4().hex,
                         framework_id=framework_id, total=total)
        _outlines[job.job_id] = job
        _by_framework[framework_id] = job.job_id

    def report(done: int, whole: int) -> None:
        job.done, job.total = done, whole

    def work() -> None:
        try:
            job.draft_id = runner(report)
        except Exception as exc:                      # noqa: BLE001
            # A background thread dying silently leaves the page on "splitting" forever.
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
        else:
            job.status = "done"
        finally:
            job._finished.set()

    threading.Thread(target=work, name=f"outline:{framework_id}",
                     daemon=True).start()
    return job
