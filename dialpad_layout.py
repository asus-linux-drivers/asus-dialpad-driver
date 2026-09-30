"""Portable, executable-code-free DialPad layout data and storage.

The external document is retained separately from the normalized runtime view so
editing never silently changes ordering, scalar/list shapes, or omitted fields.
Linux activation and explicitly trusted Python execution are lazy CLI operations.
"""

from __future__ import annotations

import argparse
import ast
import configparser
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


GEOMETRY_FIELDS = (
    "top_right_icon_width",
    "top_right_icon_height",
    "circle_diameter",
    "center_button_diameter",
    "circle_center_x",
    "circle_center_y",
)
DIRECTIONS = ("center", "clockwise", "counterclockwise")
_METADATA_FIELDS = {"title", "icon", "icons", "unit", "value", "treshold"}
_ACTION_FIELDS = _METADATA_FIELDS | {"key", "command", "modifier", "trigger", "duration"}
_FUNCTION_FIELDS = _METADATA_FIELDS | set(DIRECTIONS) | {"command"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_MODULE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Issue:
    severity: str
    path: str
    message: str


class ValidationError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = list(issues)
        super().__init__("\n".join(f"{issue.path}: {issue.message}" for issue in self.issues))


@dataclass(frozen=True)
class Metadata:
    title: str | None = None
    icon: str | None = None
    unit: str | None = None
    value_query: str | None = None
    icons: dict[str, str] = field(default_factory=dict)
    treshold: float | None = None


@dataclass(frozen=True)
class Action:
    kind: str
    keys: tuple[str, ...] = ()
    values: tuple[int, ...] = ()
    command: str | None = None
    modifier: str | None = None
    trigger: str = "release"
    duration: float = 0.0
    metadata: Metadata = field(default_factory=Metadata)


@dataclass(frozen=True)
class Function:
    actions: dict[str, tuple[Action, ...]]
    metadata: Metadata = field(default_factory=Metadata)
    command: str | None = None


@dataclass(frozen=True)
class Profile:
    actions: dict[str, tuple[Action, ...]]
    functions: dict[str, Function]


@dataclass(frozen=True)
class Layout:
    document: dict
    geometry: dict
    name: str | None
    profiles: dict[str, Profile]


@dataclass(frozen=True)
class LayoutSource:
    identifier: str
    path: Path
    format: str
    provenance: str
    builtin: bool
    overrides: tuple[Path, ...] = ()


def _field(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def _error(path: str, message: str) -> ValidationError:
    return ValidationError([Issue("error", path, message)])


def _copy_json(value: Any, path: str, issues: list[Issue], parents: set[int]) -> Any:
    """Copy only JSON data, retaining order and identifying bad leaf values."""
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            issues.append(Issue("error", path, "number must be finite"))
        return value
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in parents:
            issues.append(Issue("error", path, "cyclic containers are not JSON data"))
            return None
        parents.add(identity)
        try:
            if isinstance(value, list):
                return [_copy_json(item, f"{path}[{index}]", issues, parents)
                        for index, item in enumerate(value)]
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    issues.append(Issue("error", path, "object keys must be strings"))
                    continue
                result[key] = _copy_json(item, _field(path, key), issues, parents)
            return result
        finally:
            parents.remove(identity)
    issues.append(Issue("error", path, f"unsupported JSON value type: {type(value).__name__}"))
    return None


@lru_cache(maxsize=1)
def event_names() -> tuple[str, ...]:
    """Return the shipped UAPI symbol catalog without importing Linux libraries."""
    path = _MODULE_DIR / "dialpad_events.json"
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        names = catalog["events"]
        if (catalog["schema_version"] != 1 or not isinstance(names, list)
                or any(not isinstance(name, str)
                       or not re.fullmatch(r"(?:KEY|BTN|REL)_[A-Z0-9_]+", name)
                       for name in names)
                or len(names) != len(set(names)) or not names):
            raise ValueError("invalid event catalog")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid installed event catalog {path}: {exc}") from exc
    return tuple(names)


@lru_cache(maxsize=1)
def _event_set() -> frozenset[str]:
    return frozenset(event_names())


class _Validator:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, path: str, message: str) -> None:
        self.issues.append(Issue("error", path, message))

    def object(self, value: Any, path: str) -> dict:
        if not isinstance(value, dict):
            self.error(path, "expected an object")
            return {}
        return value

    def fields(self, value: dict, allowed: set[str], path: str) -> None:
        for key in value:
            if key not in allowed:
                self.error(_field(path, key), "unsupported field")

    def string(self, value: dict, key: str, path: str) -> str | None:
        if key not in value:
            return None
        if not isinstance(value[key], str):
            self.error(_field(path, key), "expected a string")
            return None
        return value[key]

    def number(self, value: Any, path: str, *, positive: bool = False,
               nonnegative: bool = False) -> float:
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            self.error(path, "expected a finite number")
            return 0.0
        if positive and value <= 0:
            self.error(path, "must be greater than zero")
        elif nonnegative and value < 0:
            self.error(path, "must not be negative")
        return float(value)

    def event(self, value: Any, path: str, *, modifier: bool = False) -> str | None:
        if not isinstance(value, str):
            self.error(path, "expected an event-name string")
            return None
        if value not in _event_set():
            self.error(path, f"unresolved event name {value!r}; character/keysym conversion is not supported")
            return None
        if modifier and value.startswith("REL_"):
            self.error(path, "a modifier must be a KEY_ or BTN_ event")
            return None
        return value

    def metadata(self, value: dict, path: str, *, relative: bool = False) -> Metadata:
        title = self.string(value, "title", path)
        icon = self.string(value, "icon", path)
        unit = self.string(value, "unit", path)
        icons = {}
        if "icons" in value:
            for label, icon_path in self.object(value["icons"], _field(path, "icons")).items():
                if not isinstance(icon_path, str):
                    self.error(_field(_field(path, "icons"), label), "expected an icon-path string")
                else:
                    icons[label] = icon_path
        query = None
        if "value" in value and not relative:
            query = self.string(value, "value", path)
        threshold = None
        if "treshold" in value:
            threshold = self.number(value["treshold"], _field(path, "treshold"), positive=True)
        return Metadata(title, icon, unit, query, icons, threshold)

    def action(self, value: Any, path: str) -> Action:
        value = self.object(value, path)
        self.fields(value, _ACTION_FIELDS, path)
        keys: list[str] = []
        if "key" in value:
            key = value["key"]
            if isinstance(key, list):
                if not key:
                    self.error(_field(path, "key"), "an event combination must not be empty")
                for index, name in enumerate(key):
                    event = self.event(name, f"{_field(path, 'key')}[{index}]")
                    if event is not None:
                        keys.append(event)
            else:
                event = self.event(key, _field(path, "key"))
                if event is not None:
                    keys.append(event)
        relative = bool(keys) and all(key.startswith("REL_") for key in keys)
        if any(key.startswith("REL_") for key in keys) and not relative:
            self.error(_field(path, "key"), "relative events cannot be mixed with key/button events")
        values: list[int] = []
        if relative:
            raw_values = value.get("value")
            if not isinstance(raw_values, list):
                self.error(_field(path, "value"), "relative events require an integer value array")
            else:
                if len(raw_values) != len(keys):
                    self.error(_field(path, "value"), "relative event and value counts must match")
                for index, number in enumerate(raw_values):
                    if type(number) is not int or not -(2 ** 31) <= number < 2 ** 31:
                        self.error(f"{_field(path, 'value')}[{index}]", "expected a signed 32-bit integer event value")
                    else:
                        values.append(number)
        command = self.string(value, "command", path)
        modifier = None
        if "modifier" in value:
            modifier = self.event(value["modifier"], _field(path, "modifier"), modifier=True)
        trigger = value.get("trigger", "release")
        if not isinstance(trigger, str) or trigger not in ("immediate", "release"):
            self.error(_field(path, "trigger"), "expected 'immediate' or 'release'")
            trigger = "release"
        duration = self.number(value.get("duration", 0), _field(path, "duration"), nonnegative=True)
        metadata = self.metadata(value, path, relative=relative)
        kind = "relative" if relative else "keys" if keys else "command" if command is not None else "control"
        return Action(kind, tuple(keys), tuple(values), command, modifier, trigger, duration, metadata)

    def actions(self, value: Any, path: str) -> tuple[Action, ...]:
        if isinstance(value, dict):
            return (self.action(value, path),)
        if not isinstance(value, list):
            self.error(path, "expected an action object or an ordered array of actions")
            return ()
        return tuple(self.action(action, f"{path}[{index}]") for index, action in enumerate(value))

    def function(self, value: Any, path: str) -> Function:
        value = self.object(value, path)
        self.fields(value, _FUNCTION_FIELDS, path)
        actions = {key: self.actions(item, _field(path, key))
                   for key, item in value.items() if key in DIRECTIONS}
        return Function(actions, self.metadata(value, path), self.string(value, "command", path))

    def profile(self, value: Any, path: str) -> Profile:
        value = self.object(value, path)
        actions = {}
        functions = {}
        for key, item in value.items():
            if key in DIRECTIONS:
                actions[key] = self.actions(item, _field(path, key))
            else:
                functions[key] = self.function(item, _field(path, key))
        return Profile(actions, functions)


def normalize_document(document: dict) -> Layout:
    """Validate v1 and build a normalized view, without changing the document."""
    validator = _Validator()
    try:
        copied = _copy_json(document, "$", validator.issues, set())
    except RecursionError as exc:
        raise _error("$", "document nesting is too deep") from exc
    if validator.issues:
        raise ValidationError(validator.issues)
    copied = validator.object(copied, "$")
    validator.fields(copied, {"schema_version", "name", "geometry", "app_shortcuts"}, "$")
    if type(copied.get("schema_version")) is not int or copied.get("schema_version") != 1:
        validator.error("$.schema_version", "expected supported schema version 1")
    name = validator.string(copied, "name", "$")
    geometry = validator.object(copied.get("geometry"), "$.geometry")
    validator.fields(geometry, set(GEOMETRY_FIELDS), "$.geometry")
    numbers = {}
    for key in GEOMETRY_FIELDS:
        numbers[key] = validator.number(geometry.get(key), _field("$.geometry", key),
                                        positive=key not in ("circle_center_x", "circle_center_y"))
    if numbers["center_button_diameter"] > numbers["circle_diameter"]:
        validator.error("$.geometry.center_button_diameter", "must not exceed circle_diameter")
    mappings = validator.object(copied.get("app_shortcuts"), "$.app_shortcuts")
    if "none" not in mappings:
        validator.error("$.app_shortcuts.none", "required fallback mapping is missing")
    profiles = {key: validator.profile(value, _field("$.app_shortcuts", key))
                for key, value in mappings.items()}
    if validator.issues:
        raise ValidationError(validator.issues)
    return Layout(copied, geometry, name, profiles)


class _Pairs(list):
    """Keep JSON object pairs until their full field path is available."""


def _unpack_pairs(value: Any, path: str, issues: list[Issue]) -> Any:
    if isinstance(value, _Pairs):
        result = {}
        for key, item in value:
            item_path = _field(path, key)
            if key in result:
                issues.append(Issue("error", item_path, "duplicate object key"))
            result[key] = _unpack_pairs(item, item_path, issues)
        return result
    if isinstance(value, list):
        return [_unpack_pairs(item, f"{path}[{index}]", issues) for index, item in enumerate(value)]
    return value


def parse_json(data: str | bytes) -> Layout:
    try:
        pairs = json.loads(data, object_pairs_hook=_Pairs)
        issues: list[Issue] = []
        document = _unpack_pairs(pairs, "$", issues)
    except (ValueError, RecursionError) as exc:
        raise _error("$", f"invalid JSON: {exc}") from exc
    if issues:
        raise ValidationError(issues)
    return normalize_document(document)


class _StaticPython:
    def __init__(self, filename: str):
        self.filename = filename
        self.event_types: dict[str, str] = {}
        self.modules: set[str] = set()

    def unsupported(self, node: ast.AST, message: str = "unsupported executable Python") -> None:
        line = getattr(node, "lineno", 1)
        raise _error(f"{self.filename}:{line}", message + "; use explicit trusted conversion only if you trust this source")

    def literal(self, node: ast.AST, path: str) -> Any:
        if isinstance(node, ast.Constant):
            if type(node.value) in (str, int, float, bool) or node.value is None:
                return node.value
        elif isinstance(node, ast.List):
            return [self.literal(item, f"{path}[{index}]") for index, item in enumerate(node.elts)]
        elif isinstance(node, ast.Dict):
            result = {}
            for key_node, value_node in zip(node.keys, node.values):
                if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                    self.unsupported(node, "dictionary keys must be literal strings; unpacking is not supported")
                key = key_node.value
                item_path = _field(path, key)
                if key in result:
                    raise _error(item_path, "duplicate object key in Python source")
                result[key] = self.literal(value_node, item_path)
            return result
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            if isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float):
                return -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
        elif isinstance(node, ast.Attribute):
            family = None
            if isinstance(node.value, ast.Name):
                family = self.event_types.get(node.value.id)
            elif (isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)
                  and node.value.value.id in self.modules and node.value.attr in ("EV_KEY", "EV_REL")):
                family = node.value.attr
            if family is not None:
                prefixes = ("KEY_", "BTN_") if family == "EV_KEY" else ("REL_",)
                if not node.attr.startswith(prefixes):
                    raise _error(path, f"{node.attr!r} is not an event in {family}")
                return node.attr
        self.unsupported(node, f"unsupported non-literal value at {path}")

    def parse(self, data: str | bytes) -> Layout:
        try:
            tree = ast.parse(data, filename=self.filename)
        except (SyntaxError, UnicodeError, ValueError, RecursionError) as exc:
            raise _error(self.filename, f"invalid Python source: {exc}") from exc
        assignments = {}
        supported = set(GEOMETRY_FIELDS) | {"name", "app_shortcuts"}
        for index, node in enumerate(tree.body):
            if (index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                continue
            if isinstance(node, ast.ImportFrom):
                if node.level or node.module != "libevdev":
                    self.unsupported(node, "only static libevdev event imports are supported")
                for alias in node.names:
                    if alias.name not in ("EV_KEY", "EV_REL"):
                        self.unsupported(node, "only EV_KEY and EV_REL event imports are supported")
                    local = alias.asname or alias.name
                    if local in assignments or local in self.modules or local in self.event_types:
                        self.unsupported(node, "import aliases must not be rebound")
                    self.event_types[local] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name != "libevdev":
                        self.unsupported(node, "only static libevdev event imports are supported")
                    local = alias.asname or alias.name
                    if local in assignments or local in self.modules or local in self.event_types:
                        self.unsupported(node, "import aliases must not be rebound")
                    self.modules.add(local)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name not in supported:
                    self.unsupported(node, f"unsupported layout assignment {name!r}")
                if name in assignments or name in self.event_types or name in self.modules:
                    self.unsupported(node, f"duplicate assignment {name!r}")
                path = _field("$.geometry", name) if name in GEOMETRY_FIELDS else _field("$", name)
                try:
                    assignments[name] = self.literal(node.value, path)
                except RecursionError as exc:
                    raise _error(path, "Python literal nesting is too deep") from exc
            else:
                self.unsupported(node)
        document = {"schema_version": 1}
        if "name" in assignments:
            document["name"] = assignments["name"]
        document["geometry"] = {key: value for key, value in assignments.items() if key in GEOMETRY_FIELDS}
        if "app_shortcuts" in assignments:
            document["app_shortcuts"] = assignments["app_shortcuts"]
        return normalize_document(document)


def parse_python(data: str | bytes, filename: str = "<layout>") -> Layout:
    """Interpret the shipped literal subset; never import, compile, or execute it."""
    return _StaticPython(str(filename)).parse(data)


def dumps_layout(layout: Layout) -> str:
    validated = normalize_document(layout.document)
    return json.dumps(validated.document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def load_path(path: str | os.PathLike) -> Layout:
    path = Path(path)
    if path.suffix not in (".json", ".py"):
        raise _error(str(path), "layout path must end in .json or .py")
    data = path.read_bytes()
    return parse_json(data) if path.suffix == ".json" else parse_python(data, str(path))


def revision_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_layout(path: str | os.PathLike, layout: Layout, *,
                expected_revision: str | None = None, overwrite: bool = False) -> str:
    """Atomically save JSON, checking edits and preserving existing file mode.

    A new destination is published using a no-clobber hard link. Replacements use
    os.replace. The revision is checked again immediately before publication;
    callers should still treat concurrent external writers as independent actors,
    since portable filesystems offer no conditional compare-and-replace primitive.
    """
    path = Path(path)
    payload = dumps_layout(layout).encode("utf-8")

    def inspect() -> int | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            if path.is_symlink():
                raise FileExistsError(f"Refusing to replace dangling symlink: {path}")
            if expected_revision is not None:
                raise FileExistsError(f"Layout was removed outside this editor: {path}")
            return None
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"Layout destination is not a regular file: {path}")
        if not overwrite:
            raise FileExistsError(f"Layout already exists; explicit overwrite is required: {path}")
        if expected_revision is not None and revision_bytes(path.read_bytes()) != expected_revision:
            raise FileExistsError(f"Layout changed outside this editor; reload or save a copy: {path}")
        return stat.S_IMODE(info.st_mode)

    mode = inspect()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if mode is not None:
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), mode)
                else:
                    os.chmod(temporary, mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        inspect()
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return revision_bytes(payload)


def validate_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise _error("identifier", "use a nonempty filename stem matching [A-Za-z0-9][A-Za-z0-9_-]*")
    return identifier


def user_layout_path(config_dir: str | os.PathLike, identifier: str) -> Path:
    identifier = validate_identifier(identifier)
    root = Path(config_dir).expanduser().resolve()
    directory = root / "layouts"
    destination = directory / f"{identifier}.json"
    resolved_directory = directory.resolve()
    resolved_destination = destination.resolve()
    if not resolved_directory.is_relative_to(root) or not resolved_destination.is_relative_to(resolved_directory):
        raise _error("identifier", "layout destination escapes the configuration layout directory through a symlink")
    return destination


def _builtin_paths(install_dir: Path) -> set[Path]:
    manifest_path = install_dir / "bundled-layouts.json"
    try:
        data = manifest_path.read_bytes()
    except FileNotFoundError:
        return set()
    try:
        pairs = json.loads(data, object_pairs_hook=_Pairs)
        issues: list[Issue] = []
        manifest = _unpack_pairs(pairs, str(manifest_path), issues)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _error(str(manifest_path), f"invalid bundled-layout manifest: {exc}") from exc
    if issues:
        raise ValidationError(issues)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "layouts"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or not isinstance(manifest["layouts"], list)):
        raise _error(str(manifest_path), "expected a v1 bundled-layout manifest with a layouts array")
    builtins = set()
    for index, relative in enumerate(manifest["layouts"]):
        issue_path = f"{manifest_path}.layouts[{index}]"
        if not isinstance(relative, str) or "\\" in relative:
            raise _error(issue_path, "expected layouts/<identifier>.json or layouts/<identifier>.py")
        parts = relative.split("/")
        if len(parts) != 2 or parts[0] != "layouts":
            raise _error(issue_path, "bundled layout paths must be directly beneath layouts/")
        filename = Path(parts[1])
        if filename.suffix not in (".json", ".py"):
            raise _error(issue_path, "unsupported bundled layout extension")
        validate_identifier(filename.stem)
        builtins.add((install_dir / relative).resolve())
    return builtins


