"""SwingLens application package."""

import os

from app.asyncio_compat import ensure_event_loop

for _thread_variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_variable] = "1"

ensure_event_loop()
