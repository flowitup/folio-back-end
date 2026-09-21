"""Merchant domain allowlist + account-page URL map + the browser agent's TASK templates
for both jobs that agent runs: ``fetch_invoice`` (feature B, ``build_task``) and
``find_product`` (feature A, ``build_product_search_task`` — owner decision D16: this
same agent searches the merchants' own search pages instead of calling an external
web-search or reverse-image provider).

Hard rule 3 (plan section 0): the agent never logs in and is restricted to these
merchant domains only — ``build_allowed_domains()`` feeds ``Browser(allowed_domains=...)``
verbatim, and ``is_allowed()``/the domain list are unit-tested directly (no live browser
needed) so a regression here is caught without requiring ``BROWSER_TESTS=1``.
"""

from __future__ import annotations

from urllib.parse import quote_plus, urlparse

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import MaterialIdent

#: The 7 merchants the S0 router (``models.MERCHANTS`` — 6 of them) plus ManoMano (an
#: additional catalogue-wide fallback merchant the plan's Feature A search list also
#: names) can send a `fetch_invoice` job to. Bare registrable domains — wildcarded into
#: `*.domain` allowed_domains patterns by `build_allowed_domains()`.
MERCHANT_DOMAINS: dict[str, str] = {
    "leroymerlin": "leroymerlin.fr",
    "pointp": "pointp.fr",
    "castorama": "castorama.fr",
    "bricodepot": "bricodepot.fr",
    "gedimat": "gedimat.fr",
    "technomat": "technomat.fr",
    "manomano": "manomano.fr",
}

#: The account/"my purchases" page the agent opens first for each merchant. Filled in
#: for the two merchants the plan names explicitly; the rest open the merchant's home
#: page until the owner runs the one-time login procedure and this map is updated to
#: match (docs/assistant-merchant-login.md) — the agent can still navigate from there,
#: just with a few extra steps.
MERCHANT_ACCOUNT_URLS: dict[str, str] = {
    "leroymerlin": "https://www.leroymerlin.fr/compte/mes-achats",
    "pointp": "https://www.pointp.fr/mon-compte/mes-factures",
    # TODO(owner): fill in the real "Mes achats/factures" URL after the first manual
    # login (docs/assistant-merchant-login.md) — home page until then.
    "castorama": "https://www.castorama.fr/",
    "bricodepot": "https://www.bricodepot.fr/",
    "gedimat": "https://www.gedimat.fr/",
    "technomat": "https://www.technomat.fr/",
    "manomano": "https://www.manomano.fr/",
}

TASK_TEMPLATE_FR = (
    "Tu es déjà connecté(e) à ce site fournisseur avec un compte existant. Ne te reconnecte JAMAIS et ne "
    "modifie AUCUNE donnée du compte (pas de commande, pas de modification de profil). Trouve l'achat du "
    '{date} d\'un montant TTC de {amount} € dans les pages "Mes achats", "Mes commandes", "Mes factures" '
    'ou "Tickets", puis télécharge le PDF de la facture correspondante. Si plusieurs achats correspondent, '
    "choisis celui dont la date est la plus proche du {date} et indique-le dans ton message. Si la facture "
    'n\'est pas encore disponible au téléchargement, réponds avec le statut "not_ready" et explique pourquoi. '
    "Si le site demande une vérification, un captcha, une reconnexion ou refuse l'accès, arrête-toi "
    'immédiatement, ne tente rien d\'autre, et réponds avec le statut "blocked". Navigue lentement, comme un '
    "humain qui lit la page, pas comme un script."
)


def build_allowed_domains() -> list[str]:
    """``Browser(allowed_domains=...)`` — every merchant domain and its subdomains,
    nothing else (a task asking for e.g. google.com must fail)."""
    return [f"*.{domain}" for domain in MERCHANT_DOMAINS.values()]


def is_allowed(url: str) -> bool:
    """Mirrors what browser-use enforces internally — used by the unit test (and
    available as a defensive pre-check before the agent ever opens a URL)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in MERCHANT_DOMAINS.values())


def build_task(job: AssistantJobRecord) -> str:
    """The French TASK template filled in with this job's date/amount, prefixed with a
    direct link to the merchant's account page when we know one."""
    if job.date is None or job.amount_ttc is None:  # pragma: no cover - defensive, fetch_invoice always sets both
        raise ValueError(f"build_task requires a fetch_invoice job with date/amount_ttc set, got job {job.id}.")
    task = TASK_TEMPLATE_FR.format(date=job.date.strftime("%d/%m/%Y"), amount=f"{float(job.amount_ttc):.2f}")
    account_url = MERCHANT_ACCOUNT_URLS.get(job.merchant or "")
    if account_url:
        task = f"Va d'abord sur {account_url}. " + task
    return task


# ---------------------------------------------------------------------------
# Feature A — find_product: on-site search across the merchant catalogue (owner
# decision D16: no external web-search or reverse-image provider, this same browser
# agent does the search).
# ---------------------------------------------------------------------------

