"""网页上起草是个长活儿：一条控制一次模型调用，几十条要跑几分钟。

所以不能在请求里跑完再返回——浏览器那边就是一个转圈的白页，用户不知道
是在跑还是挂了，刷新一下还会再点一次、再花一次钱。做法是后台线程跑，
页面轮询进度。

进程内状态，不落盘：`fr serve` 重启这些记录就没了，这没关系——起草的结果
在用户库里，丢的只是「跑到第几条」。真要持久化就得引入任务表和回收逻辑，
本地单人工作台不值这个价。
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
    """同一个框架已经在跑就返回那一个——刷新页面不该再点一次钱。"""
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
            # 缺 key、模型端点变了、框架被删了——都在这里变成一句人话，
            # 而不是让后台线程默默死掉、页面永远停在「起草中」。
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
            return
        job.written = len(report.written)
        job.failed = [(f.control_id, f.reason) for f in report.failed]
        job.status = "done"

    threading.Thread(target=work, name=f"draft:{framework_id}", daemon=True).start()
    return job


def reset() -> None:
    """测试用：清掉进程内的任务表。"""
    with _lock:
        _jobs.clear()
        _outlines.clear()
        _by_framework.clear()


def running_count() -> int:
    """还在跑的起草任务数。花钱那道并发闸拿它当读数。"""
    with _lock:
        return sum(1 for job in _jobs.values() if job.running)


@dataclass
class OutlineJob:
    """一次文档切分。见 2026-08-25 AI 导入设计 §5.3

    **不复用上面那个 `Job`。** 两者的字段含义不一样——那边是「写了几条」，
    这边是「跑完几块」，挤在一个 dataclass 里两边都难读。

    进程内不落盘，理由和起草一样：**结果（草稿）在用户库里**，
    丢的只是「跑到第几块」。
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
        """测试用：等它跑完。生产上靠页面自己刷新。"""
        return self._finished.wait(timeout)


_outlines: dict[str, OutlineJob] = {}
_by_framework: dict[str, str] = {}


def get_outline(job_id: str) -> OutlineJob | None:
    with _lock:
        return _outlines.get(job_id)


def start_outline(framework_id: str, total: int,
                  runner: Callable[[Callable[[int, int], None]], str]) -> OutlineJob:
    """开一个切分任务。`runner` 收一个 `report(done, total)` 回调。

    **同一个框架已经在跑就返回那一个**——刷新页面会再花一次钱，
    是这一页最贵的 bug。
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
            # 后台线程默默死掉，页面就永远停在「切分中」。
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
        else:
            job.status = "done"
        finally:
            job._finished.set()

    threading.Thread(target=work, name=f"outline:{framework_id}",
                     daemon=True).start()
    return job
