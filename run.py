"""Одновременный запуск backend (FastAPI/uvicorn) и frontend (Streamlit).

Запуск:
    python run.py

Останавливает оба процесса по Ctrl+C.
"""

from __future__ import annotations

import subprocess
import sys
import time

BACKEND_CMD = [
    sys.executable,
    "-m",
    "uvicorn",
    "backend.api:app",
    "--reload",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
]

FRONTEND_CMD = [
    sys.executable,
    "-m",
    "streamlit",
    "run",
    "app.py",
]


def main() -> None:
    """Запустить backend и frontend как дочерние процессы."""
    backend = subprocess.Popen(BACKEND_CMD)
    time.sleep(3)
    frontend = subprocess.Popen(FRONTEND_CMD)

    try:
        backend.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for process in (backend, frontend):
            if process.poll() is None:
                process.terminate()


if __name__ == "__main__":
    main()
