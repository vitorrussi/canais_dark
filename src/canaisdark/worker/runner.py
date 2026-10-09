"""Loop do worker: reserva um job, executa o handler e registra sucesso/falha."""

import logging
import signal
import time
import traceback
import uuid
from types import FrameType

from sqlmodel import Session

from canaisdark.db import get_engine
from canaisdark.integrations.elevenlabs import ElevenLabsError, default_client
from canaisdark.services import jobs
from canaisdark.services.errors import DomainError
from canaisdark.worker.handlers import HANDLERS, ClientFactory, JobCancelled

log = logging.getLogger("canaisdark.worker")


class Worker:
    def __init__(
        self, make_client: ClientFactory = default_client, poll_seconds: float = 2.0
    ) -> None:
        self.make_client = make_client
        self.poll_seconds = poll_seconds
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self._stop = False

    def run_once(self) -> bool:
        """Processa no máximo um job. Retorna True se havia trabalho."""
        with Session(get_engine()) as session:
            jobs.recover_stale(session)
            job = jobs.claim_next(session, self.worker_id)
            if job is None:
                return False
            assert job.id is not None
            job_id = job.id
            log.info("job %s (%s) iniciado", job_id, job.type)
            handler = HANDLERS.get(job.type)
            try:
                if handler is None:
                    raise DomainError(f"Tipo de job desconhecido: {job.type}")
                handler(session, job, self.make_client)
            except JobCancelled:
                jobs.append_log(session, job_id, "Cancelado pelo usuário.")
            except (ElevenLabsError, DomainError) as e:
                jobs.append_log(session, job_id, f"ERRO: {e}")
                jobs.fail(session, job_id, str(e))
            except Exception as e:  # noqa: BLE001 - nunca derrubar o worker por um job
                session.rollback()
                jobs.append_log(
                    session, job_id, "ERRO inesperado:\n" + traceback.format_exc()[-1500:]
                )
                jobs.fail(session, job_id, f"Erro inesperado: {e}")
            return True

    def stop(self, *_: object) -> None:
        self._stop = True

    def run_forever(self) -> None:
        def handler(signum: int, frame: FrameType | None) -> None:
            self.stop()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        log.info("worker %s aguardando jobs (poll %.1fs)", self.worker_id, self.poll_seconds)
        while not self._stop:
            if not self.run_once():
                time.sleep(self.poll_seconds)
        log.info("worker encerrado")
