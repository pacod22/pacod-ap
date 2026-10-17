"""The ``taxes`` example agent — a Bulgarian tax & accounting consultant.

Installed on demand (``make seed-example PROFILE=taxes``), not at boot. It shows
a fully configured domain agent: its own prompts (``taxes_prompts``), reference
facts (rates, thresholds, deadlines — ``data/profiles/taxes``), a knowledge base
of Bulgarian law, an allow-listed web search and the Bulgarian PII redaction
pack for the review process.
"""

from __future__ import annotations

from scripts.profiles.taxes_prompts import PROMPT_OVERRIDES
from scripts.seed_profile import ProfileSpec

NAME = "Tax & Accounting (Bulgaria)"
DESCRIPTION = (
    "Example agent: accounting and tax consultation in Bulgaria for individuals, "
    "the self-employed and companies. Answers in Bulgarian and English."
)

TOPIC_DESCRIPTION_BG = (
    "Счетоводство и данъчни консултации в България за физически лица, "
    "самоосигуряващи се лица и юридически лица (фирми, ЕООД, ООД, АД): "
    "ДДФЛ, корпоративен данък, ДДС, социални осигуровки, патентен данък, "
    "местни данъци и такси (включително данък върху наследство, данък при "
    "придобиване на имущество, данък върху недвижимите имоти, данък върху "
    "превозните средства), наем от физическо или юридическо лице, "
    "дивиденти, трансфертно ценообразуване, и трансгранично данъчно "
    "облагане когато България е една от юрисдикциите."
)
TOPIC_DESCRIPTION_EN = (
    "Accounting and tax consultation in Bulgaria for individuals, "
    "self-employed persons, and legal entities (companies: EOOD, OOD, AD): "
    "personal income tax, corporate income tax, VAT, social security "
    "contributions, patent tax, local taxes and fees (including inheritance "
    "tax, property acquisition tax, real estate tax, vehicle tax), rental "
    "income from individuals or companies, dividends, transfer pricing, and "
    "cross-border taxation where Bulgaria is one of the jurisdictions."
)

# Official Bulgarian sources only: the web search allow-list of this agent.
WEB_SEARCH_ALLOWED_DOMAINS = (
    "nra.bg,nssi.bg,noi.bg,mlsp.government.bg,minfin.bg,"
    "lex.bg,parliament.bg,eur-lex.europa.eu,egov.bg"
)

# The topic guard's keyword filter, this domain's part (the built-in phrases cover fraud
# with fake documents, laundering and bribery): tax evasion, concealment from the revenue
# agency (НАП), false declarations. Case-insensitive substrings.
ILLEGAL_PHRASES = (
    "укрив,укрия,скрия данъ,не декларирам,да не ме разкрие,да не ме открие,да не ме хване,"
    "нап да не,без да знае нап,без знанието на нап,фиктивн,данъчна измама,невярна декларация,"
    "неверни данни в декларация,tax evasion,evade tax,evade the tax,hide income,"
    "hide my income,hide the income,hide revenue,conceal income,conceal revenue,"
    "fictitious expense,false declaration,false tax return"
)

SPEC = ProfileSpec(
    profile_id="taxes",
    slug="taxes",
    name=NAME,
    description=DESCRIPTION,
    topic_name="accounting_bg",
    topic_description_bg=TOPIC_DESCRIPTION_BG,
    topic_description_en=TOPIC_DESCRIPTION_EN,
    legal_year=2026,
    languages="bg,en",
    short_names={
        "short_name_en": "Taxes",
        "short_name_bg": "Данъци",
        "short_name_de": "Steuern",
        "short_name_ru": "Налоги",
    },
    prompt_overrides=dict(PROMPT_OVERRIDES),
    settings_overrides={
        "websearch.enabled": "true",
        "websearch.allowed_domains": WEB_SEARCH_ALLOWED_DOMAINS,
        "loop.redaction_packs": "generic,bg",
        "guard.illegal_phrases": ILLEGAL_PHRASES,
    },
    assign_to_all_users=True,
)
