import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.script import SceneIn
from canaisdark.services import scene_prompts, scripts


def png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "navy").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def ctx(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    client.post("/login", data={"token": user_token[1]})
    client.post("/channels", data={"name": "Canal", "visual_style_prompt": "noir style"})
    r = client.post("/channels/canal/videos", data={"topic": "t"})
    vid = int(str(r.url).rsplit("/", 1)[1])
    return {"c": client, "vid": vid, "user": user_token[0], "session": session}


def _approve(ctx: dict[str, object], texts: list[str]) -> None:
    scripts.save_user_script(
        ctx["session"], ctx["vid"], [SceneIn(text=t) for t in texts], ctx["user"], approve=True
    )  # type: ignore[arg-type]


def test_blocked_until_script_approved(ctx: dict[str, object]) -> None:
    html = ctx["c"].get(f"/videos/{ctx['vid']}/scenes").text  # type: ignore[attr-defined]
    assert "Roteiro ainda não aprovado" in html


def test_page_lists_scenes_and_prompts_flow(ctx: dict[str, object]) -> None:
    _approve(ctx, ["Primeira cena.", "Segunda cena."])
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    html = c.get(f"/videos/{ctx['vid']}/scenes").text
    assert (
        "0/2 prompts" in html
        and "0/2 imagens" in html
        and "Primeira cena." in html
        and "Pedir prompts ao Claude" in html
    )
    r = c.post(f"/videos/{ctx['vid']}/prompts/request", data={"instructions": "mais detalhes"})
    assert (
        "Tarefa de prompts enfileirada" in r.text
        and "claim_next_task(video_id=" in r.text
        and "Copiar prompt" in r.text
    )
    assert (
        "já existe" in c.post(f"/videos/{ctx['vid']}/prompts/request", data={}).text.lower()
        or "aberta" in c.post(f"/videos/{ctx['vid']}/prompts/request", data={}).text
    )


def test_autosave_prompt_and_flags(ctx: dict[str, object]) -> None:
    _approve(ctx, ["Primeira cena.", "Segunda cena."])
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    scenes = scene_prompts.approved_scenes(ctx["session"], ctx["vid"])  # type: ignore[arg-type]
    r = c.post(
        f"/videos/{ctx['vid']}/scenes/{scenes[0].id}/update",
        json={"image_prompt": "a lighthouse", "wants_vertical": True},
    )
    j = r.json()
    assert (
        r.status_code == 200
        and j["full"] == "noir style, a lighthouse, wide 16:9 landscape composition"
        and j["wants_vertical"]
        and "vertical 9:16" in j["full_vertical"]
    )
    assert (
        c.post(f"/videos/{ctx['vid']}/scenes/99999/update", json={"image_prompt": "x"}).status_code
        == 422
    )
    html = c.get(f"/videos/{ctx['vid']}/scenes").text
    assert "1/2 prompts" in html and "a lighthouse" in html and "Copiar 9:16" in html


def test_upload_batch_delete_thumb_and_downloads(ctx: dict[str, object]) -> None:
    _approve(ctx, ["Um.", "Dois.", "Três."])
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["vid"]
    ok = c.post(
        f"/videos/{vid}/scenes/0/upload",
        files={"file": ("cena1.png", png(1920, 1080), "image/png")},
        data={"variant": "main"},
    )
    j = ok.json()
    assert ok.status_code == 200 and j["ok"] and not j["replaced"] and j["asset"]["thumb_url"]
    assert c.get(j["asset"]["thumb_url"]).headers["content-type"] == "image/jpeg"
    assert c.get(j["asset"]["file_url"]).status_code == 200
    again = c.post(
        f"/videos/{vid}/scenes/0/upload",
        files={"file": ("outra.png", png(1920, 1081), "image/png")},
        data={"variant": "main"},
    ).json()
    assert again["replaced"] is True
    bad = c.post(
        f"/videos/{vid}/scenes/1/upload",
        files={"file": ("x.png", b"not an image", "image/png")},
        data={"variant": "main"},
    )
    assert bad.status_code == 422 and "Formato não suportado" in bad.json()["detail"]
    odd = c.post(
        f"/videos/{vid}/scenes/1/upload",
        files={"file": ("p.png", png(1080, 1920), "image/png")},
        data={"variant": "main"},
    )
    assert odd.status_code == 422 and "vertical" in odd.json()["detail"]
    plan = c.post(
        f"/videos/{vid}/assets/batch-plan",
        json={"names": ["a.png", "03.png", "b.png"], "variant": "main"},
    ).json()["plan"]
    assert [p["scene"] for p in plan] == [
        1,
        2,
        None,
    ]  # 03 → cena 3; a.png → próxima vazia (cena 2); b.png sem cena livre
    assert "1/3 imagens" in c.get(f"/videos/{vid}/scenes").text
    assert c.post(f"/videos/{vid}/assets/{again['asset']['id']}/delete").json()["ok"]
    assert c.post(f"/videos/{vid}/assets/{again['asset']['id']}/delete").status_code == 422
    scene_prompts.save_prompts(
        ctx["session"], vid, [scene_prompts.PromptIn(scene_index=0, image_prompt="door")]
    )  # type: ignore[arg-type]
    assert "[Cena 1] noir style, door" in c.get(f"/videos/{vid}/prompts.txt").text
    assert json.loads(c.get(f"/videos/{vid}/prompts.json").text)[0]["image_prompt_full"].startswith(
        "noir style"
    )
    assert "attachment" in c.get(f"/videos/{vid}/prompts.md").headers["content-disposition"]
    assert c.get(f"/videos/{vid}/prompts.pdf").status_code == 422


def test_video_page_links_to_storyboard(ctx: dict[str, object]) -> None:
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    assert "Abrir storyboard" not in c.get(f"/videos/{ctx['vid']}").text
    _approve(ctx, ["Um."])
    html = c.get(f"/videos/{ctx['vid']}").text
    assert "Abrir storyboard" in html and "0/1 prompts" in html


def test_requires_login(client: TestClient) -> None:
    assert client.get("/videos/1/scenes", follow_redirects=False).status_code == 303
    assert (
        client.post(
            "/videos/1/scenes/0/upload", files={"file": ("a.png", b"x")}, follow_redirects=False
        ).status_code
        == 303
    )


def test_prompt_batches_partial_and_plan_mtimes(ctx: dict[str, object]) -> None:
    _approve(ctx, ["A.", "B.", "C.", "D."])
    c: TestClient = ctx["c"]  # type: ignore[assignment]
    vid = ctx["vid"]
    scene_prompts.save_prompts(
        ctx["session"],
        vid,
        [scene_prompts.PromptIn(scene_index=i, image_prompt=f"p{i}") for i in range(4)],
    )  # type: ignore[arg-type]
    html = c.get(f"/videos/{vid}/scenes/batches?size=3&variant=main").text
    assert (
        "Lote 1 (cenas 1–3, 3)" in html
        and "Lote 2 (cenas 4–4, 1)" in html
        and "Cena 01: p0" in html
    )
    assert "4 cena(s) em 1 mensagem" in c.get(f"/videos/{vid}/scenes/batches?size=0").text
    assert "Nenhuma cena" in c.get(f"/videos/{vid}/scenes/batches?size=5&variant=vertical").text
    page = c.get(f"/videos/{vid}/scenes").text
    assert "Pedir várias imagens numa só mensagem" in page and 'name="size"' in page
    plan = c.post(
        f"/videos/{vid}/assets/batch-plan",
        json={"names": ["x.png", "y.png"], "variant": "main", "mtimes": [200, 100]},
    ).json()
    assert [p["scene"] for p in plan["plan"]] == [1, 0] and plan["scene_count"] == 4
