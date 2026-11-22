"""Central logging setup.

What you get
  * every line carries a request id:  14:02:11 INFO  [a1b2c3d4] checker: ...
    (same id on the API line, the SerpApi calls, the LLM call and the DB writes of ONE request,
     even though they run in worker threads; it is also returned in the X-Request-ID header)
  * secrets are redacted automatically (api_key=..., Bearer ..., sk-..., 64-hex SerpApi keys)
  * noisy libraries (urllib3, httpx) are silenced because urllib3 DEBUG prints api_key in URLs
  * optional rotating log file

Env
  LOG_LEVEL = INFO (default) | DEBUG (full SerpApi JSON + full LLM reply) | WARNING
  LOG_FILE  = path to also write a rotating file (5 x 2 MB), e.g. app.log
"""
import contextvars
import logging
import logging.handlers
import os
import re

request_id_var = contextvars.ContextVar("request_id", default="-")

_SECRETS = [
    (re.compile(r"(api_key=)[^&\s'\"]+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\b[a-f0-9]{64}\b"), "***hex64***"),
]


class RedactFilter(logging.Filter):
    """Masks secrets in every record that reaches a handler."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = msg
        for pat, rep in _SECRETS:
            red = pat.sub(rep, red)
        if red != msg:
            record.msg, record.args = red, ()
        return True


_FORMAT = "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s"
_configured = False


def ctx_submit(executor, fn, *args, **kwargs):
    """executor.submit that carries the request id into the worker thread."""
    return executor.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def setup():
    global _configured
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if _configured:
        logging.getLogger().setLevel(level)
        return
    _configured = True

    old_factory = logging.getLogRecordFactory()

    def factory(*a, **k):
        rec = old_factory(*a, **k)
        rec.request_id = request_id_var.get()
        return rec

    logging.setLogRecordFactory(factory)

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler()
        root.addHandler(h)
    log_file = os.getenv("LOG_FILE", "").strip()
    if log_file:
        root.addHandler(logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5))
    for h in root.handlers:
        h.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
        h.addFilter(RedactFilter())

    # urllib3 DEBUG prints full request URLs INCLUDING api_key=... -> never allow it
    for noisy in ("urllib3", "urllib3.connectionpool", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
