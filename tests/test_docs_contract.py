"""Coerência entre enums, handlers, instruções do Claude e migrações (pega esquecimentos)."""

from alembic.config import Config
from alembic.script import ScriptDirectory

from canaisdark.models import JobType, TaskType
from canaisdark.services import offline_tasks, task_context
from canaisdark.worker.handlers import HANDLERS


def test_every_job_type_has_a_worker_handler() -> None:
    missing = [j.value for j in JobType if j.value not in HANDLERS]
    assert not missing, f"jobs sem handler no worker: {missing}"


def test_every_task_type_has_instructions_for_claude() -> None:
    missing = [t.value for t in TaskType if t.value not in task_context.HOW_TO]
    assert not missing, f"tarefas sem HOW_TO: {missing}"
    assert all(len(task_context.HOW_TO[t.value]) > 80 for t in TaskType)


def test_offline_mode_covers_all_supported_types() -> None:
    for kind in offline_tasks.SUPPORTED:
        assert kind in offline_tasks.FORMATS and kind in offline_tasks.LABELS
    sandbox_only = {t.value for t in TaskType} - set(offline_tasks.SUPPORTED)
    assert sandbox_only <= {"edit_video", "make_thumbnail"}, "novo tipo fora do modo copiar/colar?"


def test_alembic_has_single_head_and_linear_history() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1, f"várias heads de migração: {script.get_heads()}"
    revisions = list(script.walk_revisions())
    assert len({r.revision for r in revisions}) == len(revisions)
    assert all(not isinstance(r.down_revision, tuple) for r in revisions), "migração com merge"
