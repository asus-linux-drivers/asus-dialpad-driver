"""Linux input/session integration, layout compilation, configuration, and status.

The portable model never imports this module. libevdev is imported only while
compiling events or explicitly executing a trusted Python layout. Lock order:
configuration transactions own only their sidecar lock; callers must not wait
for the input owner or hold its output lock while doing file transactions.
"""

from __future__ import annotations

import ast
import configparser
import contextlib
from dataclasses import dataclass
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import logging
import math
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import threading
import types
from typing import Callable

from dialpad_layout import (
    Issue, Layout, LayoutSource, ValidationError, dumps_layout,
    normalize_document, parse_json, parse_python, resolve_layout,
    revision_bytes, user_layout_path, validate_identifier,
)

LOG = logging.getLogger("asus-dialpad-driver")
_GEOMETRY_FIELDS = (
    "top_right_icon_width", "top_right_icon_height", "circle_diameter",
    "center_button_diameter", "circle_center_x", "circle_center_y",
)
_python_load_lock = threading.Lock()


@dataclass(frozen=True)
class LoadedLayout:
    layout: Layout
    source: LayoutSource
    revision: str


def close_virtual_device(device) -> None:
    """Destroy uinput now, even while snapshots or libevdev cycles retain Device."""
    # python-libevdev Device has no public close method and references itself
    # through its value/absinfo helpers. Isolate access to its native context
    # here instead of waiting for cyclic GC to remove a compositor device.
    native = device._uinput
    if native is not None:
        device._uinput = None
        with native:
            pass


def select_keyboard_device(devices: str) -> str | None:
    """Select one physical typing keyboard, preferring historically known names."""
    fallback = None
    word_bits = struct.calcsize("L") * 8
    # Linux input-event-codes.h: A, Z, space, left Ctrl, left Shift, left Alt.
    typing_keys = (30, 44, 57, 29, 42, 56)
    required = sum(1 << code for code in typing_keys)
    for block in devices.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        sysfs = fields.get("S: Sysfs", "")
        # Bluetooth HID can live under /devices/virtual/misc/uhid; only the
        # virtual input subtree denotes injected devices such as uinput.
        if not sysfs or sysfs.startswith("/devices/virtual/input/"):
            continue
        event = next((item[5:] for item in fields.get("H: Handlers", "").split()
                      if item.startswith("event") and item[5:].isdigit()), None)
        if event is None:
            continue
        words = fields.get("B: KEY", "").split()
        bits = sum(int(word, 16) << (index * word_bits)
                   for index, word in enumerate(reversed(words)))
        if bits & required != required:
            continue
        name = fields.get("N: Name", "").strip('"')
        if (name == "AT Translated Set 2 keyboard" or
                (name.startswith(("ASUE", "Asus", "ASUP", "ASUF")) and "Keyboard" in name)):
            return event
        if fallback is None:
            fallback = event
    return fallback


def uses_gnome_input_sources(environ) -> bool:
    """Explicit current-desktop identity takes precedence over login defaults."""
    desktop = environ.get("XDG_CURRENT_DESKTOP", "")
    if desktop:
        return any(name.casefold() in {"gnome", "gnome-classic", "gnome-flashback", "unity"}
                   for name in desktop.split(":"))
    session = environ.get("XDG_SESSION_DESKTOP") or environ.get("DESKTOP_SESSION", "")
    return session.casefold() in {
        "gnome", "gnome-xorg", "gnome-wayland", "gnome-classic", "gnome-flashback",
        "ubuntu", "ubuntu-xorg", "ubuntu-wayland", "unity",
    }


def read_gnome_input_source(get_setting) -> tuple[int, str] | None:
    """Read GNOME's selected XKB source without mistaking GVariant types for data."""
    def sources(name):
        data = get_setting("org.gnome.desktop.input-sources", name)
        if not data:
            return []
        text = data.decode().strip().removeprefix("@a(ss)").lstrip()
        value = ast.literal_eval(text)
        if not isinstance(value, list) or any(
                not isinstance(item, tuple) or len(item) != 2 or
                not all(isinstance(part, str) for part in item) for item in value):
            raise ValueError(f"Invalid GNOME input source list: {name}")
        return value

    available = sources("sources")
    if not available:
        return None
    recent = sources("mru-sources")
    if recent and recent[0] in available:
        index = available.index(recent[0])
    else:
        current = get_setting("org.gnome.desktop.input-sources", "current")
        if not current:
            return None
        index = int(current.decode().split()[-1])
    if not 0 <= index < len(available) or available[index][0] != "xkb":
        return None
    return index, available[index][1].split("+", 1)[0]


