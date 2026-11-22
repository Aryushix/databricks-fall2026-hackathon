"""
spoken.py

Care Finder — Spoken Reply Templates

Builds the short sentence the app reads
aloud after each response.

Text comes only from fixed templates and
structured response fields. No language
model writes these sentences, so nothing
is spoken that the orchestrator did not
return.

No external packages required.
"""

from typing import Optional

from schemas import (
    OrchestratorResponse,
    ResponseStatus,
    TrustLabel,
)

# ==========================================
# 1. LANGUAGE SELECTION
# ==========================================

# Languages with templates. Others fall
# back to Hindi, the app's first language.
SUPPORTED = ("hi", "en")


def pick_language(*candidates: Optional[str]) -> str:
    """
    Return the first supported language
    among the candidates ("hi-IN" -> "hi").
    """

    for code in candidates:
        if not code:
            continue
        base = code.split("-")[0].lower()
        if base in SUPPORTED:
            return base

    return "hi"


# ==========================================
# 2. VOCABULARY
# ==========================================

SERVICE_NAMES = {
    "dialysis": {
        "hi": "डायलिसिस",
        "en": "dialysis",
    },
    "emergency c-section": {
        "hi": "इमरजेंसी सिज़ेरियन ऑपरेशन",
        "en": "an emergency C-section",
    },
    "sick newborn care": {
        "hi": "बीमार नवजात शिशु की देखभाल",
        "en": "sick newborn care",
    },
    "trauma": {
        "hi": "गंभीर चोट का इलाज",
        "en": "trauma care",
    },
    "burn care": {
        "hi": "जलने का इलाज",
        "en": "burn care",
    },
    "mri": {
        "hi": "एमआरआई",
        "en": "an MRI",
    },
}

TRUST_PHRASES = {
    TrustLabel.CONFIRMED: {
        "hi": "यह सेवा पक्की है",
        "en": "This service is confirmed",
    },
    TrustLabel.LIKELY: {
        "hi": "यह सेवा मिलने की संभावना है",
        "en": "This service is likely offered",
    },
    TrustLabel.CLAIMED_ONLY: {
        "hi": "इस सेवा का सिर्फ़ दावा है, पक्का नहीं",
        "en": "This service is only claimed, not confirmed",
    },
    TrustLabel.CONFLICTING_INFO: {
        "hi": "इसकी जानकारी में विरोधाभास है",
        "en": "Its records conflict",
    },
}

CALL_AHEAD = {
    "hi": "जाने से पहले फ़ोन करके पक्का करें।",
    "en": "Call ahead before you go.",
}

EMERGENCY = {
    "hi": "यह आपातकाल लगता है। अभी 108 पर कॉल करें।",
    "en": "This sounds like an emergency. Call 108 now.",
}


def service_name(service: Optional[str], lang: str) -> str:

    if not service:
        return ""

    names = SERVICE_NAMES.get(service.lower())

    return names[lang] if names else service


# ==========================================
# 3. SPOKEN REPLY
# ==========================================

DEMO = {
    "hi": "यह डेमो डेटा है, असली नहीं।",
    "en": "Demo data, not real.",
}


def spoken_text(
    response: OrchestratorResponse,
    lang: str,
    emergency: bool = False,
    demo: bool = False,
) -> str:
    """
    One or two short sentences for the
    response, in `lang` ("hi" or "en").

    `demo` prefixes results with a spoken
    warning, because _spoken_name() drops
    "(Fictional)" from facility names.
    """

    lang = pick_language(lang)

    text = _reply(response, lang)

    if demo and response.status == ResponseStatus.RESULTS:
        text = f"{DEMO[lang]} {text}"

    if emergency:
        text = f"{EMERGENCY[lang]} {text}"

    return text.strip()


def _reply(
    response: OrchestratorResponse,
    lang: str,
) -> str:

    status = response.status

    if status == ResponseStatus.CONFIRMATION_REQUIRED:

        c = response.confirmation
        service = service_name(c.service, lang)

        if lang == "hi":
            return f"{service}, {c.location} के पास। क्या यह सही है?"

        return f"{service[:1].upper()}{service[1:]} near {c.location}. Is that right?"

    if status == ResponseStatus.RESULTS and response.facilities:

        top = response.facilities[0]
        name = _spoken_name(top.name)
        trust = TRUST_PHRASES.get(top.trust_label)
        km = (
            round(top.distance_km)
            if top.distance_km is not None
            else None
        )

        if lang == "hi":
            first = f"सबसे अच्छा विकल्प: {name}"
            if km is not None:
                first += f", लगभग {km} किलोमीटर दूर"
            sentences = [first + "।"]
            if trust:
                sentences.append(trust["hi"] + "।")
        else:
            first = f"Best option: {name}"
            if km is not None:
                first += f", about {km} kilometres away"
            sentences = [first + "."]
            if trust:
                sentences.append(trust["en"] + ".")

        sentences.append(CALL_AHEAD[lang])

        return " ".join(sentences)

    if status == ResponseStatus.NO_RESULTS:

        if lang == "hi":
            return (
                "इस खोज के लिए पक्के सबूत वाली कोई "
                "जगह नहीं मिली। आपातकाल में 108 पर "
                "कॉल करें।"
            )

        return (
            "No place with enough evidence was "
            "found for this search. In an "
            "emergency, call 108."
        )

    if status == ResponseStatus.CLARIFICATION_REQUIRED:
        return _clarify(response, lang)

    if status == ResponseStatus.RESET:

        if lang == "hi":
            return "खोज साफ़ हो गई। कौन सा इलाज चाहिए?"

        return "Search cleared. Which service do you need?"

    if status == ResponseStatus.ASSISTANCE_REQUESTED:

        if lang == "hi":
            return (
                "डेमो: मदद का अनुरोध दर्ज हुआ, पर अभी "
                "किसी व्यक्ति से संपर्क नहीं किया गया।"
            )

        return response.message

    if lang == "hi":
        return "कुछ गड़बड़ हुई। कृपया फिर से कोशिश करें।"

    return "Something went wrong. Please try again."


def _clarify(
    response: OrchestratorResponse,
    lang: str,
) -> str:

    if lang == "en":
        return response.message

    missing = response.missing_fields

    if "service" in missing and "location" in missing:
        return "कौन सा इलाज चाहिए, और किस जगह के पास?"

    if "service" in missing:
        return "कौन सा इलाज चाहिए?"

    if "location" in missing:
        return "किस शहर, ज़िले या पिन कोड के पास खोजें?"

    if response.message.startswith("What should I change"):
        return "क्या बदलना है, इलाज या जगह?"

    return "कृपया फिर से बताइए, आपको क्या चाहिए?"


def _spoken_name(name: str) -> str:
    """
    Drop bracketed notes such as
    "(Fictional)" that read badly aloud.
    The screen still shows the full name.
    """

    return name.split(" (")[0].strip()
