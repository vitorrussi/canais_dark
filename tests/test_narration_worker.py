import json
from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import FakeElevenLabs
from canaisdark.models import AssetType, JobStatus, JobType, User
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import audio, channels, jobs, narration, scripts, videos
from canaisdark.services.errors import Conflict, InvalidState
from canaisdark.worker.runner import Worker

SCENES = [
    "Era uma noite escura em 1900.",
    "Três homens sumiram sem deixar rastro.",
    "Ninguém soube explicar o que houve.",
]


@pytest.fixture
def env(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "KEY")
    monkeypatch.setattr(settings, "elevenlabs_max_chars_per_job", 2500)
    monkeypatch.setattr(settings, "forced_alignment_enabled", True)
    user = user_token[0]
    ch = channels.create_channel(session, ChannelCreate(name="Canal", elevenlabs_voice_id="voz123"))
    assert ch.id
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user)
    assert v.id
    ver = scripts.save_user_script(
        session, v.id, [SceneIn(text=t) for t in SCENES], user, approve=True
    )
    return {
        "video_id": v.id,
        "channel_id": ch.id,
        "user": user,
        "version": ver,
        "fake": FakeElevenLabs(),
    }


def worker(env: dict[str, object]) -> Worker:
    fake = env["fake"]
    return Worker(make_client=lambda: fake, poll_seconds=0)  # type: ignore[arg-type,return-value]


def drain(w: Worker) -> int:
    n = 0
    while w.run_once():
        n += 1
    return n


