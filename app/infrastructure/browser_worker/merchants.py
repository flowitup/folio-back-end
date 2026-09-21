"""Merchant domain allowlist + account-page URL map + the browser agent's TASK template
(plan section 4 "Feature B", ``BrowserProfile``/TASK paragraph).

Hard rule 3 (plan section 0): the agent never logs in and is restricted to these
merchant domains only — ``build_allowed_domains()`` feeds ``Browser(allowed_domains=...)``
verbatim, and ``is_allowed()``/the domain list are unit-tested directly (no live browser
needed) so a regression here is caught without requiring ``BROWSER_TESTS=1``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.application.assistant.jobs_repo import AssistantJobRecord

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
    task = TASK_TEMPLATE_FR.format(date=job.date.strftime("%d/%m/%Y"), amount=f"{float(job.amount_ttc):.2f}")
    account_url = MERCHANT_ACCOUNT_URLS.get(job.merchant)
    if account_url:
        task = f"Va d'abord sur {account_url}. " + task
    return task


__all__ = ["MERCHANT_DOMAINS", "MERCHANT_ACCOUNT_URLS", "build_allowed_domains", "is_allowed", "build_task"]
