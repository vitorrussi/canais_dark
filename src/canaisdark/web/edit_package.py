"""Edição livre pelo Claude: links assinados (download/upload) e fallback manual pelo site.

Rotas `/x/...` não usam sessão: a assinatura (3 h, 1 vídeo, amarrada ao token da pessoa) é a
credencial, pois quem chama é o sandbox do claude.ai com `curl`. Respostas sempre em JSON.
"""

import mimetypes
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response

from canaisdark.services import edit_package, videos
from canaisdark.services.errors import DomainError, Forbidden
from canaisdark.web.common import SessionDep, WebUser

router = APIRouter(include_in_schema=False)

_STATUS = {"NotFound": 404, "Conflict": 409, "InvalidState": 422, "Forbidden": 403}


def _error(e: DomainError) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "detail": str(e)}, status_code=_STATUS.get(type(e).__name__, 400)
    )


@router.get("/x/{signed}")
def download(signed: str, session: SessionDep) -> Response:
    try:
        grant = edit_package.verify(session, signed)
        if grant.key == "zip":
            manifest = edit_package.build_manifest(session, grant.video.id or 0, grant.user)
            path = edit_package.build_zip(session, grant.video, manifest)
            return FileResponse(path, media_type="application/zip", filename="pacote-de-edicao.zip")
        if grant.key.startswith("f:"):
            f = edit_package.find_file(session, grant.video, grant.key[2:])
            mime = mimetypes.guess_type(f.path.name)[0] or "application/octet-stream"
            return FileResponse(f.path, media_type=mime, filename=f.path.name)
        if grant.key.startswith("t:"):
            tf = edit_package.find_thumb_file(session, grant.video, grant.key[2:])
            tmime = mimetypes.guess_type(tf.path.name)[0] or "application/octet-stream"
            return FileResponse(tf.path, media_type=tmime, filename=tf.path.name)
        raise Forbidden("Este link não serve para download.")
    except DomainError as e:
        return _error(e)


def _upload_response(session: SessionDep, signed: str) -> tuple[Any, Any]:
    grant = edit_package.verify(session, signed)
    if grant.key != "upload":
        raise Forbidden("Este link não serve para envio.")
    return grant.video, grant.user


@router.post("/x/{signed}/upload")
async def upload(
    request: Request, signed: str, session: SessionDep, part: int = 1, total: int = 1
) -> Response:
    """Recebe o corpo bruto (≤ 95 MB por requisição). Na última parte, monta, valida e registra."""
    try:
        video, user = _upload_response(session, signed)
        target = edit_package.part_path(session, video, part, total)
        size = 0
        tmp = target.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > edit_package.MAX_PART_BYTES:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    return JSONResponse(
                        {"status": "error", "detail": "Parte maior que 95 MB: use split -b 90m."},
                        status_code=413,
                    )
                fh.write(chunk)
        if size == 0:
            tmp.unlink(missing_ok=True)
            return JSONResponse({"status": "error", "detail": "Corpo vazio."}, status_code=422)
        tmp.replace(target)
        assembled = await run_in_threadpool(edit_package.assemble, session, video, total)
        if assembled is None:
            got = edit_package.received_parts(session, video)
            return JSONResponse(
                {
                    "status": "partial",
                    "received": got,
                    "missing": [i for i in range(1, total + 1) if i not in got],
                }
            )
        result = await run_in_threadpool(edit_package.register, session, video, assembled, user)
        return JSONResponse(result)
    except DomainError as e:
        return _error(e)


@router.post("/x/{signed}/thumbnail")
async def upload_thumbnail(request: Request, signed: str, session: SessionDep) -> Response:
    """Recebe a imagem da thumbnail (corpo bruto, até 10 MB); valida e grava (a última vale)."""
    try:
        grant = edit_package.verify(session, signed)
        if grant.key != "thumb":
            raise Forbidden("Este link não serve para enviar a thumbnail.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > edit_package.THUMB_MAX_BYTES:
                return JSONResponse(
                    {"status": "error", "detail": "Imagem maior que 10 MB."}, status_code=413
                )
        result = await run_in_threadpool(
            edit_package.register_thumbnail, session, grant.video, bytes(body), grant.user
        )
        return JSONResponse(result)
    except DomainError as e:
        return _error(e)


# ----- Com sessão do site (fallback manual) -----


@router.get("/videos/{video_id}/edit-package.zip")
def package_zip(user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    manifest = edit_package.build_manifest(session, video_id, user)
    path = edit_package.build_zip(session, video, manifest)
    return FileResponse(path, media_type="application/zip", filename=f"video-{video_id}-pacote.zip")


@router.get("/videos/{video_id}/external-render/link")
def upload_link(user: WebUser, session: SessionDep, video_id: int, request: Request) -> Response:
    """Link de envio (válido 3 h) para o formulário do site enviar o MP4 em partes."""
    video = videos.get_video(session, video_id)
    signed = edit_package.sign(video, user, "upload")
    return JSONResponse({"url": f"/x/{signed}/upload", "max_part_bytes": 90 * 1024 * 1024})
