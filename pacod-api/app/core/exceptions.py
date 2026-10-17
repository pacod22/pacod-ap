"""Domain-specific exceptions.

Each exception maps cleanly to a user-facing error code. The API layer
translates these into bilingual error messages for the client.
"""

from __future__ import annotations

_USER_MESSAGES: dict[str, dict[str, str]] = {
    "authentication_error": {
        "bg": "Невалиден достъп. Моля, влезте отново в профила си.",
        "en": "Authentication failed. Please sign in again.",
        "de": "Anmeldung fehlgeschlagen. Bitte melden Sie sich erneut an.",
        "ru": "Не удалось выполнить вход. Пожалуйста, войдите снова.",
    },
    "unsupported_language": {
        "bg": "Асистентът поддържа въпроси само на **български** или **английски** език.",
        "en": "This assistant supports questions in **Bulgarian** or **English** only.",
    },
    "profile_forbidden": {
        "bg": (
            "Нямате достъп до този асистент. "
            "Моля, изберете друг асистент или се свържете с администратора."
        ),
        "en": (
            "You do not have access to this assistant. "
            "Please choose another assistant or contact the administrator."
        ),
        "de": (
            "Sie haben keinen Zugriff auf diesen Assistenten. "
            "Bitte wählen Sie einen anderen Assistenten oder wenden Sie sich an den Administrator."
        ),
        "ru": (
            "У вас нет доступа к этому ассистенту. "
            "Пожалуйста, выберите другого ассистента или обратитесь к администратору."
        ),
    },
    "quota_exceeded": {
        "bg": "Достигнахте дневния лимит на запитвания. Моля, опитайте отново утре.",
        "en": "You have reached your daily request limit. Please try again tomorrow.",
        "de": (
            "Sie haben Ihr tägliches Anfragelimit erreicht. "
            "Bitte versuchen Sie es morgen erneut."
        ),
        "ru": "Вы исчерпали дневной лимит запросов. Пожалуйста, попробуйте завтра.",
    },
    "rate_limit": {
        "bg": "Твърде много запитвания. Моля, изчакайте малко и опитайте отново.",
        "en": "Too many requests. Please wait a moment and try again.",
        "de": "Zu viele Anfragen. Bitte warten Sie einen Moment und versuchen Sie es erneut.",
        "ru": "Слишком много запросов. Пожалуйста, подождите немного и попробуйте снова.",
    },
    "upstream_error": {
        "bg": "Възникна временен проблем. Моля, опитайте отново след няколко секунди.",
        "en": "A temporary issue occurred. Please try again in a few seconds.",
        "de": (
            "Ein vorübergehendes Problem ist aufgetreten. "
            "Bitte versuchen Sie es in ein paar Sekunden erneut."
        ),
        "ru": "Произошла временная ошибка. Пожалуйста, повторите попытку через несколько секунд.",
    },
    "empty_response": {
        "bg": (
            "Моделът не върна отговор. Това може да се случи с някои модели "
            "(напр. „разсъждаващи“ модели). Моля, опитайте отново или изберете друг модел."
        ),
        "en": (
            "The model returned no answer. This can happen with some models "
            "(e.g. reasoning models). Please try again or select a different model."
        ),
    },
}

# Profile-scoped messages: the agent's own scope phrase (the topic-guard node's
# ``scope_phrase_*`` config) is filled into the off-topic message; an agent without
# one gets the domain-neutral wording.
_OFF_TOPIC = {
    "bg": (
        "Асистентът отговаря само на въпроси, свързани с **{scope}**. "
        "Моля, задайте въпрос по тази тема."
    ),
    "en": (
        "This assistant only answers questions about **{scope}**. "
        "Please ask a question on that topic."
    ),
}
# No scope phrase configured for the agent.
_OFF_TOPIC_GENERIC = {
    "bg": (
        "Въпросът е извън тематичната област на асистента. "
        "Моля, задайте въпрос по неговата тема."
    ),
    "en": (
        "This question is outside the assistant's subject area. "
        "Please ask a question on its topic."
    ),
}

_FORBIDDEN_GENERIC = {
    "bg": (
        "Асистентът **не може да помогне** с искания, свързани с "
        "измами, деклариране на неверни данни, заобикаляне на закона "
        "или друго незаконно действие. "
        "Ако имате въпроси относно законосъобразни действия по темата, "
        "с удоволствие ще помогна."
    ),
    "en": (
        "This assistant **cannot help** with requests involving "
        "fraud, false declarations, circumventing the law, "
        "or any other illegal activity. "
        "If you have questions about lawful options on this topic, I am happy to assist."
    ),
}

