import io
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import Asset, User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, scene_assets, scene_prompts, scripts, videos
from canaisdark.services.errors import InvalidState, NotFound


def png(w: int, h: int, color: str = "red", fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def env(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    user = user_token[0]
    ch = channels.create_channel(session, ChannelCreate(name="Canal"))
    assert ch.id
    v = videos.create_video(session, ch.id, VideoCreate(topic="t"), user)
    assert v.id
    scripts.save_user_script(
        session, v.id, [SceneIn(text=t) for t in ["A", "B", "C"]], user, approve=True
    )
    return {"vid": v.id, "root": tmp_path / "files"}


def test_detect_kind_by_content_not_extension() -> None:
    assert scene_assets.detect_kind(png(10, 10)) == ("image", "png")
    assert scene_assets.detect_kind(png(10, 10, fmt="JPEG")) == ("image", "jpg")
    assert scene_assets.detect_kind(png(10, 10, fmt="WEBP")) == ("image", "webp")
    assert scene_assets.detect_kind(b"\x00\x00\x00\x18ftypmp42") == ("video_clip", "mp4")
    assert scene_assets.detect_kind(b"\x00\x00\x00\x14ftypqt  ") == ("video_clip", "mov")
    assert scene_assets.detect_kind(b"\x1a\x45\xdf\xa3xxxx") == ("video_clip", "webm")
    assert scene_assets.detect_kind(b"<html>not an image</html>") is None


def test_upload_ok_with_thumb_and_warnings(session: Session, env: dict[str, object]) -> None:
    r = scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080), "cena1.png")  # type: ignore[arg-type]
    assert (
        r.asset.type == "image"
        and r.asset.meta["width"] == 1920
        and r.warnings == []
        and not r.replaced
    )
    root: Path = env["root"]  # type: ignore[assignment]
    assert (root / r.asset.path).exists() and (root / r.asset.meta["thumb"]).exists()
    with Image.open(root / r.asset.meta["thumb"]) as t:
        assert t.width <= 480
    low = scene_assets.save_upload(session, env["vid"], 1, png(1280, 720), "b.png")  # type: ignore[arg-type]
    assert any("abaixo de 1920" in w for w in low.warnings)
    odd = scene_assets.save_upload(session, env["vid"], 2, png(1500, 1000), "c.png")  # type: ignore[arg-type]  # 3:2 (ChatGPT): aviso, não erro
    assert any("será ajustada" in w for w in odd.warnings)


@pytest.mark.parametrize(
    ("data", "variant", "msg"),
    [
        (b"MZ not an image at all", "main", "Formato não suportado"),
        (png(300, 200), "main", "Resolução muito baixa"),
        (png(1080, 1920), "main", "é vertical"),
        (png(1920, 1080), "vertical", "é horizontal"),
        (png(1920, 1080)[:200], "main", "corrompida"),
        (b"", "main", "vazio"),
    ],
)
def test_upload_rejections(
    session: Session, env: dict[str, object], data: bytes, variant: str, msg: str
) -> None:
    with pytest.raises(InvalidState, match=msg):
        scene_assets.save_upload(session, env["vid"], 0, data, "x.png", variant)  # type: ignore[arg-type]
    assert list(Path(env["root"]).rglob("s001_*")) == []  # type: ignore[arg-type]  # nada fica no disco


def test_too_big_and_unknown_scene_and_variant(session: Session, env: dict[str, object]) -> None:
    big = png(1920, 1080) + b"0" * (1024 * 1024 + 1)
    with pytest.raises(InvalidState, match="Cloudflare"):
        scene_assets.save_upload(session, env["vid"], 0, big, "big.png")  # type: ignore[arg-type]
    with pytest.raises(NotFound, match="Cena 9"):
        scene_assets.save_upload(session, env["vid"], 8, png(1920, 1080), "x.png")  # type: ignore[arg-type]
    with pytest.raises(InvalidState, match="Variante"):
        scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080), "x.png", "square")  # type: ignore[arg-type]


def test_replace_deletes_old_file_and_remove(session: Session, env: dict[str, object]) -> None:
    root: Path = env["root"]  # type: ignore[assignment]
    first = scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080, "red"), "a.png")  # type: ignore[arg-type]
    old_path, old_thumb = root / first.asset.path, root / first.asset.meta["thumb"]
    second = scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080, "blue"), "b.png")  # type: ignore[arg-type]
    assert (
        second.replaced
        and not old_path.exists()
        and not old_thumb.exists()
        and (root / second.asset.path).exists()
    )
    from sqlmodel import select

    assert (
        len(session.exec(select(Asset).where(Asset.scene_id == second.asset.scene_id)).all()) == 1
    )
    scene_assets.remove_asset(session, env["vid"], second.asset.id or 0)  # type: ignore[arg-type]
    assert not (root / second.asset.path).exists()
    with pytest.raises(NotFound):
        scene_assets.remove_asset(session, env["vid"], second.asset.id or 0)  # type: ignore[arg-type]


