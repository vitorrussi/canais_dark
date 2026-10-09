import json
from pathlib import Path

import pytest
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import Asset, User
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.schemas.scene import PromptIn
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, scene_prompts, scripts, videos
from canaisdark.services.errors import InvalidState


@pytest.fixture
def env(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    user = user_token[0]
    ch = channels.create_channel(
        session, ChannelCreate(name="Canal", visual_style_prompt="dark cinematic, 35mm grain.")
    )
    assert ch.id
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user)
    assert v.id
    return {"video_id": v.id, "channel_id": ch.id, "user": user}


def _approve(session: Session, env: dict[str, object], texts: list[str]):  # type: ignore[no-untyped-def]
    return scripts.save_user_script(
        session, env["video_id"], [SceneIn(text=t) for t in texts], env["user"], approve=True
    )  # type: ignore[arg-type]


def test_requires_approved_script(session: Session, env: dict[str, object]) -> None:
    with pytest.raises(InvalidState, match="não foi aprovado"):
        scene_prompts.save_prompts(
            session, env["video_id"], [PromptIn(scene_index=0, image_prompt="x")]
        )  # type: ignore[arg-type]


def test_compose_and_partial_save(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, ["Um", "Dois", "Três"])
    res = scene_prompts.save_prompts(
        session,
        env["video_id"],  # type: ignore[arg-type]
        [
            PromptIn(scene_index=0, image_prompt="an empty lighthouse at dusk.", vertical=True),
            PromptIn(
                scene_index=2,
                image_prompt="a torn diary",
                key_scene=True,
                video_prompt="slow push-in",
            ),
        ],
    )
    assert res.saved == [0, 2] and res.missing == [1]
    scenes = scene_prompts.approved_scenes(session, env["video_id"])  # type: ignore[arg-type]
    ch = channels.get_channel(session, env["channel_id"])  # type: ignore[arg-type]
    assert (
        scene_prompts.compose_prompt(ch, scenes[0])
        == "dark cinematic, 35mm grain, an empty lighthouse at dusk, wide 16:9 landscape composition"
    )
    assert scene_prompts.compose_prompt(ch, scenes[0], True).endswith(
        "vertical 9:16 portrait composition"
    )
    assert scene_prompts.compose_prompt(ch, scenes[1]) == ""
    assert (
        scenes[0].wants_vertical
        and scenes[2].is_key_scene
        and scenes[2].video_prompt == "slow push-in"
    )
    channels.update_channel(session, env["channel_id"], ChannelUpdate(visual_style_prompt=""))  # type: ignore[arg-type]
    session.refresh(ch)
    assert scene_prompts.compose_prompt(ch, scenes[0]).startswith(
        "an empty lighthouse"
    )  # sem prefixo: sem vírgula sobrando
    assert scene_prompts.prompt_coverage(scenes) == (2, 3)


def test_invalid_index_is_explained(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, ["Um", "Dois"])
    with pytest.raises(InvalidState, match=r"índices 0 a 1"):
        scene_prompts.save_prompts(
            session, env["video_id"], [PromptIn(scene_index=5, image_prompt="x")]
        )  # type: ignore[arg-type]


def test_carry_over_on_new_approved_version(session: Session, env: dict[str, object]) -> None:
    v1 = _approve(session, env, ["Cena igual", "Cena velha", "Outra igual"])
    scene_prompts.save_prompts(
        session,
        env["video_id"],
        [
            PromptIn(scene_index=0, image_prompt="p0", vertical=True),
            PromptIn(scene_index=1, image_prompt="p1"),
            PromptIn(scene_index=2, image_prompt="p2"),
        ],
    )  # type: ignore[arg-type]
    old_scenes = scripts.scenes_of(session, v1)
    for sc in old_scenes:
        session.add(
            Asset(
                video_id=env["video_id"],
                scene_id=sc.id,
                type="image",
                path=f"x{sc.index}.png",
                meta={"variant": "main"},
            )
        )  # type: ignore[arg-type]
    session.commit()
    v2 = _approve(session, env, ["Cena igual", "Cena NOVA", "Outra igual"])
    new_scenes = scripts.scenes_of(session, v2)
    assert [s.image_prompt for s in new_scenes] == ["p0", "", "p2"]
    assert new_scenes[0].wants_vertical is True
    from sqlmodel import select

    owners = {a.path: a.scene_id for a in session.exec(select(Asset))}
    assert owners["x0.png"] == new_scenes[0].id and owners["x2.png"] == new_scenes[2].id
    assert (
        owners["x1.png"] == old_scenes[1].id
    )  # a cena alterada não herda: o asset fica na versão antiga


def test_package_formats(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, ["Um", "Dois"])
    scene_prompts.save_prompts(
        session, env["video_id"], [PromptIn(scene_index=0, image_prompt="a door", vertical=True)]
    )  # type: ignore[arg-type]
    name, mime, txt = scene_prompts.package(session, env["video_id"], "txt")  # type: ignore[arg-type]
    assert (
        name.endswith("-prompts.txt")
        and "[Cena 1] dark cinematic, 35mm grain, a door" in txt
        and "vertical 9:16" in txt
        and "(sem prompt ainda)" in txt
    )
    assert "## Cena 1" in scene_prompts.package(session, env["video_id"], "md")[2]  # type: ignore[arg-type]
    data = json.loads(scene_prompts.package(session, env["video_id"], "json")[2])  # type: ignore[arg-type]
    assert data[0]["image_prompt_vertical"] and data[1]["image_prompt_full"] == ""
    with pytest.raises(InvalidState):
        scene_prompts.package(session, env["video_id"], "pdf")  # type: ignore[arg-type]