_FALLBACK = {
    "bg": "Възникна грешка. Моля, опитайте отново.",
    "en": "An error occurred. Please try again.",
}


def _pick(payload: dict[str, str], lang: str) -> str:
    # Most messages are BG/EN only (the chat languages); any other caller (a DE/RU
    # UI) gets the platform's default language, then English.
    from app.core.i18n import default_language

    return payload.get(lang) or payload.get(default_language().value) or payload["en"]


def user_message_md(code: str, language=None, profile=None) -> str:
    """Return a user-facing Markdown error message in the requested language.

    `language` can be a Language enum value, any object with a `.value` str
    attribute, or a plain code string (``"de"``).  Defaults to the platform's
    default language (``PLATFORM_DEFAULT_LANGUAGE``) when language is unknown.

    `profile` (a ``ProfileContext``, or anything with ``scope_phrase_bg/en``)
    scopes the ``off_topic`` wording to that agent's subject; without a scope
    phrase the domain-neutral wording is used.
    """
    from app.core.i18n import default_language

    lang = getattr(language, "value", language) or default_language().value
    if code == "off_topic":
        # Scope phrase: the agent's own config (agent-editor topic-guard node),
        # else the generic wording.
        own = {
            k: v
            for k in ("bg", "en")
            if (v := (getattr(profile, f"scope_phrase_{k}", "") or "").strip())
        }
        if own:  # one language given → use it for both
            own = {"bg": own.get("bg") or own["en"], "en": own.get("en") or own["bg"]}
        if not own:
            return _pick(_OFF_TOPIC_GENERIC, lang)
        return _pick(_OFF_TOPIC, lang).format(scope=_pick(own, lang))
    if code == "forbidden_request":
        return _pick(_FORBIDDEN_GENERIC, lang)
    return _pick(_USER_MESSAGES.get(code, _FALLBACK), lang)


class AgentError(Exception):
    """Base exception. All app errors inherit from this."""

    code: str = "agent_error"
    http_status: int = 500

    def __init__(self, message: str = "", **context):
        super().__init__(message)
        self.context = context


class AuthenticationError(AgentError):
    code = "authentication_error"
    http_status = 401


class UnsupportedLanguageError(AgentError):
    code = "unsupported_language"
    http_status = 400


class OffTopicError(AgentError):
    code = "off_topic"
    http_status = 400


class ForbiddenRequestError(AgentError):
    code = "forbidden_request"
    http_status = 403


class QuotaExceededError(AgentError):
    code = "quota_exceeded"
    http_status = 429


class RateLimitError(AgentError):
    code = "rate_limit"
    http_status = 429


class UpstreamError(AgentError):
    """An LLM call through the gateway failed.

    A plain ``UpstreamError`` (a 429, a 5xx, a lost connection) is safe to send
    again; the subclasses below are not. Every retry layer — the shared client's
    tenacity, ``_open_stream`` and the improvement loop's backoff — asks
    ``retryable`` and nothing else. ``retry_after`` carries the gateway's
    ``Retry-After`` (seconds) when it gave one.
    """

    code = "upstream_error"
    http_status = 502
    retryable = True

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamTimeoutError(UpstreamError):
    """An LLM request timed out: on the client's clock, or the gateway's (its 504
    ``upstream_timeout``, also as a mid-stream error event).

    The provider does not stop generating when the client hangs up: the answer
    is finished and billed anyway. So a timed-out request is NEVER retried — by
    the shared client, by the improvement loop, or by the gateway (the client
    sends ``x-gw-retries: 1``) — a retry would pay for a second copy of an answer
    nobody received. Same user-facing code and status as its parent.
    """

    retryable = False


class UpstreamQuotaError(UpstreamError):
    """The gateway refused the call for budget: a limit is used up (402
    ``quota_exceeded``, e.g. a worker key's spend cap) or the provider account
    is out of credit (402 ``upstream_quota_exhausted``). Sending it again
    changes nothing until the window resets, so it is not retried."""

    retryable = False


class UpstreamRequestError(UpstreamError):
    """The gateway refused the request itself (a 4xx other than 402/429: an
    unknown or revoked key, a model this key may not use, a malformed request).
    The same request fails the same way again, so it is not retried."""

    retryable = False


class IdentityUnavailableError(AgentError):
    """pacod-id could not be asked (its keys, an introspection, or a copy of its feed too
    old for an admin decision): 503, not 401 — the caller's token may well be fine."""

    code = "identity_unavailable"
    http_status = 503


class ConfigurationError(AgentError):
    code = "configuration_error"
    http_status = 500
