from pathlib import Path

from canaisdark.config import get_settings


def video_dir(channel_slug: str, video_id: int, create: bool = False) -> Path:
    path = get_settings().data_dir / channel_slug / str(video_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
