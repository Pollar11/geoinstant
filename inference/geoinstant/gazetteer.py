"""Offline reverse geocoding (GeoNames, or the bundled seed)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DATA_DIR
from .geo import haversine_km

CONTINENT_NAMES = {
    "AF": "Africa",
    "AN": "Antarctica",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}


@dataclass(frozen=True)
class Country:
    iso2: str
    name: str
    continent: str
    drives_on: str  # "L" or "R"


@dataclass(frozen=True)
class Place:
    name: str
    admin1: str
    country: str
    country_code: str
    continent: str
    distance_km: float

    @property
    def display_name(self) -> str:
        parts = [self.name, self.admin1, self.country]
        seen: list[str] = []
        for p in parts:
            if p and p not in seen:
                seen.append(p)
        label = ", ".join(seen)
        if self.distance_km > 25:
            return f"~{self.distance_km:.0f} km from {label}"
        if self.distance_km > 3:
            return f"Near {label}"
        return label


def load_countries(path: Path = DATA_DIR / "countries.csv") -> dict[str, Country]:
    with path.open(newline="", encoding="utf-8") as f:
        return {r["iso2"]: Country(r["iso2"], r["name"], r["continent"], r["drives_on"]) for r in csv.DictReader(f)}


class Gazetteer:
    def __init__(self, csv_path: Path | None = None, countries: dict[str, Country] | None = None) -> None:
        self.countries = countries or load_countries()
        path = csv_path if csv_path and csv_path.exists() else DATA_DIR / "cities_seed.csv"
        self.source = path.name
        names, iso2, admin1, lat, lon, pop = [], [], [], [], [], []
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                names.append(r["name"])
                iso2.append(r["iso2"])
                admin1.append(r.get("admin1", ""))
                lat.append(float(r["lat"]))
                lon.append(float(r["lon"]))
                pop.append(float(r.get("population") or 0))
        self.names = names
        self.iso2 = iso2
        self.admin1 = admin1
        self.lat = np.asarray(lat)
        self.lon = np.asarray(lon)
        self.population = np.asarray(pop)

    def __len__(self) -> int:
        return len(self.names)

    def country(self, iso2: str) -> Country:
        return self.countries.get(iso2) or Country(iso2, iso2, "", "R")

    def reverse(self, lat: float, lon: float) -> Place:
        d = haversine_km(lat, lon, self.lat, self.lon)
        # Prefer bigger towns when distances are close.
        score = d - 2.0 * np.log10(np.maximum(self.population, 10.0))
        i = int(np.argmin(score))
        c = self.country(self.iso2[i])
        return Place(
            name=self.names[i],
            admin1=self.admin1[i],
            country=c.name,
            country_code=c.iso2,
            continent=CONTINENT_NAMES.get(c.continent, c.continent),
            distance_km=float(d[i]),
        )
