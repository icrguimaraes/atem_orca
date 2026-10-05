"""Worker de importação standalone (opcional): `python -m app.worker`.

Use quando separar o processamento em outro serviço Railway; defina RUN_IMPORT_WORKER=false no web.
"""

import logging
import signal

from app.config import get_settings
from app.db import SessionLocal
from app.imports.pipeline import ImportWorker
from app.services.storage import get_storage


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = ImportWorker(SessionLocal, get_storage(), get_settings().worker_poll_seconds)
    worker.daemon = False
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    worker.start()
    worker.join()


if __name__ == "__main__":
    main()
