"""A small in-process bound for synchronous file/render work on one API worker."""
from contextlib import contextmanager
from threading import BoundedSemaphore
from fastapi import HTTPException

_slots=BoundedSemaphore(2)


@contextmanager
def processing_slot():
    if not _slots.acquire(blocking=False):
        raise HTTPException(429,detail={"code":"processing_busy","message":"The processor is busy. Wait before retrying this bounded operation."})
    try:
        yield
    finally:
        _slots.release()
