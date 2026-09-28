"""Hierarchical geocell tree: continent → country → admin1 → leaf. Coarse levels are leaf marginals."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from .gazetteer import CONTINENT_NAMES, Gazetteer
from .geo import haversine_km

Level = Literal["continent", "country", "admin1", "leaf"]
LEVELS: tuple[Level, ...] = ("continent", "country", "admin1", "leaf")


@dataclass
class LevelIndex:
    parent: NDArray[np.int64]  # (L,) node id of each leaf at this level
    keys: list[str]  # node id → stable key ("EU", "FR", "FR/Ile-de-France", leaf id)
    labels: list[str]  # node id → human label


@dataclass
class CellTree:
    lat: NDArray[np.float64]
    lon: NDArray[np.float64]
    iso2: list[str]
    admin1: list[str]
    name: list[str]
    scale_km: NDArray[np.float64]  # typical radius of the cell
    log_prior: NDArray[np.float64]  # training-distribution prior over leaves
    continent: list[str]
    levels: dict[str, LevelIndex] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.levels = {
            "continent": self._index(self.continent, lambda k: CONTINENT_NAMES.get(k, k or "Unknown")),
            "country": self._index(self.iso2, lambda k: k),
            "admin1": self._index(
                [f"{c}/{a}" for c, a in zip(self.iso2, self.admin1, strict=True)],
                lambda k: k.split("/", 1)[1] or k.split("/", 1)[0],
            ),
            "leaf": LevelIndex(parent=np.arange(len(self)), keys=[str(i) for i in range(len(self))], labels=list(self.name)),
        }

    def set_country_names(self, names: dict[str, str]) -> None:
        lv = self.levels["country"]
        lv.labels = [names.get(k, k) for k in lv.keys]

    @staticmethod
    def _index(values: list[str], label: object) -> LevelIndex:
        keys, inv = np.unique(np.asarray(values, dtype=object), return_inverse=True)
        keys_l = [str(k) for k in keys]
        return LevelIndex(parent=inv.astype(np.int64), keys=keys_l, labels=[label(k) for k in keys_l])  # type: ignore[operator]

    def __len__(self) -> int:
        return int(self.lat.shape[0])

    # ------------------------------------------------------------------------------------
    def marginal(self, p: NDArray[np.float64], level: Level, mask: NDArray[np.bool_] | None = None) -> NDArray[np.float64]:
        lv = self.levels[level]
        w = p if mask is None else np.where(mask, p, 0.0)
        return np.bincount(lv.parent, weights=w, minlength=len(lv.keys)).astype(np.float64)

    def nearest_leaf(
        self, lat: NDArray[np.float64] | float, lon: NDArray[np.float64] | float, chunk: int = 4096
    ) -> NDArray[np.int64]:
        lat_a = np.atleast_1d(np.asarray(lat, dtype=np.float64))
        lon_a = np.atleast_1d(np.asarray(lon, dtype=np.float64))
        out = np.empty(lat_a.shape[0], dtype=np.int64)
        for s in range(0, lat_a.shape[0], chunk):
            d = haversine_km(lat_a[s : s + chunk, None], lon_a[s : s + chunk, None], self.lat[None, :], self.lon[None, :])
            # Normalise by cell size: assign to the containing cell, not the nearest centroid.
            out[s : s + chunk] = np.argmin(d / self.scale_km[None, :], axis=1)
        return out

    def distances_km(self, lat: float, lon: float) -> NDArray[np.float64]:
        return haversine_km(lat, lon, self.lat, self.lon)

    # ------------------------------------------------------------------------------------
    @classmethod
    def from_gazetteer(cls, gaz: Gazetteer, scale_km: float = 30.0) -> CellTree:
        n = len(gaz)
        tree = cls(
            lat=gaz.lat.copy(),
            lon=gaz.lon.copy(),
            iso2=list(gaz.iso2),
            admin1=list(gaz.admin1),
            name=list(gaz.names),
            scale_km=np.full(n, scale_km),
            log_prior=np.zeros(n),
            continent=[gaz.country(c).continent for c in gaz.iso2],
        )
        tree.set_country_names({k: v.name for k, v in gaz.countries.items()})
        return tree

    @classmethod
    def load(cls, path: Path, gaz: Gazetteer) -> CellTree:
        """Load leaves produced by scripts/build_cells.py (npz with string arrays)."""
        z = np.load(path, allow_pickle=False)
        iso2 = [str(x) for x in z["iso2"]]
        tree = cls(
            lat=z["lat"].astype(np.float64),
            lon=z["lon"].astype(np.float64),
            iso2=iso2,
            admin1=[str(x) for x in z["admin1"]],
            name=[str(x) for x in z["name"]],
            scale_km=z["scale_km"].astype(np.float64),
            log_prior=z["log_prior"].astype(np.float64) if "log_prior" in z else np.zeros(len(iso2)),
            continent=[gaz.country(c).continent for c in iso2],
        )
        tree.set_country_names({k: v.name for k, v in gaz.countries.items()})
        return tree
