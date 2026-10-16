"""French trade categories map to canonical slugs (QA3 chiffrage-library-inventory-27)."""

import pytest

from app.domain.value_objects.library_category import normalize_category


@pytest.mark.parametrize(
    ("raw", "slug"),
    [
        ("Plâtrerie", "materiaux_construction"),
        ("Maçonnerie", "materiaux_construction"),
        ("Placo", "materiaux_construction"),
        ("Bois", "materiaux_construction"),
        ("Toiture", "materiaux_construction"),
        ("Enduits", "materiaux_construction"),
        ("Gros œuvre", "materiaux_construction"),
        ("Second œuvre", "materiaux_construction"),
        ("Colles", "droguerie"),
        ("Luminaires", "luminaire"),
        ("Peintures", "revetement_sol_mur_peinture"),
        ("Robinets", "plomberie"),
        ("Isolant mince", "materiaux_construction"),
        # unchanged behaviour
        ("Vis", "quincaillerie"),
        ("Visserie", "quincaillerie"),
        ("Carrelage", "revetement_sol_mur_peinture"),
        ("Rien à voir", "autre"),
    ],
)
def test_common_french_trade_names(raw, slug):
    assert normalize_category(raw) == slug
