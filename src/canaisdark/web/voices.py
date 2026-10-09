"""Escolha de voz e modelo do canal, com prévias gratuitas e amostra curta com o texto do usuário."""

import math
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.integrations.elevenlabs import ElevenLabsError, default_client
from canaisdark.models import Video
from canaisdark.schemas.channel import ChannelUpdate
from canaisdark.services import channels, credits, scripts, voices
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, render, templates

router = APIRouter(include_in_schema=False)


def _default_sample_text(session: Session, channel_id: int) -> str:
    """Primeira frase do roteiro mais recente do canal (ou um texto padrão)."""
    for video in session.exec(
        select(Video).where(Video.channel_id == channel_id).order_by(col(Video.id).desc())
    ):
        versions = scripts.list_versions(session, video.id or 0)
        if versions:
            scenes = scripts.scenes_of(session, versions[0])
            if scenes:
                return scenes[0].text[: voices.SAMPLE_MAX_CHARS]
    return "Abril de 1986. Uma cidade inteira recebeu ordem para partir."


@router.get("/channels/{slug}/voice", response_class=HTMLResponse)
def voice_page(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    tab: str = "account",
    accent: str = "brazilian",
    gender: str = "",
    q: str = "",
) -> Response:
    """Casca da página (instantânea). A lista de vozes carrega depois, por HTMX, porque a API do ElevenLabs é lenta."""
    channel = channels.get_channel_by_slug(session, slug)
    return render(
        request,
        "voice_picker.html",
        user,
        channel=channel,
        tab=tab,
        accent=accent,
        gender=gender,
        q=q,
        models=voices.MODELS,
        multiplier=voices.model_multiplier(channel.tts_model),
        ready=get_settings().elevenlabs_ready,
        sample_text=_default_sample_text(session, channel.id or 0),
        max_chars=voices.SAMPLE_MAX_CHARS,
        crumbs=[("Canais", "/"), (channel.name, f"/channels/{slug}")],
    )


@router.get("/channels/{slug}/voice/list", response_class=HTMLResponse)
def voice_list(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    tab: str = "account",
    accent: str = "brazilian",
    gender: str = "",
    q: str = "",
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    error = ""
    items: list[dict[str, Any]] = []
    try:
        items = (
            voices.library_voices("pt", accent, gender, q)
            if tab == "library"
            else voices.account_voices()
        )
    except ElevenLabsError as e:
        error = str(e)
    balance = credits.get_balance()
    tier = balance.tier if balance else ""
    return templates.TemplateResponse(
        request,
        "_voice_list.html",
        {
            "user": user,
            "channel": channel,
            "tab": tab,
            "items": items,
            "error": error,
            "tier": tier,
            "library_usable": voices.library_usable(tier),
        },
    )


@router.post("/channels/{slug}/voice/select")
def select_voice(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    voice_id: Annotated[str, Form()] = "",
    model_id: Annotated[str, Form()] = "",
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    try:
        channels.update_channel(
            session,
            channel.id,
            ChannelUpdate(
                elevenlabs_voice_id=voice_id.strip() or None, tts_model=model_id.strip() or None
            ),
        )
    except DomainError as e:
        return back(request, f"/channels/{slug}/voice", error=e)
    return back(request, f"/channels/{slug}/voice", ok="Voz e modelo do canal atualizados")


class SampleRequest(BaseModel):
    voice_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


@router.post("/channels/{slug}/voice/sample")
def sample(user: WebUser, session: SessionDep, slug: str, data: SampleRequest) -> Response:
    """Amostra curta (≤150 caracteres) com a voz/modelo escolhidos. GASTA créditos (mostrado antes na tela)."""
    channels.get_channel_by_slug(session, slug)
    text = data.text.strip()
    if len(text) > voices.SAMPLE_MAX_CHARS:
        return JSONResponse(
            {"detail": f"Texto de teste limitado a {voices.SAMPLE_MAX_CHARS} caracteres."},
            status_code=422,
        )
    if not get_settings().elevenlabs_ready:
        return JSONResponse(
            {"detail": "ELEVENLABS_API_KEY não configurada no servidor."}, status_code=422
        )
    channel = channels.get_channel_by_slug(session, slug)
    try:
        audio = default_client().text_to_speech(
            data.voice_id,
            text,
            model_id=data.model_id,
            stability=channel.tts_stability,
            similarity=channel.tts_similarity,
            speed=channel.tts_speed,
        )
    except ElevenLabsError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return Response(
        audio,
        media_type="audio/mpeg",
        headers={
            "X-Credits-Estimated": str(
                math.ceil(len(text) * voices.model_multiplier(data.model_id))
            )
        },
    )