def discover_layouts(config_dir: str | os.PathLike, install_dir: str | os.PathLike | None = None) -> list[LayoutSource]:
    """List actual files, without parsing them; user directory priority comes first."""
    config_root = Path(config_dir).expanduser().resolve()
    install_root = Path(install_dir).expanduser().resolve() if install_dir is not None else _MODULE_DIR
    builtins = _builtin_paths(install_root)
    seen_directories = set()
    candidates: dict[str, list[Path]] = {}
    for root in (config_root, install_root):
        directory = (root / "layouts").resolve()
        if directory in seen_directories:
            continue
        seen_directories.add(directory)
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            continue
        children.sort(key=lambda path: (path.stem, 0 if path.suffix == ".json" else 1, path.name))
        for path in children:
            if (path.suffix not in (".json", ".py") or not _IDENTIFIER.fullmatch(path.stem)
                    or not path.is_file()):
                continue
            paths = candidates.setdefault(path.stem, [])
            if all(previous.resolve() != path.resolve() for previous in paths):
                paths.append(path)
    result = []
    for identifier, paths in candidates.items():
        path = paths[0]
        builtin = path.resolve() in builtins
        result.append(LayoutSource(identifier, path, "json" if path.suffix == ".json" else "python",
                                   "builtin" if builtin else "user", builtin, tuple(paths[1:])))
    return result