#: The on-site search URL template for merchants that expose a plain query-string
#: search. Technomat has no such URL — the agent is told to use its home page's own
#: search box instead (see ``_merchant_search_instruction``).
MERCHANT_SEARCH_URLS: dict[str, str] = {
    "leroymerlin": "https://www.leroymerlin.fr/recherche?q={query}",
    "pointp": "https://www.pointp.fr/recherche?text={query}",
    "castorama": "https://www.castorama.fr/search?term={query}",
    "bricodepot": "https://www.bricodepot.fr/recherche?q={query}",
    "gedimat": "https://www.gedimat.fr/recherche?q={query}",
    "manomano": "https://www.manomano.fr/recherche?q={query}",
}

#: Priority order the plan names for feature A's product search — the agent works
#: through these in order and stops once it has enough candidates.
PRODUCT_SEARCH_MERCHANT_ORDER: tuple[str, ...] = (
    "leroymerlin",
    "pointp",
    "castorama",
    "bricodepot",
    "gedimat",
    "technomat",
    "manomano",
)

_MERCHANT_DISPLAY_NAMES: dict[str, str] = {
    "leroymerlin": "Leroy Merlin",
    "pointp": "Point P",
    "castorama": "Castorama",
    "bricodepot": "Brico Dépôt",
    "gedimat": "Gedimat",
    "technomat": "Technomat",
    "manomano": "ManoMano",
}

#: Stop once at least this many candidates were found across every merchant visited...
PRODUCT_SEARCH_MIN_CANDIDATES = 3
#: ...or once this many merchants were visited, whichever happens first.
PRODUCT_SEARCH_MAX_MERCHANTS = 3
#: Never open more than this many product pages per merchant.
PRODUCT_SEARCH_MAX_PAGES_PER_MERCHANT = 2

PRODUCT_SEARCH_TASK_TEMPLATE_FR = (
    "Tu cherches la fiche produit d'un matériau de construction chez plusieurs fournisseurs français, dans cet "
    "ordre de priorité : {sites}. Ne te connecte JAMAIS, n'ajoute RIEN au panier et ne modifie AUCUNE donnée de "
    "compte. Voici les requêtes de recherche à utiliser, de la plus précise à la plus générale : {queries}. Pour "
    "chaque site, utilise sa recherche interne avec la requête la plus précise d'abord, ouvre au maximum "
    "{max_pages} fiches produit, et relève pour chaque fiche pertinente : titre, marque, référence, EAN, prix "
    "TTC, unité, URL de l'image et URL de la page. Arrête-toi dès que tu as trouvé au moins {min_candidates} "
    "fiches au total ou après avoir visité {max_merchants} sites, selon ce qui arrive en premier. Réponds avec "
    'le statut "done" et la liste des fiches trouvées, ou "not_found" si aucune fiche pertinente n\'a été '
    "trouvée sur aucun site. Si un site demande une connexion, un captcha ou refuse l'accès, passe simplement au "
    "site suivant sans t'arrêter ; si TOUS les sites bloquent l'accès, réponds avec le statut \"blocked\". "
    "Navigue lentement, comme un humain qui lit la page, pas comme un script."
)


def _merchant_search_instruction(key: str, query: str) -> str:
    name = _MERCHANT_DISPLAY_NAMES.get(key, key)
    url_template = MERCHANT_SEARCH_URLS.get(key)
    if url_template:
        return f"{name} ({url_template.format(query=quote_plus(query))})"
    domain = MERCHANT_DOMAINS.get(key, "")
    return f"{name} (page d'accueil https://www.{domain}/, utilise la barre de recherche du site)"


def build_product_search_task(ident: MaterialIdent, queries: list[str]) -> str:
    """The French TASK template for a ``find_product`` job — filled in with ``ident``'s
    search queries and the merchant priority order/search URLs above."""
    resolved_queries = queries or [ident.name]
    sites = " ; ".join(_merchant_search_instruction(key, resolved_queries[0]) for key in PRODUCT_SEARCH_MERCHANT_ORDER)
    return PRODUCT_SEARCH_TASK_TEMPLATE_FR.format(
        sites=sites,
        queries=" ; ".join(resolved_queries),
        max_pages=PRODUCT_SEARCH_MAX_PAGES_PER_MERCHANT,
        min_candidates=PRODUCT_SEARCH_MIN_CANDIDATES,
        max_merchants=PRODUCT_SEARCH_MAX_MERCHANTS,
    )


__all__ = [
    "MERCHANT_DOMAINS",
    "MERCHANT_ACCOUNT_URLS",
    "MERCHANT_SEARCH_URLS",
    "PRODUCT_SEARCH_MERCHANT_ORDER",
    "PRODUCT_SEARCH_MIN_CANDIDATES",
    "PRODUCT_SEARCH_MAX_MERCHANTS",
    "PRODUCT_SEARCH_MAX_PAGES_PER_MERCHANT",
    "build_allowed_domains",
    "is_allowed",
    "build_task",
    "build_product_search_task",
]
