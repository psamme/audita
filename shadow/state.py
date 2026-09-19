"""Serialize policy mutations in the local single-process server."""
from functools import wraps
from threading import RLock

LOCK = RLock()


def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return call