def resolve_layout(identifier: str, config_dir: str | os.PathLike,
                   install_dir: str | os.PathLike | None = None) -> LayoutSource:
    identifier = validate_identifier(identifier)
    for source in discover_layouts(config_dir, install_dir):
        if source.identifier == identifier:
            return source
    raise FileNotFoundError(f"No layout {identifier!r} in the selected configuration/install directories")


def _cli_source(source: str, config_dir: str, install_dir: str | None) -> tuple[Path, str]:
    path = Path(source).expanduser()
    if path.suffix in (".json", ".py") or path.is_absolute() or "/" in source or "\\" in source:
        if path.suffix not in (".json", ".py"):
            raise _error(str(path), "layout path must end in .json or .py")
        return path, "json" if path.suffix == ".json" else "python"
    selected = resolve_layout(source, config_dir, install_dir)
    return selected.path, selected.format


def _cli_load(source: str, args: argparse.Namespace) -> Layout:
    path, format_name = _cli_source(source, args.config_dir, args.install_dir)
    data = path.read_bytes()
    if format_name == "json":
        return parse_json(data)
    try:
        return parse_python(data, str(path))
    except ValidationError:
        if not args.trusted_python:
            raise
        from dialpad_layout_linux import load_trusted_python
        print(f"Executing explicitly trusted Python source: {path}", file=sys.stderr)
        return load_trusted_python(data, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=os.environ.get("DIALPAD_CONFIG_DIR"),
                        help="instance configuration directory (or DIALPAD_CONFIG_DIR)")
    parser.add_argument("--install-dir", help="driver installation directory, defaults to this module's directory")
    parser.add_argument("--trusted-python", action="store_true",
                        help="explicitly permit dynamic Python execution; use only with trusted sources")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    listing = subparsers.add_parser("list", help="list discovered layout identifiers without loading them")
    list_format = listing.add_mutually_exclusive_group()
    list_format.add_argument("--identifiers", action="store_true", help="one identifier per line (default)")
    list_format.add_argument("--json", action="store_true", help="include source paths and provenance as JSON")
    validate = subparsers.add_parser("validate", help="statically validate a path or discovered identifier")
    validate.add_argument("source")
    convert = subparsers.add_parser("convert", help="convert a source path or identifier to JSON")
    convert.add_argument("source")
    convert.add_argument("destination")
    convert.add_argument("--overwrite", action="store_true")
    export = subparsers.add_parser("export", help="export a discovered identifier as JSON")
    export.add_argument("identifier")
    export.add_argument("destination")
    export.add_argument("--overwrite", action="store_true")
    activate = subparsers.add_parser("activate", help="request activation; this is not driver acknowledgment")
    activate.add_argument("identifier")
    subparsers.add_parser("status", help="query the running instance; unavailable is an error")
    config_set = subparsers.add_parser("config-set", help="transactionally update one [main] setting")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_get = subparsers.add_parser("config-get", help="read one [main] setting, empty when absent")
    config_get.add_argument("key")
    defaults = subparsers.add_parser("config-defaults", help="transactionally merge missing INI defaults")
    defaults.add_argument("path")
    for command in (validate, convert, export, activate):
        command.add_argument("--trusted-python", action="store_true", default=argparse.SUPPRESS,
                             help="explicitly permit trusted dynamic Python execution")
    args = parser.parse_args(argv)
    if not args.config_dir:
        parser.error("--config-dir or DIALPAD_CONFIG_DIR is required; an instance directory is never guessed")
    try:
        if args.operation == "list":
            sources = discover_layouts(args.config_dir, args.install_dir)
            if args.json:
                print(json.dumps([{
                    "identifier": source.identifier,
                    "path": str(source.path),
                    "format": source.format,
                    "provenance": source.provenance,
                    "builtin": source.builtin,
                    "overrides": [str(path) for path in source.overrides],
                } for source in sources], indent=2))
            else:
                for source in sources:
                    print(source.identifier)
        elif args.operation in ("validate", "convert", "export"):
            if args.operation == "export":
                validate_identifier(args.identifier)
                layout = _cli_load(args.identifier, args)
            else:
                layout = _cli_load(args.source, args)
            if args.operation == "validate":
                print("Valid layout")
            else:
                print(save_layout(args.destination, layout, overwrite=args.overwrite))
        else:
            import dialpad_layout_linux as linux
            if args.operation == "activate":
                loaded = linux.activate_layout(args.config_dir, args.identifier, args.install_dir,
                                               trusted_python=args.trusted_python)
                print(json.dumps({"state": "requested", "identifier": loaded.source.identifier,
                                  "revision": loaded.revision, "path": str(loaded.source.path)}, indent=2))
            elif args.operation == "status":
                print(json.dumps(linux.get_status(args.config_dir), indent=2))
            elif args.operation == "config-set":
                linux.update_config(args.config_dir, {args.key: args.value})
            elif args.operation == "config-get":
                print(linux.read_config(args.config_dir).get("main", args.key, fallback=""))
            else:
                defaults_parser = configparser.ConfigParser(interpolation=None)
                with Path(args.path).open(encoding="utf-8") as stream:
                    defaults_parser.read_file(stream)
                linux.initialize_config(args.config_dir, defaults_parser)
    except (ValidationError, OSError, ValueError, RuntimeError, ImportError, configparser.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
