"""Run independent SerpApi jobs in parallel with a HARD deadline.

Why not `with ThreadPoolExecutor(...)`: leaving that block waits for every thread, so one slow Google call
(your log had a 15 s one) holds the whole request. Here we stop waiting at the deadline, report the slow job as an
error, and return what we have. The request id still follows each job into its thread (ctx_submit).

JOB_TIMEOUT (seconds, default 25) controls the deadline.
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from logging_config import ctx_submit

log = logging.getLogger("parallel")


def _timed(name, fn):
    def run():
        t = time.perf_counter()
        try:
            return fn()
        finally:
            log.debug("job=%s finished in %dms", name, (time.perf_counter() - t) * 1000)
    return run


def run_parallel(jobs, timeout=None, defaults=None, max_workers=8):
    """jobs: {name: callable}. Returns (results {name: value-or-default}, errors [str])."""
    timeout = float(timeout or os.getenv("JOB_TIMEOUT", "25"))
    defaults = defaults or {}
    if not jobs:
        return {}, []
    ex = ThreadPoolExecutor(max_workers=min(max_workers, len(jobs)), thread_name_prefix="job")
    futs = {k: ctx_submit(ex, _timed(k, fn)) for k, fn in jobs.items()}
    deadline = time.monotonic() + timeout
    res, errors = {}, []
    try:
        for k, f in futs.items():
            try:
                res[k] = f.result(timeout=max(0.05, deadline - time.monotonic()))
            except FutureTimeout:
                errors.append(f"{k}: timed out after {timeout:.0f}s")
                log.warning("job=%s TIMED OUT after %.0fs (continuing without it)", k, timeout)
                res[k] = defaults.get(k, [])
                f.cancel()
            except Exception as e:  # one failed call must not kill the request
                errors.append(f"{k}: {e}")
                log.warning("job=%s FAILED: %s", k, e)
                res[k] = defaults.get(k, [])
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return res, errors