def test_same_image_twice_is_not_deleted_by_replace(
    session: Session, env: dict[str, object]
) -> None:
    data = png(1920, 1080)
    a = scene_assets.save_upload(session, env["vid"], 0, data, "a.png")  # type: ignore[arg-type]
    b = scene_assets.save_upload(session, env["vid"], 0, data, "a.png")  # type: ignore[arg-type]
    assert (
        b.replaced and (Path(env["root"]) / b.asset.path).exists() and a.asset.path == b.asset.path
    )  # type: ignore[arg-type]


def test_vertical_variant_marks_scene_and_coexists(
    session: Session, env: dict[str, object]
) -> None:
    main = scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080), "m.png")  # type: ignore[arg-type]
    vert = scene_assets.save_upload(session, env["vid"], 0, png(1080, 1920), "v.png", "vertical")  # type: ignore[arg-type]
    scenes = scene_prompts.approved_scenes(session, env["vid"])  # type: ignore[arg-type]
    assert scenes[0].wants_vertical
    amap = scene_assets.scene_assets(session, [scenes[0].id or 0])
    assert (
        set(amap[scenes[0].id or 0]) == {"main", "vertical"}
        and amap[scenes[0].id or 0]["main"].id == main.asset.id
        and vert.asset.meta["variant"] == "vertical"
    )


def test_map_filenames_by_number_then_order() -> None:
    names = [
        "scene_03_final.webp",
        "cena-1.png",
        "zebra.png",
        "alpha.png",
        "IMG_99999.png",
        "02.jpg",
    ]
    res = scene_assets.map_filenames(names, 5, filled={3})
    by = {r["name"]: r["scene"] for r in res}
    assert by["cena-1.png"] == 0 and by["02.jpg"] == 1 and by["scene_03_final.webp"] == 2
    # sem número válido: preenche as cenas vazias restantes (índice 4; a 3 já tem imagem) em ordem natural
    assert by["alpha.png"] == 4 and by["IMG_99999.png"] is None and by["zebra.png"] is None
    assert "Não há mais cenas" in [r for r in res if r["name"] == "zebra.png"][0]["reason"]


def test_map_filenames_duplicates_and_empty() -> None:
    res = scene_assets.map_filenames(["1.png", "01.png"], 3, set())
    assert [r["scene"] for r in res] == [0, None] and "Duas imagens" in res[1]["reason"]
    assert scene_assets.map_filenames([], 3, set()) == []


def test_stage_states_for_prompts_and_assets(
    session: Session, env: dict[str, object], user_token: tuple[User, str]
) -> None:
    from canaisdark.schemas.scene import PromptIn
    from canaisdark.services import tasks, video_status

    def states() -> dict[str, str]:
        v = videos.get_video(session, env["vid"])  # type: ignore[arg-type]
        return {x.key: x.state for x in video_status.get_stages(session, v)}

    assert states()["prompts"] == "not_started" and states()["assets"] == "not_started"
    scene_prompts.request_prompts(session, env["vid"], "", user_token[0])  # type: ignore[arg-type]
    assert states()["prompts"] == "pending"
    task = tasks.claim_next(session, user_token[0], "generate_prompts")
    assert task and states()["prompts"] == "in_progress"
    scene_prompts.save_prompts(session, env["vid"], [PromptIn(scene_index=0, image_prompt="a")])  # type: ignore[arg-type]
    assert states()["prompts"] == "in_progress"  # tarefa ainda aberta
    tasks.complete_task(session, task.id or 0, user_token[0], "parcial")
    assert states()["prompts"] == "partial"
    scene_prompts.save_prompts(
        session,
        env["vid"],
        [
            PromptIn(scene_index=1, image_prompt="b", vertical=True),
            PromptIn(scene_index=2, image_prompt="c"),
        ],
    )  # type: ignore[arg-type]
    assert states()["prompts"] == "done"
    scene_assets.save_upload(session, env["vid"], 0, png(1920, 1080), "1.png")  # type: ignore[arg-type]
    assert states()["assets"] == "partial"
    scene_assets.save_upload(session, env["vid"], 1, png(1920, 1080), "2.png")  # type: ignore[arg-type]
    scene_assets.save_upload(session, env["vid"], 2, png(1920, 1080), "3.png")  # type: ignore[arg-type]
    assert states()["assets"] == "partial"  # a cena 2 pediu versão vertical
    scene_assets.save_upload(session, env["vid"], 1, png(1080, 1920), "2v.png", "vertical")  # type: ignore[arg-type]
    assert states()["assets"] == "done"
