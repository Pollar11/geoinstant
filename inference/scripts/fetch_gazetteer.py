"""Download GeoNames cities + countries into artifacts/ (CC-BY 4.0)."""

from __future__ import annotations

import argparse
import csv
import io
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
# ISO codes of countries/territories that drive on the left (used by the traffic-side cue).
LEFT_DRIVING = set(
    [
        "AG",
        "AI",
        "AU",
        "BB",
        "BD",
        "BM",
        "BN",
        "BS",
        "BT",
        "BW",
        "CC",
        "CK",
        "CX",
        "CY",
        "DM",
        "FJ",
        "FK",
        "GB",
        "GD",
        "GG",
        "GY",
        "HK",
        "ID",
        "IE",
        "IM",
        "IN",
        "JE",
        "JM",
        "JP",
        "KE",
        "KI",
        "KN",
        "KY",
        "LC",
        "LK",
        "LS",
        "MO",
        "MS",
        "MT",
        "MU",
        "MV",
        "MW",
        "MY",
        "MZ",
        "NA",
        "NF",
        "NP",
        "NR",
        "NU",
        "NZ",
        "PG",
        "PK",
        "PN",
        "SB",
        "SC",
        "SG",
        "SH",
        "SR",
        "SZ",
        "TC",
        "TH",
        "TK",
        "TL",
        "TO",
        "TT",
        "TV",
        "TZ",
        "UG",
        "VC",
        "VG",
        "VI",
        "WS",
        "ZA",
        "ZM",
        "ZW",
    ]
)


def fetch(name: str) -> bytes:
    with urllib.request.urlopen(BASE + name, timeout=120) as r:
        return r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("artifacts/cities.csv"))
    ap.add_argument("--dataset", default="cities1000", choices=["cities500", "cities1000", "cities5000", "cities15000"])
    args = ap.parse_args()

    admin1 = {}
    for line in fetch("admin1CodesASCII.txt").decode("utf-8").splitlines():
        code, name, *_ = line.split("\t")
        admin1[code] = name

    z = zipfile.ZipFile(io.BytesIO(fetch(f"{args.dataset}.zip")))
    rows = z.read(f"{args.dataset}.txt").decode("utf-8").splitlines()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "iso2", "admin1", "lat", "lon", "population"])
        for line in rows:
            c = line.split("\t")
            # geonameid, name, asciiname, alternatenames, lat, lon, fclass, fcode, cc, cc2, admin1, ..., population
            w.writerow([c[1], c[8], admin1.get(f"{c[8]}.{c[10]}", ""), c[4], c[5], c[14] or 0])
    countries_out = args.out.with_name("countries.csv")
    with countries_out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iso2", "name", "continent", "drives_on"])
        for line in fetch("countryInfo.txt").decode("utf-8").splitlines():
            if line.startswith("#"):
                continue
            c = line.split("\t")
            w.writerow([c[0], c[4], c[8], "L" if c[0] in LEFT_DRIVING else "R"])
    print(f"{len(rows)} places → {args.out}; countries → {countries_out}")


if __name__ == "__main__":
    main()
