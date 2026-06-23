import logging
import os
import sys
from pathlib import Path

LOGS_DIR = Path(os.getenv("DATA_DIR", ".")) / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FORMAT = "%(asctime)s  [%(levelname)-8s]  %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str = "etsy_agent") -> logging.Logger:
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    fh = logging.FileHandler(LOGS_DIR / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    _loggers[name] = logger
    return logger


def log_action(agent: str, message: str, level: str = "info") -> None:
    logger = get_logger(agent)
    getattr(logger, level.lower(), logger.info)(message)
