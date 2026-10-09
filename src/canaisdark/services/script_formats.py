"""Exportar roteiro como arquivo (.txt, .md, .json) e importar texto/arquivo em cenas."""

import json
import re
from dataclasses import dataclass

from canaisdark.models import Channel, Scene, ScriptVersion, Video
from canaisdark.schemas.script import MAX_SCENE_CHARS, SceneIn
from canaisdark.services import script_text
from canaisdark.services.errors import InvalidState

MAX_UPLOAD_BYTES = 2 * 1024 * 1024

FORMATS = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
}

SPLIT_RULES = {
    "auto": "Detectar automaticamente",
    "blocks": "Linha em branco entre cenas",
    "lines": "Uma cena por linha",
    "headings": "Títulos “Cena 1”, “## Cena 2”…",
    "sentences": "Uma cena por frase",
    "chars": "Agrupar frases até N caracteres",
}


@dataclass
class Download:
    filename: str
    mime: str
    content: str


def render_script(
    fmt: str,
    title: str,
    version: ScriptVersion,
    scenes: list[Scene],
    channel: Channel,
    video: Video,
) -> Download:
    if fmt not in FORMATS:
        raise InvalidState(f"Formato inválido '{fmt}'. Use: {', '.join(FORMATS)}")
    name = f"{channel.slug}-{video.id}-v{version.number}.{fmt}"
    if fmt == "txt":
        body = "\n\n".join(s.text for s in scenes) + "\n"
    elif fmt == "md":
        parts = [f"# {title}\n"]
        for s in scenes:
            block = f"## Cena {s.index + 1}\n\n{s.text}"
            if s.notes:
                block += f"\n\n> {s.notes}"
            parts.append(block + "\n")
        body = "\n".join(parts)
    else:
        body = json.dumps(
            {
                "format": "canaisdark.script",
                "version": 1,
                "title": title,
                "script_version": version.number,
                "approved": version.approved,
                "scenes": [{"text": s.text, "notes": s.notes} for s in scenes],
            },
            ensure_ascii=False,
            indent=2,
        )
    return Download(name, FORMATS[fmt], body)


# ----- Importar -----

_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?(?:cena|scene)\s*\d*\s*[:.\-–—)]*\s*(.*)$", re.IGNORECASE)


def decode_upload(data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidState("Arquivo grande demais (máximo 2 MB)")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise InvalidState("Não foi possível ler o arquivo como texto")


def _split_headings(text: str) -> list[SceneIn]:
    scenes: list[dict[str, list[str]]] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = _HEADING.match(line)
        if m and (line.lstrip().startswith("#") or re.match(r"\s*(cena|scene)\b", line, re.I)):
            scenes.append({"text": [m.group(1).strip()] if m.group(1).strip() else [], "notes": []})
        elif not line.strip():
            continue
        elif not scenes and re.match(r"#\s+\S", line):
            continue  # título do documento (# Título), não é cena
        else:
            if not scenes:
                scenes.append({"text": [], "notes": []})
            if line.lstrip().startswith(">"):
                scenes[-1]["notes"].append(line.lstrip()[1:].strip())
            else:
                scenes[-1]["text"].append(line.strip())
    return [
        SceneIn(text=" ".join(s["text"]), notes=" ".join(s["notes"])) for s in scenes if s["text"]
    ]


def _group_by_chars(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    current = ""
    for sentence in script_text.split_sentences(" ".join(script_text.split_blocks(text))):
        if current and len(current) + 1 + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def detect_rule(text: str) -> str:
    if any(
        _HEADING.match(line)
        and (line.lstrip().startswith("#") or re.match(r"\s*(cena|scene)\b", line, re.I))
        for line in text.splitlines()
    ):
        return "headings"
    if len(script_text.split_blocks(text)) > 1:
        return "blocks"
    return "sentences"


def text_to_scenes(text: str, rule: str = "auto", max_chars: int = 400) -> list[SceneIn]:
    if rule not in SPLIT_RULES:
        raise InvalidState(f"Regra de divisão inválida: {rule}")
    text = script_text.clean_text(text)
    if not text:
        raise InvalidState("O texto está vazio")
    if rule == "auto":
        rule = detect_rule(text)
    if rule == "headings":
        scenes = _split_headings(text)
    elif rule == "blocks":
        scenes = [SceneIn(text=t) for t in script_text.split_blocks(text)]
    elif rule == "lines":
        scenes = [SceneIn(text=t.strip()) for t in text.splitlines() if t.strip()]
    elif rule == "sentences":
        scenes = [
            SceneIn(text=t)
            for t in script_text.split_sentences(" ".join(script_text.split_blocks(text)))
        ]
    else:
        if not 50 <= max_chars <= MAX_SCENE_CHARS:
            raise InvalidState(f"N deve estar entre 50 e {MAX_SCENE_CHARS}")
        scenes = [SceneIn(text=t) for t in _group_by_chars(text, max_chars)]
    if not scenes:
        raise InvalidState("Nenhuma cena encontrada com essa regra de divisão")
    return scenes


def _scenes_from_json(raw: str) -> list[SceneIn] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("format") == "canaisdark.script":
        items = data.get("scenes", [])
    elif isinstance(data, dict) and data.get("format") == "canaisdark.video":
        scripts = data.get("scripts") or []
        if not scripts:
            raise InvalidState("O arquivo de vídeo não contém roteiros")
        items = max(scripts, key=lambda s: s.get("number", 0)).get("scenes", [])
    elif isinstance(data, list):
        items = data
    else:
        return None
    scenes = []
    for it in items:
        if isinstance(it, str):
            scenes.append(SceneIn(text=it))
        elif isinstance(it, dict) and str(it.get("text", "")).strip():
            scenes.append(SceneIn(text=str(it["text"]), notes=str(it.get("notes", ""))))
    if not scenes:
        raise InvalidState("Nenhuma cena válida encontrada no JSON")
    return scenes


def parse_input(
    pasted: str, filename: str | None, data: bytes | None, rule: str = "auto", max_chars: int = 400
) -> list[SceneIn]:
    """Aceita texto colado OU arquivo (.txt/.md/.json). Arquivo tem prioridade se enviado."""
    text = decode_upload(data) if data else pasted
    if filename and filename.lower().endswith(".json") and data:
        scenes = _scenes_from_json(text)
        if scenes is not None:
            return scenes
    return text_to_scenes(text, rule, max_chars)


def summarize(scenes: list[SceneIn], wpm: int = script_text.WORDS_PER_MINUTE) -> dict[str, object]:
    words = sum(script_text.word_count(s.text) for s in scenes)
    seconds = script_text.estimated_seconds(words, wpm)
    return {
        "scenes": len(scenes),
        "words": words,
        "duration": script_text.format_duration(seconds),
        "too_long": [i + 1 for i, s in enumerate(scenes) if len(s.text) > MAX_SCENE_CHARS],
    }


def default_title(video: Video) -> str:
    return video.title or f"Vídeo {video.id}"