def test_plan_guards(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    vid = env["video_id"]
    p = narration.plan(session, vid)  # type: ignore[arg-type]
    assert (
        p.can_start
        and p.chars == sum(len(t) for t in SCENES) + 2
        and p.credits_estimated == p.chars // 2 + (p.chars % 2)
    )
    assert len(p.chunks) == 1 and not p.partial and p.will_align
    t = narration.plan(session, vid, test_scenes=1)  # type: ignore[arg-type]
    assert t.partial and len(t.scene_texts) == 1 and t.chars == len(SCENES[0])
    monkeypatch.setattr(get_settings(), "elevenlabs_max_chars_per_job", 40)
    over = narration.plan(session, vid)  # type: ignore[arg-type]
    assert not over.can_start and "limite por geração é 40" in over.problems[0]
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    assert any(
        "ELEVENLABS_API_KEY" in x for x in narration.plan(session, vid, test_scenes=1).problems
    )  # type: ignore[arg-type]
    from canaisdark.integrations.elevenlabs import Subscription

    low = narration.plan(session, vid, test_scenes=1, balance=Subscription(used=9990, limit=10000))  # type: ignore[arg-type]
    assert any("Créditos insuficientes" in x for x in low.problems)


def test_plan_needs_approval_and_voice(
    session: Session, env: dict[str, object], user_token: tuple[User, str]
) -> None:
    ch_id = env["channel_id"]
    channels.update_channel(session, ch_id, ChannelUpdate(forced_alignment=False))  # type: ignore[arg-type]
    v2 = videos.create_video(session, ch_id, VideoCreate(topic="outro"), user_token[0])  # type: ignore[arg-type]
    p = narration.plan(session, v2.id or 0)
    assert any("não foi aprovado" in x for x in p.problems) and not p.will_align


def test_chunks_respect_scene_boundaries_and_split_long() -> None:
    chunks = narration.build_chunks(["a" * 30, "b" * 30, "c" * 30], 70)
    assert [c.scenes for c in chunks] == [[0, 1], [2]]
    long = "Frase um aqui. Frase dois aqui. Frase três aqui."
    parts = narration.build_chunks([long], 20)
    assert (
        all(len(c.text) <= 20 for c in parts)
        and len(parts) >= 3
        and all(c.scenes == [0] for c in parts)
    )
    assert narration.build_chunks([], 100) == []


def test_full_flow_forced_alignment(session: Session, env: dict[str, object]) -> None:
    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    job = narration.start(session, env["video_id"], env["user"])  # type: ignore[arg-type]
    assert drain(worker(env)) == 2  # tts + alignment encadeado
    session.expire_all()
    tts = jobs.get_job(session, job.id or 0)
    assert (
        tts.status == "done"
        and tts.result["align"] is True
        and "Alinhamento enfileirado" in tts.log
    )
    ali = jobs.latest_job(session, env["video_id"], JobType.ALIGNMENT)  # type: ignore[arg-type]
    assert ali and ali.status == "done" and ali.parent_job_id == job.id
    assert fake.alignment_calls == 1 and len(fake.tts_calls) == 1
    assert (
        fake.tts_calls[0]["voice_id"] == "voz123"
        and fake.tts_calls[0]["model_id"] == "eleven_flash_v2_5"
    )
    scenes = scripts.scenes_of(session, env["version"])  # type: ignore[arg-type]
    assert all(s.start_s is not None and s.end_s is not None for s in scenes)
    assert (
        scenes[0].start_s == 0
        and scenes[0].end_s <= scenes[1].start_s + 0.01
        and scenes[1].end_s <= scenes[2].start_s + 0.01
    )  # type: ignore[operator]
    audio_asset = narration.latest_asset(session, env["video_id"], AssetType.AUDIO)  # type: ignore[arg-type]
    assert (
        audio_asset and audio_asset.meta["kind"] == "narration" and not audio_asset.meta["partial"]
    )
    assert narration.asset_path(audio_asset).exists()
    doc = json.loads(
        narration.asset_path(
            narration.latest_asset(session, env["video_id"], AssetType.ALIGNMENT)
        ).read_text()
    )  # type: ignore[arg-type]
    assert doc["timing"] == "forced" and len(doc["scenes"]) == 3 and doc["words"]
    srt_asset = narration.latest_subtitle(session, env["video_id"], "srt")  # type: ignore[arg-type]
    assert srt_asset is not None
    srt = narration.asset_path(srt_asset).read_text()
    assert "-->" in srt
    assert "Créditos gastos neste TTS" in tts.log and tts.result["credits_used"] is not None


def test_alignment_disabled_estimates_timings(session: Session, env: dict[str, object]) -> None:
    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    narration.start(session, env["video_id"], env["user"], align=False)  # type: ignore[arg-type]
    assert drain(worker(env)) == 1  # só TTS: nenhum job de alinhamento
    assert (
        fake.alignment_calls == 0
        and jobs.latest_job(session, env["video_id"], JobType.ALIGNMENT) is None
    )  # type: ignore[arg-type]
    scenes = scripts.scenes_of(session, env["version"])  # type: ignore[arg-type]
    assert scenes[0].start_s == 0 and scenes[-1].end_s is not None and scenes[-1].end_s > 0
    doc = json.loads(
        narration.asset_path(
            narration.latest_asset(session, env["video_id"], AssetType.ALIGNMENT)
        ).read_text()
    )  # type: ignore[arg-type]
    assert doc["timing"] == "estimated" and doc["low_confidence"] == []


def test_global_switch_disables_alignment(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "forced_alignment_enabled", False)
    assert not narration.plan(session, env["video_id"], align=True).will_align  # type: ignore[arg-type]


def test_short_test_mode_is_partial_and_clears_other_scene_times(
    session: Session, env: dict[str, object]
) -> None:
    narration.start(session, env["video_id"], env["user"], test_scenes=1, align=False)  # type: ignore[arg-type]
    drain(worker(env))
    asset = narration.latest_asset(session, env["video_id"], AssetType.AUDIO)  # type: ignore[arg-type]
    assert asset and asset.meta["partial"] is True
    scenes = scripts.scenes_of(session, env["version"])  # type: ignore[arg-type]
    assert scenes[0].end_s and scenes[1].start_s is None and scenes[2].end_s is None
    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    assert fake.tts_calls[0]["text"] == SCENES[0]


def test_resume_reuses_paid_chunks(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "tts_chunk_chars", 60)  # força vários trechos
    monkeypatch.setattr(
        audio,
        "concat_mp3",
        lambda parts, out: out.write_bytes(b"".join(p.read_bytes() for p in parts)),
    )
    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    fake.fail_on_call = 2
    job = narration.start(session, env["video_id"], env["user"], align=False)  # type: ignore[arg-type]
    assert job.params["chunk_chars"] == 60
    drain(worker(env))
    session.expire_all()
    failed = jobs.get_job(session, job.id or 0)
    assert (
        failed.status == "failed" and "falha simulada" in failed.error and len(fake.tts_calls) == 2
    )
    fake.fail_on_call = None
    retry = jobs.retry(session, failed.id or 0, env["user"])  # type: ignore[arg-type]
    drain(worker(env))
    session.expire_all()
    ok = jobs.get_job(session, retry.id or 0)
    assert ok.status == "done" and "reaproveitado" in ok.log
    first_chunk_calls = [c for c in fake.tts_calls if c["text"] == fake.tts_calls[0]["text"]]
    assert len(first_chunk_calls) == 1  # o 1º trecho pago não foi gerado de novo


def test_tts_error_fails_with_message_and_keeps_worker_alive(
    session: Session, env: dict[str, object]
) -> None:
    from canaisdark.integrations.elevenlabs import ElevenLabsError

    class Bad(FakeElevenLabs):
        def text_to_speech(self, voice_id: str, text: str, **kw: object) -> bytes:
            raise ElevenLabsError("Voz não encontrada: confira o voice_id do canal.", 404)

    env["fake"] = Bad()
    job = narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    assert drain(worker(env)) == 1
    session.expire_all()
    j = jobs.get_job(session, job.id or 0)
    assert j.status == JobStatus.FAILED.value and "voice_id" in j.error and "ERRO" in j.log


def test_alignment_failure_keeps_audio_and_can_retry(
    session: Session, env: dict[str, object]
) -> None:
    from canaisdark.integrations.elevenlabs import ElevenLabsError

    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    fake.alignment_error = ElevenLabsError("Créditos do ElevenLabs insuficientes", 400)
    narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    drain(worker(env))
    ali = jobs.latest_job(session, env["video_id"], JobType.ALIGNMENT)  # type: ignore[arg-type]
    assert (
        ali
        and ali.status == "failed"
        and narration.latest_asset(session, env["video_id"], AssetType.AUDIO)
    )  # type: ignore[arg-type]
    fake.alignment_error = None
    retry = jobs.retry(session, ali.id or 0, env["user"])  # type: ignore[arg-type]
    drain(worker(env))
    session.expire_all()
    assert (
        jobs.get_job(session, retry.id or 0).status == "done" and len(fake.tts_calls) == 1
    )  # TTS não repetido


def test_cancelled_job_is_skipped_and_dedupe(session: Session, env: dict[str, object]) -> None:
    job = narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    with pytest.raises(Conflict):
        narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    jobs.cancel(session, job.id or 0)
    assert drain(worker(env)) == 0
    assert env["fake"].tts_calls == []  # type: ignore[attr-defined]


def test_start_refused_when_plan_has_problems(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "elevenlabs_max_chars_per_job", 10)
    with pytest.raises(InvalidState, match="limite por geração"):
        narration.start(session, env["video_id"], env["user"])  # type: ignore[arg-type]


def _states(session: Session, env: dict[str, object]) -> dict[str, str]:
    from canaisdark.services import video_status

    video = videos.get_video(session, env["video_id"])  # type: ignore[arg-type]
    return {s.key: s.state for s in video_status.get_stages(session, video)}


def test_stage_states_through_the_flow(
    session: Session, env: dict[str, object], user_token: tuple[User, str]
) -> None:
    assert _states(session, env)["narration"] == "not_started"
    narration.start(session, env["video_id"], env["user"])  # type: ignore[arg-type]
    assert _states(session, env)["narration"] == "pending"
    w = worker(env)
    w.run_once()
    st = _states(session, env)
    assert st["narration"] == "done" and st["alignment"] == "pending"
    w.run_once()
    st = _states(session, env)
    assert st["narration"] == "done" and st["alignment"] == "done"
    # novo roteiro aprovado → narração/alinhamento ficam desatualizados
    newer = scripts.save_user_script(
        session, env["video_id"], [SceneIn(text="Novo texto.")], user_token[0], approve=True
    )  # type: ignore[arg-type]
    assert newer.number == 2
    st = _states(session, env)
    assert st["narration"] == "stale" and st["alignment"] == "stale"


def test_stage_partial_skipped_failed(session: Session, env: dict[str, object]) -> None:
    narration.start(session, env["video_id"], env["user"], test_scenes=1, align=False)  # type: ignore[arg-type]
    drain(worker(env))
    st = _states(session, env)
    assert st["narration"] == "partial" and st["alignment"] == "skipped"


def test_stage_failed(session: Session, env: dict[str, object]) -> None:
    from canaisdark.integrations.elevenlabs import ElevenLabsError

    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    fake.fail_on_call = 1
    narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    drain(worker(env))
    assert _states(session, env)["narration"] == "failed"
    _ = ElevenLabsError


def test_undo_window_blocks_worker_until_elapsed(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from canaisdark.models.channel import utcnow

    monkeypatch.setattr(get_settings(), "narration_undo_seconds", 30)
    job = narration.start(session, env["video_id"], env["user"], test_scenes=1, align=False)  # type: ignore[arg-type]
    assert job.run_after is not None and job.status == "queued"
    assert drain(worker(env)) == 0 and env["fake"].tts_calls == []  # type: ignore[attr-defined]
    job.run_after = utcnow() - timedelta(seconds=1)  # a janela passou
    session.add(job)
    session.commit()
    assert drain(worker(env)) == 1 and len(env["fake"].tts_calls) == 1  # type: ignore[attr-defined]


def test_undo_during_window_costs_nothing(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "narration_undo_seconds", 30)
    job = narration.start(session, env["video_id"], env["user"], test_scenes=1)  # type: ignore[arg-type]
    jobs.cancel(session, job.id or 0)
    monkeypatch.setattr(get_settings(), "narration_undo_seconds", 0)
    assert drain(worker(env)) == 0 and env["fake"].tts_calls == []  # type: ignore[attr-defined]
    assert narration.latest_asset(session, env["video_id"], AssetType.AUDIO) is None  # type: ignore[arg-type]


def test_cancel_while_generating_stops_before_next_chunk(
    session: Session, env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "tts_chunk_chars", 40)
    monkeypatch.setattr(
        audio,
        "concat_mp3",
        lambda parts, out: out.write_bytes(b"".join(p.read_bytes() for p in parts)),
    )
    fake: FakeElevenLabs = env["fake"]  # type: ignore[assignment]
    job = narration.start(session, env["video_id"], env["user"], align=False)  # type: ignore[arg-type]
    original = fake.text_to_speech

    def cancel_after_first(voice_id: str, text: str, **kw: object) -> bytes:
        data = original(voice_id, text, **kw)
        jobs.cancel(
            session, job.id or 0
        )  # o usuário clica em Cancelar enquanto o 1º trecho termina
        return data

    fake.text_to_speech = cancel_after_first  # type: ignore[method-assign]
    drain(worker(env))
    session.expire_all()
    done = jobs.get_job(session, job.id or 0)
    assert done.status == "cancelled" and len(fake.tts_calls) == 1
    assert "reaproveitados" in done.log
    assert narration.latest_asset(session, env["video_id"], AssetType.AUDIO) is None  # type: ignore[arg-type]
