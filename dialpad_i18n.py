"""Manager-only translations and per-user language preference.

Messages are addressed by stable dot-separated paths in nested JSON catalogs.
English is an independent catalog and the fallback language. Only the GUI
imports this module; translation never touches the layout model or user data.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from string import Formatter

from PySide6.QtCore import QLibraryInfo, QLocale, QSettings, QTranslator

log = logging.getLogger(__name__)
LANGUAGES = ("system", "en_US", "zh_CN", "zh_TW")
CATALOG_DIR = Path(__file__).resolve().parent / "locales"
# One identifier selects the interface and names the catalog file.
_LEGACY_PREFERENCES = {"en": "en_US"}
_KEY_PART = re.compile(r"[a-z][a-z0-9_]*")
_english = None
_catalog = {}
_language = "en_US"
_preference = "system"
_settings = None
_qt_translator = None


def tr(key, **values):
    """Resolve a message path, then interpolate opaque user values once."""
    global _english
    if not isinstance(key, str):
        raise ValueError("Translation keys must be dot-separated paths")
    parts = key.split(".")
    if len(parts) < 2 or any(_KEY_PART.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid translation key: {key!r}")
    if _english is None:
        _english = load_catalog("en_US")
    fallback = _resolve(_english, parts)
    if fallback is None:
        raise KeyError(f"Unknown translation key: {key}")
    text = _resolve(_catalog, parts)
    if text is None:
        text = fallback
    return text.format(**values) if values else text


def _resolve(catalog, parts):
    node = catalog
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def language_for(preference, system_locale):
    if preference in LANGUAGES and preference != "system":
        return preference
    parts = system_locale.replace("-", "_").split("_")
    if parts[0].lower() != "zh":
        return "en_US"
    return "zh_TW" if any(part.lower() in ("hant", "tw", "hk", "mo") for part in parts[1:]) else "zh_CN"


def manager_settings():
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                     "asus-dialpad-driver", "layout-manager")


def _fields(template):
    return {name for _literal, name, _format, _conversion in Formatter().parse(template) if name is not None}


def load_catalog(language, directory=CATALOG_DIR):
    """Read one nested catalog; dotted names belong in calls, not JSON keys."""
    if language not in LANGUAGES or language == "system":
        raise ValueError(f"Unsupported catalog language: {language!r}")
    path = Path(directory) / f"{language}.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))

    def validate(node, prefix):
        if not isinstance(node, dict):
            raise ValueError(f"Expected a translation object at {prefix or '<root>'}")
        for name, value in node.items():
            if _KEY_PART.fullmatch(name) is None:
                raise ValueError(f"Invalid translation path segment: {name!r}")
            key = f"{prefix}.{name}" if prefix else name
            if isinstance(value, dict):
                validate(value, key)
            elif isinstance(value, str) and value and prefix:
                _fields(value)
            else:
                raise ValueError(f"Expected a nonempty message at a nested path: {key}")

    validate(catalog, "")
    return catalog


def _check_translations(catalog, english, prefix=""):
    """Ignore incompatible translated leaves so their English value remains usable."""
    for name in tuple(catalog):
        value, reference = catalog[name], english.get(name)
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict) and isinstance(reference, dict):
            _check_translations(value, reference, key)
        elif isinstance(value, str) and isinstance(reference, str) and _fields(value) == _fields(reference):
            continue
        else:
            log.warning("Ignoring translation with unknown path or incompatible placeholders: %s", key)
            del catalog[name]


def initialize_i18n(app, *, settings=None, preference=None, system_locale=None, catalog_dir=CATALOG_DIR):
    """Call before constructing a window; a preference change never rebuilds it."""
    global _english, _catalog, _language, _preference, _settings, _qt_translator
    _settings = settings if settings is not None else manager_settings()
    selected = preference if preference is not None else _settings.value("ui/language", "system")
    # Installations predating the catalog-aligned identifier keep their explicit English choice.
    selected = _LEGACY_PREFERENCES.get(selected, selected)
    _preference = selected if selected in LANGUAGES else "system"
    _language = language_for(_preference, system_locale or QLocale.system().bcp47Name())
    # English is required: missing packaging must not silently display message IDs.
    _english = load_catalog("en_US", catalog_dir)
    _catalog = {}
    if _language != "en_US":
        try:
            _catalog = load_catalog(_language, catalog_dir)
            _check_translations(_catalog, _english)
        except (OSError, ValueError) as exc:
            log.warning("Cannot load %s translations; using English: %s", _language, exc)
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if _language != "en_US":
        translator = QTranslator(app)
        if translator.load("qtbase_" + _language, QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
            _qt_translator = translator
        else:
            log.warning("Qt standard-dialog translation is unavailable for %s", _language)
    return _language


def language_preference():
    return _preference


def current_language():
    return _language


def save_language_preference(preference):
    """Persist for the next launch, without changing the active translations."""
    global _preference
    if preference not in LANGUAGES:
        raise ValueError("Unsupported interface language")
    settings = _settings if _settings is not None else manager_settings()
    settings.setValue("ui/language", preference)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError(f"Cannot save manager language preference: {settings.fileName()}")
    _preference = preference
