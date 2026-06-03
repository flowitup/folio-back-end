"""Canonical category vocabulary for the bibliotheque (product library).

16 canonical slugs — 15 Leroy Merlin top-level "univers" + one fallback.
This module is PURE stdlib (unicodedata, re) — zero Flask/SQLAlchemy/infra deps.

Design notes
------------
* normalize_category() is idempotent: feeding a slug back returns the same slug
  because each slug folds to a synonym key for its own category.
* None / empty / whitespace-only input returns None (genuinely un-categorised).
  Non-null text that maps to nothing returns "autre" (safe fallback).
* Longest-keyword-wins is used for substring matching to guarantee determinism
  when two synonym lists could both match (e.g. "parquet" is in revetement list,
  not menuiserie list, but if both had overlapping keywords the longest match wins).
  Tie-break example: "parquet" belongs to revetement_sol_mur_peinture, not
  menuiserie — rely on the explicit curated synonym table (parquet is only in
  the revetement list) rather than substring ordering.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------------------
# Canonical slug order (matches the 16 "univers" ordering in the spec)
# ---------------------------------------------------------------------------

LIBRARY_CATEGORY_SLUGS: tuple[str, ...] = (
    "terrasse_jardin",
    "revetement_sol_mur_peinture",
    "chauffage_clim_ventilation",
    "salle_de_bains",
    "meuble_rangement",
    "cuisine",
    "luminaire",
    "decoration",
    "menuiserie",
    "materiaux_construction",
    "electricite_domotique",
    "outillage",
    "plomberie",
    "quincaillerie",
    "droguerie",
    "autre",
)

# Canonical French display labels, keyed by slug
CANONICAL_FR: dict[str, str] = {
    "terrasse_jardin": "Terrasse et jardin",
    "revetement_sol_mur_peinture": "Revêtement sol, mur et peinture",
    "chauffage_clim_ventilation": "Chauffage, climatisation et ventilation",
    "salle_de_bains": "Salle de bains",
    "meuble_rangement": "Meuble et rangement",
    "cuisine": "Cuisine",
    "luminaire": "Luminaire",
    "decoration": "Décoration",
    "menuiserie": "Menuiserie",
    "materiaux_construction": "Matériaux de construction",
    "electricite_domotique": "Electricité et domotique",
    "outillage": "Outillage",
    "plomberie": "Plomberie",
    "quincaillerie": "Quincaillerie",
    "droguerie": "Droguerie",
    "autre": "Autre / Non classé",
}

# ---------------------------------------------------------------------------
# Synonym table — folded keyword → canonical slug
#
# Rules for curation:
#  - Every slug must appear as its own synonym (folded form) so that feeding a
#    slug back normalises to itself (idempotency guarantee).
#  - "terrasse_jardin" folds to "terrasse jardin" (underscores → spaces after
#    fold, see _fold()), so it maps correctly.
#  - Avoid over-broad short keywords that cause false positives:
#    "sol" is kept because no other category has "sol" as a keyword and the
#    shortest unique key is fine; risky short keys like single-letter words
#    are not included.
#  - "parquet" maps ONLY to revetement_sol_mur_peinture; it is NOT in the
#    menuiserie list. This is an intentional curated tie-break: parquet is a
#    floor covering, not a carpentry element.
#  - Short terms like "porte" or "fenetre" are included in menuiserie because
#    they are strongly associated with joinery; consumers who worry about
#    false positives (e.g. "porte-savon" → menuiserie) can widen the keyword
#    to a longer phrase, but in practice "porte-savon" would be outcompeted
#    by "salle de bains" / "lavabo" synonyms since those appear in the
#    salle_de_bains list — substring match on the whole folded string means
#    if the value is "porte-savon salle de bains" both keys match and the
#    longer one ("salle de bains" = 12 chars) wins. For plain "porte-savon"
#    the longest match is "porte" (5 chars) → menuiserie; that is acceptable
#    and expected behaviour for the Leroy Merlin import context.
# ---------------------------------------------------------------------------

# fmt: off
_SYNONYMS: dict[str, str] = {
    # terrasse_jardin
    "jardin":                       "terrasse_jardin",
    "terrasse":                     "terrasse_jardin",
    "exterieur":                    "terrasse_jardin",
    "plein air":                    "terrasse_jardin",
    "terrasse jardin":              "terrasse_jardin",      # idempotency: slug folded

    # revetement_sol_mur_peinture
    "revetement":                   "revetement_sol_mur_peinture",
    "sol":                          "revetement_sol_mur_peinture",
    "mur":                          "revetement_sol_mur_peinture",
    "peinture":                     "revetement_sol_mur_peinture",
    "carrelage":                    "revetement_sol_mur_peinture",
    "parquet":                      "revetement_sol_mur_peinture",   # floor covering, not joinery
    "papier peint":                 "revetement_sol_mur_peinture",
    "lambris":                      "revetement_sol_mur_peinture",
    "revetement sol mur peinture":  "revetement_sol_mur_peinture",  # idempotency: slug folded

    # chauffage_clim_ventilation
    "chauffage":                    "chauffage_clim_ventilation",
    "climatisation":                "chauffage_clim_ventilation",
    "clim":                         "chauffage_clim_ventilation",
    "ventilation":                  "chauffage_clim_ventilation",
    "radiateur":                    "chauffage_clim_ventilation",
    "vmc":                          "chauffage_clim_ventilation",
    "poele":                        "chauffage_clim_ventilation",
    "chauffe eau":                  "chauffage_clim_ventilation",
    "chauffage clim ventilation":   "chauffage_clim_ventilation",   # idempotency: slug folded
    "chauffage climatisation et ventilation": "chauffage_clim_ventilation",

    # salle_de_bains
    "salle de bains":               "salle_de_bains",
    "salle de bain":                "salle_de_bains",
    "douche":                       "salle_de_bains",
    "baignoire":                    "salle_de_bains",
    "lavabo":                       "salle_de_bains",
    "sanitaire":                    "salle_de_bains",
    # "salle de bains" is already listed above — covers both "salle de bain" and slug idempotency

    # meuble_rangement
    "meuble":                       "meuble_rangement",
    "rangement":                    "meuble_rangement",
    "dressing":                     "meuble_rangement",
    "etagere":                      "meuble_rangement",
    "placard":                      "meuble_rangement",
    "meuble rangement":             "meuble_rangement",            # idempotency: slug folded

    # cuisine
    "cuisine":                      "cuisine",
    "evier":                        "cuisine",
    "plan de travail":              "cuisine",
    "credence":                     "cuisine",

    # luminaire
    "luminaire":                    "luminaire",
    "lampe":                        "luminaire",
    "eclairage":                    "luminaire",
    "ampoule":                      "luminaire",
    "spot":                         "luminaire",
    "applique":                     "luminaire",

    # decoration
    "decoration":                   "decoration",
    "deco":                         "decoration",
    "rideau":                       "decoration",
    "tableau":                      "decoration",
    "miroir":                       "decoration",
    "tapis":                        "decoration",

    # menuiserie
    "menuiserie":                   "menuiserie",
    "porte":                        "menuiserie",
    "fenetre":                      "menuiserie",
    "volet":                        "menuiserie",
    "escalier":                     "menuiserie",

    # materiaux_construction
    "materiaux":                    "materiaux_construction",
    "construction":                 "materiaux_construction",
    "ciment":                       "materiaux_construction",
    "beton":                        "materiaux_construction",
    "platre":                       "materiaux_construction",
    "plaque":                       "materiaux_construction",
    "isolation":                    "materiaux_construction",
    "brique":                       "materiaux_construction",
    "parpaing":                     "materiaux_construction",
    "materiaux de construction":    "materiaux_construction",
    "materiaux construction":       "materiaux_construction",      # idempotency: slug folded

    # electricite_domotique
    "electricite":                  "electricite_domotique",
    "electrique":                   "electricite_domotique",
    "domotique":                    "electricite_domotique",
    "interrupteur":                 "electricite_domotique",
    "prise":                        "electricite_domotique",
    "cable":                        "electricite_domotique",
    "electricite domotique":        "electricite_domotique",       # idempotency: slug folded

    # outillage
    "outillage":                    "outillage",
    "outil":                        "outillage",
    "perceuse":                     "outillage",
    "visseuse":                     "outillage",
    "scie":                         "outillage",
    "meuleuse":                     "outillage",

    # plomberie
    "plomberie":                    "plomberie",
    "tuyau":                        "plomberie",
    "raccord":                      "plomberie",
    "robinet":                      "plomberie",
    "joint":                        "plomberie",
    "flexible":                     "plomberie",

    # quincaillerie
    "quincaillerie":                "quincaillerie",
    "vis":                          "quincaillerie",
    "boulon":                       "quincaillerie",
    "cheville":                     "quincaillerie",
    "charniere":                    "quincaillerie",
    "serrure":                      "quincaillerie",
    "clou":                         "quincaillerie",
    "visserie":                     "quincaillerie",

    # droguerie
    "droguerie":                    "droguerie",
    "nettoyant":                    "droguerie",
    "colle":                        "droguerie",
    "mastic":                       "droguerie",
    "produit entretien":            "droguerie",
    "soude":                        "droguerie",

    # autre — slug itself maps to itself for idempotency
    "autre":                        "autre",
}
# fmt: on

# Pre-compute sorted keyword list (longest first) once at import time.
# Longest-keyword-wins is the tie-break for substring matching: if two keys
# both appear in the folded value, the longer one wins. This is deterministic
# because we sort by descending length and return the first match.
_SORTED_KEYWORDS: list[tuple[str, str]] = sorted(_SYNONYMS.items(), key=lambda kv: len(kv[0]), reverse=True)

# Pre-compute set of valid slugs for O(1) membership checks
_SLUG_SET: frozenset[str] = frozenset(LIBRARY_CATEGORY_SLUGS)

# Regex to collapse runs of non-alphanumeric characters to a single space
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")


def _fold(s: str) -> str:
    """Fold a string for locale-insensitive comparison.

    Steps:
    1. NFKD decomposition + ASCII encode/decode (strips accents).
    2. Lowercase.
    3. Collapse runs of non-alphanumeric characters to a single space.
    4. Strip leading/trailing whitespace.

    Examples:
        "Plomberie"          → "plomberie"
        "Terrasse_jardin"    → "terrasse jardin"
        "Revêtement sol"     → "revetement sol"
        "CHAUFFAGE, clim"    → "chauffage clim"
    """
    normalized = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    lower = normalized.lower()
    collapsed = _NON_ALNUM_RUN.sub(" ", lower)
    return collapsed.strip()


def normalize_category(raw: Optional[str]) -> Optional[str]:
    """Map a raw free-text category string to a canonical slug or None.

    Mapping strategy (in priority order):
    1. None / empty / whitespace-only  → None  (preserve genuinely un-categorised)
    2. Exact folded match against a canonical FR label  → slug
    3. Exact folded match against a synonym key  → slug
    4. Longest synonym keyword that is a substring of the folded value  → slug
    5. No match  → "autre"

    The function is idempotent: normalize_category(slug) == slug for every
    canonical slug because each slug is registered as its own synonym key
    (underscores in slugs fold to spaces, e.g. "plomberie" → "plomberie").
    """
    if raw is None:
        return None

    folded = _fold(raw)
    if not folded:
        # Whitespace-only or empty after folding
        return None

    # Step 1: Exact match against the folded canonical FR label
    for slug, label in CANONICAL_FR.items():
        if folded == _fold(label):
            return slug

    # Step 2: Exact match against a synonym key
    exact = _SYNONYMS.get(folded)
    if exact is not None:
        return exact

    # Step 3: Substring match — longest keyword wins (deterministic tie-break)
    for keyword, slug in _SORTED_KEYWORDS:
        if keyword in folded:
            return slug

    # Step 4: Fallback
    return "autre"


def is_valid_category_slug(value: str) -> bool:
    """Return True iff value is one of the 16 canonical category slugs."""
    return value in _SLUG_SET
