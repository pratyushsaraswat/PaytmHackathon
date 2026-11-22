"""One-time logging setup. LOG_LEVEL=DEBUG -> full SerpApi JSON + full OpenAI reply in logs."""
import logging
import os


def setup():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)