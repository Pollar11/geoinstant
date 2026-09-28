import numpy as np

from geoinstant.cells import CellTree
from geoinstant.evidence.base import observation_loglik
from geoinstant.evidence.text_cues import analyse_text
from geoinstant.gazetteer import Gazetteer

TREE = CellTree.from_gazetteer(Gazetteer())


def keys(texts: list[str]) -> set[str]:
    return {o.key for o in analyse_text(texts)}


def top_country(texts: list[str]) -> str:
    total = sum(observation_loglik(o, TREE) for o in analyse_text(texts))
    country = TREE.levels["country"]
    p = np.exp(total - total.max())
    return country.keys[int(np.argmax(np.bincount(country.parent, weights=p)))]


def test_ukrainian_letters_beat_generic_cyrillic() -> None:
    assert {"script:cyrillic", "letter:uk"} <= keys(["вулиця Хрещатик, їжа"])
    assert top_country(["вулиця Хрещатик, їжа"]) == "UA"


def test_scripts() -> None:
    assert top_country(["서울특별시"]) == "KR"
    assert top_country(["ถนนสุขุมวิท"]) == "TH"
    assert top_country(["ラーメン 東京"]) == "JP"  # kana wins over Han
    assert "script:han" not in keys(["ラーメン 東京"])


def test_latin_languages() -> None:
    assert top_country(["Hauptstraße 5"]) == "DE"
    assert top_country(["Kossuth Lajos utca"]) == "HU"
    assert top_country(["ul. Marszałkowska"]) == "PL"
    assert top_country(["Rua Augusta"]) in {"PT", "BR"}


def test_tld_and_phone() -> None:
    assert "tld:BR" in keys(["www.padaria.com.br"])
    assert "phone:48" in keys(["Tel. +48 22 123 45 67"])
    assert "phone:351" in keys(["+351 21 000 0000"])


def test_english_words_do_not_trigger_foreign_street_rules() -> None:
    assert not keys(["Departures via London", "Gate 12", "Main Avenue boulevard"])
