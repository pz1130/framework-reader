"""导入的后台任务与进度页。见 2026-08-25 AI 导入设计 §5.3

一份 200 页的制度要分几块、跑几分钟。同步跑完再返回，浏览器那边就是一个
转圈的白页——人不知道是在跑还是挂了，刷新一下还会再点一次、再花一次钱。

**预检仍然同步**：额度不够、没配 key、权限不对是立刻能知道的，
扔进后台只会让人先看三秒转圈再看到「你没权限」。
"""
import threading

import pytest

from framework_reader.web import jobs


@pytest.fixture(autouse=True)
def _clean():
    jobs.reset()
    yield
    jobs.reset()


def test_a_finished_job_carries_the_draft_it_made():
    done = threading.Event()

    def run(report):
        report(1, 1)
        return "draft-abc"

    job = jobs.start_outline("ACME-1", total=1, runner=run)
    job.wait(timeout=5)
    assert job.status == "done"
    assert job.draft_id == "draft-abc"
    _ = done


def test_progress_counts_the_chunks():
    def run(report):
        for n in (1, 2, 3):
            report(n, 3)
        return "d"

    job = jobs.start_outline("ACME-1", total=3, runner=run)
    job.wait(timeout=5)
    assert (job.done, job.total) == (3, 3)


def test_a_failure_becomes_a_sentence_not_a_dead_thread():
    """后台线程默默死掉，页面就永远停在「切分中」。"""
    def run(report):
        raise RuntimeError("模型端点变了")

    job = jobs.start_outline("ACME-1", total=1, runner=run)
    job.wait(timeout=5)
    assert job.status == "error"
    assert "模型端点变了" in job.error


def test_the_same_framework_twice_is_one_job():
    """刷新页面会再花一次钱，是这一页最贵的 bug。"""
    started = []
    release = threading.Event()

    def run(report):
        started.append(1)
        release.wait(timeout=5)
        return "d"

    first = jobs.start_outline("ACME-1", total=1, runner=run)
    second = jobs.start_outline("ACME-1", total=1, runner=run)
    assert first is second
    release.set()
    first.wait(timeout=5)
    assert len(started) == 1


def test_a_different_framework_gets_its_own_job():
    release = threading.Event()

    def run(report):
        release.wait(timeout=5)
        return "d"

    a = jobs.start_outline("ACME-1", total=1, runner=run)
    b = jobs.start_outline("ACME-2", total=1, runner=run)
    assert a is not b
    release.set()
    a.wait(timeout=5)
    b.wait(timeout=5)


def test_a_finished_job_no_longer_blocks_a_retry():
    """跑完了就该能再导一次——比如上一次切歪了，改完原文重来。"""
    jobs.start_outline("ACME-1", total=1, runner=lambda r: "d").wait(timeout=5)
    again = jobs.start_outline("ACME-1", total=1, runner=lambda r: "d2")
    again.wait(timeout=5)
    assert again.draft_id == "d2"


def test_an_outline_job_is_found_by_its_own_id():
    job = jobs.start_outline("ACME-1", total=1, runner=lambda r: "d")
    job.wait(timeout=5)
    assert jobs.get_outline(job.job_id) is job


def test_an_unknown_job_id_is_none_not_a_crash():
    assert jobs.get_outline("no-such-job") is None


# ---------- 进度页的文案与进度条 ----------

def test_the_bar_matches_the_progress():
    import re

    from framework_reader.web import views

    for done, expected in ((0, 0), (1, 33), (3, 100)):
        job = jobs.OutlineJob(job_id="x", framework_id="BIG", total=3, done=done)
        width = re.search(r'class="fill" style="width:(\d+)%"',
                          views.import_progress(job))
        assert int(width.group(1)) == expected


def test_the_wording_counts_what_is_finished_not_what_is_running():
    """`done` 数的是**已完成**的块。写成「第 0 块」读着像还没开始，
    而它已经在跑第一块了。"""
    from framework_reader.web import views

    job = jobs.OutlineJob(job_id="x", framework_id="BIG", total=3, done=0)
    page = views.import_progress(job)
    assert "Chunk 0" not in page
    assert "Finished 0 / 3 chunks" in page


def test_a_one_chunk_document_does_not_say_block_one_of_one_awkwardly():
    """小文档只有一块，进度页一闪而过。别让它说得像个大工程。"""
    from framework_reader.web import views

    job = jobs.OutlineJob(job_id="x", framework_id="S", total=1, done=0)
    assert "split into 1 chunk" not in views.import_progress(job)
