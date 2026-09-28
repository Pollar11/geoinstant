"""All street-photo sources behind one interface."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .mapillary import StreetImage

log = logging.getLogger(__name__)


class StreetSource(Protocol):
    async def list_images(self, bbox: tuple[float, float, float, float]) -> list[StreetImage]: ...
    async def fetch(self, url: str) -> bytes: ...


class MultiSource:
    """Lists from every source (one failing doesn't stop the others); fetches through the one that listed it."""

    def __init__(self, sources: list[StreetSource]) -> None:
        self.sources = sources
        self._owner: dict[str, StreetSource] = {}

    async def list_images(self, bbox: tuple[float, float, float, float]) -> list[StreetImage]:
        self._owner = {}
        found = await asyncio.gather(*(s.list_images(bbox) for s in self.sources), return_exceptions=True)
        out: dict[str, StreetImage] = {}
        errors = []
        for src, batch in zip(self.sources, found, strict=True):
            if isinstance(batch, BaseException):
                log.warning("%s listing failed: %s", type(src).__name__, batch)
                errors.append(batch)
                continue
            for im in batch:
                out.setdefault(im.id, im)
                self._owner[im.thumb_url] = self._owner[im.full_url] = src
        if errors and len(errors) == len(self.sources):
            raise errors[0]
        return list(out.values())

    async def fetch(self, url: str) -> bytes:
        return await self._owner.get(url, self.sources[0]).fetch(url)