def _issue(path: str, message: str) -> ValidationError:
    return ValidationError([Issue("error", path, message)])


def _trusted_python(data: bytes, source: LayoutSource) -> Layout:
    """Execute exactly the bytes hashed by the caller; never consult a .pyc.

    Execution is explicitly trusted. Restoring module registrations does not
    undo external effects or reload imported helper modules.
    """
    import libevdev

    module_name = "layouts." + source.identifier
    module = types.ModuleType(module_name)
    module.__file__ = str(source.path)
    module.__package__ = "layouts"
    module.__spec__ = importlib.util.spec_from_loader(
        module_name, loader=None, origin=str(source.path),
    )
    module.__loader__ = None
    package = types.ModuleType("layouts")
    package.__package__ = "layouts"
    package.__path__ = [str(source.path.parent)]
    package.__spec__ = importlib.machinery.ModuleSpec("layouts", loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__

    # Module registration is needed by relative imports and e.g. dataclasses.
    # Serialize these temporary registrations across preparation workers.
    with _python_load_lock:
        missing = object()
        old_package = sys.modules.get("layouts", missing)
        old_module = sys.modules.get(module_name, missing)
        if old_package is not missing:
            package.__path__.extend(
                p for p in getattr(old_package, "__path__", ())
                if p not in package.__path__
            )
        sys.modules["layouts"] = package
        sys.modules[module_name] = module
        try:
            exec(compile(data, str(source.path), "exec"), module.__dict__)
        finally:
            for name, old in ((module_name, old_module), ("layouts", old_package)):
                if old is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old

    def ordinary(value):
        if isinstance(value, dict):
            return {key: ordinary(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ordinary(item) for item in value]
        # Event aliases retain their symbolic identity when libevdev exposes it.
        if getattr(value, "type", None) in (libevdev.EV_KEY, libevdev.EV_REL):
            return value.name
        return value

    document = {
        "schema_version": 1,
        "geometry": {field: getattr(module, field, None) for field in _GEOMETRY_FIELDS},
        "app_shortcuts": ordinary(getattr(module, "app_shortcuts", None)),
    }
    if hasattr(module, "name"):
        document["name"] = module.name
    return normalize_document(document)


def load_trusted_python(data: bytes, path) -> Layout:
    """Explicitly execute externally selected Python source bytes."""
    path = Path(path).resolve()
    source = LayoutSource(identifier=path.stem, path=path, format="python",
                          provenance="user", builtin=False)
    return _trusted_python(data, source)


def load_layout(identifier, config_dir, install_dir=None, *, trusted_python=False) -> LoadedLayout:
    source = resolve_layout(identifier, config_dir, install_dir)
    revision = None
    try:
        data = source.path.read_bytes()
        revision = revision_bytes(data)
        if source.format == "json":
            layout = parse_json(data)
        else:
            try:
                layout = parse_python(data, str(source.path))
            except ValidationError:
                if not trusted_python:
                    raise
                layout = _trusted_python(data, source)
    except Exception as error:
        error.requested = {"identifier": identifier, "path": str(source.path),
                           "revision": revision}
        raise
    return LoadedLayout(layout, source, revision)


def compile_layout(layout: Layout, *, libevdev_module=None) -> dict:
    """Resolve normalized actions against installed libevdev, without effects.

    Consumers receive separate event_values and value_query fields. The legacy
    overloaded value field does not cross this boundary. Modifier alternatives
    are stably prioritized once, rather than sorted for each input event.
    """
    if libevdev_module is None:
        import libevdev as libevdev_module

    def event(name, path):
        family = libevdev_module.EV_REL if name.startswith("REL_") else libevdev_module.EV_KEY
        code = getattr(family, name, None)
        if code is None:
            raise _issue(path, f"Event {name!r} is not supported by installed libevdev")
        return code

    def metadata(meta):
        result = {}
        for field in ("title", "icon", "unit", "value_query", "treshold"):
            value = getattr(meta, field)
            if value is not None:
                result[field] = value
        if meta.icons:
            result["icons"] = dict(meta.icons)
        return result

    def actions(entries, path):
        result = []
        for index, action in enumerate(entries):
            item_path = f"{path}[{index}]"
            item = metadata(action.metadata)
            item.update(kind=action.kind, trigger=action.trigger, duration=action.duration)
            if action.keys:
                keys = [event(name, item_path + ".key") for name in action.keys]
                item["key"] = keys[0] if len(keys) == 1 and action.kind != "relative" else keys
            if action.values:
                item["event_values"] = list(action.values)
            if action.command is not None:
                item["command"] = action.command
            if action.modifier is not None:
                item["modifier"] = event(action.modifier, item_path + ".modifier")
            result.append(item)
        result.sort(key=lambda item: "modifier" not in item)
        return result

    result = {}
    for app, profile in layout.profiles.items():
        path = f"$.app_shortcuts[{app!r}]"
        compiled = {
            direction: actions(entries, path + "." + direction)
            for direction, entries in profile.actions.items()
        }
        for name, function in profile.functions.items():
            function_path = path + f"[{name!r}]"
            value = metadata(function.metadata)
            if function.command is not None:
                value["command"] = function.command
            value.update({
                direction: actions(entries, function_path + "." + direction)
                for direction, entries in function.actions.items()
            })
            compiled[name] = value
        result[app] = compiled
    return result


def validate_device_geometry(layout: Layout, bounds: dict | None) -> None:
    if bounds is None:
        return
    minimum_x, maximum_x = bounds["min_x"], bounds["max_x"]
    minimum_y, maximum_y = bounds["min_y"], bounds["max_y"]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds.values()):
        raise _issue("$.geometry", "Device bounds are not finite numbers")
    if minimum_x >= maximum_x or minimum_y >= maximum_y:
        raise _issue("$.geometry", "Device bounds have no positive extent")
    geometry = layout.geometry
    radius = geometry["circle_diameter"] / 2
    issues = []
    for coordinate, minimum, maximum in (
        ("circle_center_x", minimum_x, maximum_x),
        ("circle_center_y", minimum_y, maximum_y),
    ):
        center = geometry[coordinate]
        if center - radius < minimum or center + radius > maximum:
            issues.append(Issue("error", "$.geometry." + coordinate,
                                "Dial circle extends outside measured touchpad bounds"))
    for field, extent in (
        ("top_right_icon_width", maximum_x - minimum_x),
        ("top_right_icon_height", maximum_y - minimum_y),
    ):
        if geometry[field] > extent:
            issues.append(Issue("error", "$.geometry." + field,
                                "Activation region exceeds measured touchpad extent"))
    if issues:
        raise ValidationError(issues)


def read_config(config_dir) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    path = Path(config_dir) / "dialpad_dev"
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except FileNotFoundError:
        pass
    if not parser.has_section("main"):
        parser.add_section("main")
    return parser


def _config_value(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


@contextlib.contextmanager
def _config_transaction(config_dir):
    directory = Path(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".dialpad_dev.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Configuration lock is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory, read_config(directory)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, *, durable=False, mode=0o600) -> None:
    """Replacement with cleanup on failure; recovery additionally fsyncs both.

    fsync failure after replacement propagates: callers must not claim durable
    persistence merely because the file is visible in the current process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode):
            raise OSError(f"Refusing to replace non-regular file: {path}")
        mode = stat.S_IMODE(existing.st_mode)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        if durable:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _write_config(directory: Path, parser: configparser.ConfigParser) -> None:
    buffer = io.StringIO()
    parser.write(buffer)
    _atomic_write(directory / "dialpad_dev", buffer.getvalue().encode("utf-8"))


def update_config(config_dir, patch: dict, defaults_if_missing: dict | None = None) -> configparser.ConfigParser:
    with _config_transaction(config_dir) as (directory, parser):
        changed = not (directory / "dialpad_dev").exists()
        for key, value in (defaults_if_missing or {}).items():
            if not parser.has_option("main", key):
                parser.set("main", key, _config_value(value))
                changed = True
        for key, value in patch.items():
            value = _config_value(value)
            if parser.get("main", key, fallback=None) != value:
                parser.set("main", key, value)
                changed = True
        if changed:
            _write_config(directory, parser)
        return parser


def initialize_config(config_dir, defaults: configparser.ConfigParser) -> configparser.ConfigParser:
    with _config_transaction(config_dir) as (directory, parser):
        changed = not (directory / "dialpad_dev").exists()
        for key, value in defaults.defaults().items():
            if key not in parser.defaults():
                parser[parser.default_section][key] = value
                changed = True
        for section in defaults.sections():
            if not parser.has_section(section):
                parser.add_section(section)
                changed = True
            for key, value in defaults.items(section, raw=True):
                if not parser.has_option(section, key):
                    parser.set(section, key, value)
                    changed = True
        if changed:
            _write_config(directory, parser)
        return parser


def requested_layout(config_dir, argv_default=None) -> str:
    explicit = read_config(config_dir).get("main", "layout", fallback="").strip()
    selection = explicit or argv_default
    if not selection:
        raise ValueError("No layout selected: set [main] layout or supply the positional layout argument")
    return validate_identifier(selection)


def activate_layout(config_dir, identifier, install_dir=None, *, trusted_python=False,
                    expected_revision=None) -> LoadedLayout:
    loaded = load_layout(identifier, config_dir, install_dir, trusted_python=trusted_python)
    if expected_revision is not None and loaded.revision != expected_revision:
        raise ValueError("Layout changed after review; reload it before activation")
    # Serialize the final source check with manager removal. Trusted Python ran
    # before taking this lock, so arbitrary code cannot deadlock config writes.
    with _config_transaction(config_dir) as (directory, parser):
        current = resolve_layout(identifier, config_dir, install_dir)
        if current.path != loaded.source.path or revision_bytes(current.path.read_bytes()) != loaded.revision:
            raise ValueError("Layout changed before activation; reload it before retrying")
        if parser.get("main", "layout", fallback=None) != identifier:
            parser.set("main", "layout", identifier)
            _write_config(directory, parser)
    return loaded


def remove_user_layout(config_dir, source: LayoutSource, expected_revision, install_dir=None,
                       *, instance_id, replacement=None) -> None:
    """Remove a reviewed, inactive file without racing another manager.

    Activation and removal share the stable config sidecar. The read-only
    status callback must return its in-memory snapshot, never acquire this file
    lock or wait for a queued input operation. Arbitrary external file writers
    do not participate in this transaction.
    """
    with _config_transaction(config_dir) as (_directory, parser):
        current = resolve_layout(source.identifier, config_dir, install_dir)
        allowed = user_layout_path(config_dir, source.identifier).parent
        if (current.builtin or current.path != source.path or source.path.is_symlink()
                or source.path.parent.resolve() != allowed):
            raise ValueError("Only the reviewed user layout file may be removed")
        status = get_status(config_dir)
        if status.get("instance_id") != instance_id:
            raise ValueError("Driver instance changed; the original file is retained")
        configured = parser.get("main", "layout", fallback="").strip()
        if configured == source.identifier or any(
            (status.get(field) or {}).get("identifier") == source.identifier
            for field in ("requested", "applied")
        ):
            raise ValueError("The layout is still requested or applied; select a replacement first")
        if replacement is not None:
            identifier, revision = replacement
            if status.get("state") != "applied" or configured != identifier or any(
                (status.get(field) or {}).get("identifier") != identifier
                or (status.get(field) or {}).get("revision") != revision
                for field in ("requested", "applied")
            ):
                raise ValueError("Replacement acknowledgment changed; the original file is retained")
            replacement_source = resolve_layout(identifier, config_dir, install_dir)
            if revision_bytes(replacement_source.path.read_bytes()) != revision:
                raise ValueError("Replacement changed after acknowledgment; the original file is retained")
        if revision_bytes(source.path.read_bytes()) != expected_revision:
            raise ValueError("The original file changed externally; it was not removed")
        source.path.unlink()


def save_recovery(config_dir, loaded: LoadedLayout) -> None:
    # Revalidate data instead of trusting mutable dictionaries or caller types.
    document = json.loads(dumps_layout(normalize_document(loaded.layout.document)))
    source = loaded.source
    envelope = {
        "recovery_version": 1,
        "source": {
            "identifier": source.identifier,
            "path": str(source.path),
            "format": source.format,
            "provenance": source.provenance,
            "builtin": source.builtin,
            "revision": loaded.revision,
        },
        "layout": document,
    }
    data = (json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _atomic_write(Path(config_dir) / ".layout-state" / "last-successful.json", data, durable=True)


def load_recovery(config_dir) -> LoadedLayout:
    path = Path(config_dir) / ".layout-state" / "last-successful.json"

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate recovery key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Non-finite recovery number: {value}")

    envelope = json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=invalid_constant)
    if not isinstance(envelope, dict) or set(envelope) != {"recovery_version", "source", "layout"}:
        raise ValueError("Invalid recovery envelope")
    if type(envelope["recovery_version"]) is not int or envelope["recovery_version"] != 1:
        raise ValueError("Unsupported recovery version")
    source = envelope["source"]
    if not isinstance(source, dict) or set(source) != {"identifier", "path", "format", "provenance", "builtin", "revision"}:
        raise ValueError("Invalid recovery source")
    identifier = validate_identifier(source["identifier"])
    revision = source["revision"]
    if not isinstance(revision, str) or len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid recovery revision")
    if source["format"] not in ("json", "python") or source["provenance"] not in ("builtin", "user"):
        raise ValueError("Invalid recovery source format/provenance")
    if type(source["builtin"]) is not bool or not isinstance(source["path"], str):
        raise ValueError("Invalid recovery source identity")
    layout = normalize_document(envelope["layout"])
    recovered_source = LayoutSource(identifier=identifier, path=Path(source["path"]),
                                    format=source["format"], provenance=source["provenance"],
                                    builtin=source["builtin"])
    return LoadedLayout(layout, recovered_source, revision)


def _private_directory(path: Path, *, create=False) -> None:
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError(f"Expected a current-user-only directory: {path}")


def _endpoint(config_dir, *, create=False) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("XDG_RUNTIME_DIR is unset; live driver status is unavailable")
    runtime_path = Path(runtime)
    if not runtime_path.is_absolute():
        raise RuntimeError("XDG_RUNTIME_DIR must be absolute")
    _private_directory(runtime_path)
    directory = runtime_path / "asus-dialpad-driver"
    _private_directory(directory, create=create)
    identity = hashlib.sha256(os.fsencode(Path(config_dir).resolve())).hexdigest()[:24]
    return directory / (identity + ".sock")


def _peer_is_current_user(connection: socket.socket) -> bool:
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    return uid == os.getuid()


def _receive_line(connection: socket.socket, limit=1048576) -> bytes:
    chunks = bytearray()
    while len(chunks) <= limit:
        data = connection.recv(min(65536, limit + 1 - len(chunks)))
        if not data:
            raise ConnectionError("Status connection closed before a complete response")
        chunks.extend(data)
        newline = chunks.find(b"\n")
        if newline >= 0:
            return bytes(chunks[:newline])
    raise ValueError("Status message exceeds size limit")


class StatusServer:
    """One read-only status endpoint per configuration directory and user.

    A stable flock prevents takeover. Only the owning server removes its socket;
    readers and the manager never unlink endpoints. Polling supports multiple
    clients without sharing the floating overlay's datagram socket.
    """

    def __init__(self, config_dir, get_status: Callable[[], dict]):
        self.config_dir = Path(config_dir)
        self.get_status = get_status
        self.path = None
        self._socket = None
        self._lock_fd = None
        self._thread = None
        self._stopping = threading.Event()
        self._identity = None

    def start(self):
        if self._socket is not None:
            raise RuntimeError("Status server is already running")
        self.path = _endpoint(self.config_dir, create=True)
        lock_path = self.path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise PermissionError("Status instance lock has unsafe ownership or permissions")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another driver owns this configuration's status endpoint") from error
            self._lock_fd = fd
        except BaseException:
            os.close(fd)
            raise
        try:
            try:
                old = self.path.lstat()
            except FileNotFoundError:
                old = None
            if old is not None:
                if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.getuid():
                    raise PermissionError("Refusing to replace an unowned status endpoint")
                self.path.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket = listener
            listener.bind(str(self.path))
            self.path.chmod(0o600)
            info = self.path.lstat()
            self._identity = (info.st_dev, info.st_ino)
            listener.listen(8)
            listener.settimeout(0.2)
            self._stopping.clear()
            self._thread = threading.Thread(target=self._serve, name="dialpad-status", daemon=True)
            self._thread.start()
        except BaseException:
            self.close()
            raise
        return self

    def _serve(self):
        listener = self._socket
        while not self._stopping.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stopping.is_set():
                    LOG.exception("Status listener failed")
                break
            with connection:
                try:
                    connection.settimeout(0.5)
                    if not _peer_is_current_user(connection):
                        continue
                    request = json.loads(_receive_line(connection, 4096))
                    if request != {"op": "status"}:
                        response = {"error": "Only the read-only status operation is supported"}
                    else:
                        response = self.get_status()
                    data = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
                    connection.sendall(data)
                except (OSError, ValueError):
                    LOG.debug("Invalid/disconnected status client", exc_info=True)
                except Exception:
                    LOG.exception("Unable to publish driver status")

    def close(self):
        self._stopping.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        if self._identity is not None and self.path is not None:
            with contextlib.suppress(FileNotFoundError):
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self._identity:
                    self.path.unlink()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
        self._socket = self._thread = self._lock_fd = self._identity = None


def get_status(config_dir, *, timeout=0.5) -> dict:
    path = _endpoint(config_dir)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("Driver status endpoint has unsafe ownership or permissions")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        if not _peer_is_current_user(connection):
            raise PermissionError("Status endpoint peer is not the current user")
        connection.sendall(b'{"op":"status"}\n')
        result = json.loads(_receive_line(connection))
    if not isinstance(result, dict) or not isinstance(result.get("instance_id"), str):
        raise ValueError("Invalid driver status response")
    return result