def test_request_prompts_creates_task_with_context(
    session: Session, env: dict[str, object]
) -> None:
    from canaisdark.services import task_context, tasks

    with pytest.raises(InvalidState):
        scene_prompts.request_prompts(session, env["video_id"], "", env["user"])  # type: ignore[arg-type]
    _approve(session, env, ["Cena um", "Cena dois"])
    task = scene_prompts.request_prompts(
        session, env["video_id"], "mais planos de detalhe", env["user"]
    )  # type: ignore[arg-type]
    assert task.type == "generate_prompts" and task.instructions == "mais planos de detalhe"
    with pytest.raises(Exception, match="aberta"):
        scene_prompts.request_prompts(session, env["video_id"], "", env["user"])  # type: ignore[arg-type]
    claimed = tasks.claim_next(session, env["user"], "generate_prompts")  # type: ignore[arg-type]
    assert claimed
    ctx = task_context.build_task_context(session, claimed)
    assert (
        [s["index"] for s in ctx["scenes"]] == [0, 1]
        and "INGLÊS" in ctx["how_to"]
        and ctx["channel"]["visual_style_prompt"].startswith("dark")
    )


def test_batch_prompts_sizes_and_filters(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, [f"Cena {i}" for i in range(1, 8)])
    items = [
        PromptIn(scene_index=i, image_prompt=f"scene {i + 1} view.", vertical=(i % 3 == 0))
        for i in range(6)
    ]  # a cena 7 fica sem prompt
    scene_prompts.save_prompts(session, env["video_id"], items)  # type: ignore[arg-type]
    data = scene_prompts.batch_prompts(session, env["video_id"], size=4)  # type: ignore[arg-type]
    assert data["skipped"] == [7] and data["total"] == 6
    batches = data["batches"]  # type: ignore[assignment]
    assert [(b["first"], b["last"], b["count"]) for b in batches] == [(1, 4, 4), (5, 6, 2)]
    first = batches[0]["text"]
    assert (
        first.startswith("Gere AGORA, nesta mesma resposta, as 4 imagens")
        and "todas de uma vez" in first
    )
    assert (
        "16:9 (paisagem/widescreen)" in first
        and "Não faça perguntas" in first
        and "UMA POR VEZ" not in first
    )
    assert (
        "Estilo visual para TODAS: dark cinematic, 35mm grain" in first
        and first.count("35mm grain") == 1
    )  # estilo só no cabeçalho
    assert (
        "Cena 01: scene 1 view" in first
        and "Cena 04: scene 4 view" in first
        and "Cena 05" not in first
    )
    everything = scene_prompts.batch_prompts(session, env["video_id"], size=0)  # type: ignore[arg-type]
    assert len(everything["batches"]) == 1 and everything["batches"][0]["count"] == 6  # type: ignore[index]
    vert = scene_prompts.batch_prompts(session, env["video_id"], size=10, vertical=True)  # type: ignore[arg-type]
    assert (
        vert["total"] == 2
        and "9:16 (vertical)" in vert["batches"][0]["text"]
        and "Cena 04" in vert["batches"][0]["text"]
    )  # type: ignore[index]
    from canaisdark.services import scene_assets

    assert scene_assets.coverage(
        session, scene_prompts.approved_scenes(session, env["video_id"])
    ) == (0, 9)  # type: ignore[arg-type]


def test_batch_prompts_per_reply_wording(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, [f"Cena {i}" for i in range(1, 9)])
    scene_prompts.save_prompts(
        session,
        env["video_id"],
        [PromptIn(scene_index=i, image_prompt=f"scene {i + 1}") for i in range(8)],
    )  # type: ignore[arg-type]
    data = scene_prompts.batch_prompts(session, env["video_id"], size=0, per_reply=3)  # type: ignore[arg-type]
    text = data["batches"][0]["text"]  # type: ignore[index]
    assert (
        "grupos de 3 por resposta" in text
        and "cenas 1 a 3" in text
        and 'escrever "continue"' in text
        and "próximas 3 cenas" in text
    )
    assert (
        "Cena 08: scene 8" in text
    )  # a lista completa vai na mensagem; o chatbot libera 3 por vez
    assert (
        "grupos de"
        not in scene_prompts.batch_prompts(session, env["video_id"], size=0, per_reply=0)[
            "batches"
        ][0]["text"]
    )  # type: ignore[index]
    big = scene_prompts.batch_prompts(session, env["video_id"], size=2, per_reply=3)["batches"][0][
        "text"
    ]  # type: ignore[index]
    assert "grupos de" not in big  # per_reply >= tamanho do lote: vira "todas de uma vez"


def test_set_all_vertical(session: Session, env: dict[str, object]) -> None:
    _approve(session, env, ["Um", "Dois", "Três"])
    scene_prompts.save_prompts(
        session,
        env["video_id"],  # type: ignore[arg-type]
        [PromptIn(scene_index=0, image_prompt="a", vertical=True)],
    )
    assert scene_prompts.set_all_vertical(session, env["video_id"], True) == 2  # type: ignore[arg-type]
    assert scene_prompts.set_all_vertical(session, env["video_id"], False) == 3  # type: ignore[arg-type]
    scenes = scene_prompts.approved_scenes(session, env["video_id"])  # type: ignore[arg-type]
    assert not any(s.wants_vertical for s in scenes)
