"""Application messages use English source text and the Windows display language."""
import json
from pathlib import Path

_translations = {}


def select_language(language_tags, available):
    for tag in language_tags:
        parts = tag.replace("_", "-").lower().split("-")
        primary = parts[0]
        if primary == "zh":
            key = "zh_Hant" if any(p in ("hant", "tw", "hk", "mo") for p in parts) else "zh_Hans"
        elif primary == "sr":
            key = "sr_Latn" if "latn" in parts else "sr_Cyrl"
        elif primary == "pt":
            key = "pt_BR" if "br" in parts else "pt_PT"
        else:
            key = {"no": "nb", "tl": "fil"}.get(primary, primary)
        if key in available:
            return key
    return "en"


def set_ui_language(language_tags):
    global _translations
    directory = Path(__file__).with_name("assets") / "languages"
    catalogs = {path.stem: path for path in directory.glob("*.json")}
    language = select_language(language_tags, {"en", *catalogs})
    _translations = {} if language == "en" else json.loads(catalogs[language].read_text(encoding="utf-8"))
    return language


def tr(message):
    return _translations.get(message, message)
