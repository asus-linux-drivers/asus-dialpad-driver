"""Hardware-independent ownership, contact boundaries, and reload coordination.

The input thread is the only caller of ``commit`` and the only gesture writer.
Callbacks only submit requests. Lock order is state/queue lock, then output lock.
Never wait for queued owner work while holding a configuration/file/output lock.
Read-only status takes only the state lock and does no file or output operations.
Output senders only take the output lock and pin one device for press/release.
Preparation, device creation, persistence, and metadata commands run outside both
runtime locks. A snapshot is published only after its replacement device is usable.
"""

from dataclasses import dataclass
import logging
import math
import os
import threading
import uuid


ACTIONS = frozenset(("center", "clockwise", "counterclockwise"))
CONTACT_KEYS = frozenset((
    "BTN_TOUCH", "BTN_TOOL_FINGER", "BTN_TOOL_DOUBLETAP", "BTN_TOOL_TRIPLETAP",
    "BTN_TOOL_PENCIL", "BTN_TOOL_BRUSH", "BTN_TOOL_AIRBRUSH", "BTN_TOOL_MOUSE",
    "BTN_TOOL_LENS",
    "BTN_TOOL_QUADTAP", "BTN_TOOL_QUINTTAP", "BTN_TOOL_PEN", "BTN_TOOL_RUBBER",
))


def select_profile(compiled, binary=None, title=None, suppress=False):
    """Keep ordered binary-before-title matching, including explicit empty rules."""
    if not suppress:
        for text in (binary, title):
            if text:
                lowered = text.lower()
                for name, profile in compiled.items():
                    if name != "none" and name in lowered:
                        return name, profile
    return "none", compiled["none"]


def function_metadata(profile, minimum=0):
    names = tuple(name for name in profile if name not in ACTIONS)
    icons = tuple(profile[name].get("icon") for name in names)
    padding = (None,) * max(0, minimum - len(names))
    return names + padding, icons + padding


def action_capabilities(compiled):
    keys, modifiers = set(), set()

    def visit(mapping):
        for name in ACTIONS:
            for entry in mapping.get(name, ()):
                key = entry.get("key")
                if key is not None:
                    keys.update(key if isinstance(key, (list, tuple)) else (key,))
                if entry.get("modifier") is not None:
                    modifiers.add(entry["modifier"])

    for profile in compiled.values():
        visit(profile)
        for name, function in profile.items():
            if name not in ACTIONS:
                visit(function)
    return frozenset(keys), frozenset(modifiers)


def matching_action(profile, action, pressed, modifiers, duration=0):
    """Compiled alternatives already have stable modifier-first precedence."""
    for entry in profile.get(action, ()):
        modifier = entry.get("modifier")
        if not ((modifier is not None and modifier in modifiers) or
                (modifier is None and not modifiers)):
            continue
        if duration < entry.get("duration", 0):
            continue
        trigger = entry.get("trigger", "release")
        if (trigger == "immediate") == bool(pressed):
            return entry
    return None


@dataclass(frozen=True)
class Geometry:
    center_x: float
    center_y: float
    radius: float
    center_radius: float
    icon_bounds: tuple

    @classmethod
    def from_layout(cls, geometry, bounds):
        return cls(
            geometry["circle_center_x"], geometry["circle_center_y"],
            geometry["circle_diameter"] / 2,
            geometry["center_button_diameter"] / 2,
            (bounds["max_x"] - geometry["top_right_icon_width"], bounds["max_x"],
             bounds["min_y"], bounds["min_y"] + geometry["top_right_icon_height"]),
        )

    def position(self, x, y):
        dx, dy = x - self.center_x, y - self.center_y
        return math.hypot(dx, dy), (math.degrees(math.atan2(dy, dx)) + 90) % 360


@dataclass(frozen=True)
class PreparedLayout:
    loaded: object
    compiled: dict
    geometry: Geometry
    settings: dict
    capabilities: frozenset
    modifiers: frozenset


@dataclass(frozen=True)
class Snapshot:
    prepared: PreparedLayout
    epoch: int
    profile_name: str
    profile: dict
    function_names: tuple
    function_titles: tuple
    function_icons: tuple
    context: dict
    device: object
    capabilities: frozenset
    coactivators: frozenset

    @property
    def geometry(self):
        return self.prepared.geometry


@dataclass
class GestureState:
    snapshot: Snapshot | None = None
    active: bool = False
    release_pending: bool = False
    started: float | None = None
    touch_x: float | None = None
    touch_y: float | None = None
    within_icon: bool = False
    icon_activated: bool = False
    coactivator_blocked: bool = False
    last_slice: int | None = None
    center_triggered: bool = False
    center_entered: float = 0
    center_immediate: bool = False
    tap_disabled: bool = False
    selected_function: str | None = None
    display_title: str | None = None
    center_activated: bool = False
    angle_start: float | None = None
    last_angle: float | None = None
    angle_accumulator: float = 0
    pending_rotation: str | None = None

    def begin(self, snapshot, now):
        # Function confirmation persists across touches, but never snapshots.
        selected, title, confirmed = self.selected_function, self.display_title, self.center_activated
        self.reset()
        self.snapshot, self.active, self.started = snapshot, True, now
        self.selected_function, self.display_title = selected, title
        self.center_activated = confirmed

    def finish(self):
        self.active = self.release_pending = False
        self.snapshot = None
        self.touch_x = self.touch_y = None
        self.started = None
        self.last_angle = self.angle_start = None
        self.angle_accumulator = 0
        self.pending_rotation = None
        self.center_triggered = False

    def reset(self):
        self.__dict__.update(GestureState().__dict__)


class ContactState:
    """Track frame completion and *all* contact indicators, not BTN_TOOL_FINGER.

    Type-B slots retain tracking IDs across frames. Type-A devices are supported
    through per-frame contacts; after a lost Type-A frame, contact keys must be
    re-queried or a complete fresh frame must arrive before declaring it idle.
    """

    def __init__(self):
        self.keys = {}
        self.slots = {}
        self.positions = {}
        self.slot = 0
        self.has_slots = False
        self.type_a = False
        self.type_a_frame = False
        self.type_a_active = False
        self.x = self.y = None
        self.frame_complete = True
        self.synchronized = False

    @property
    def touching(self):
        return (any(self.keys.values()) or any(value >= 0 for value in self.slots.values())
                or self.type_a_active)

    @property
    def idle(self):
        return self.synchronized and self.frame_complete and not self.touching

    def feed(self, name, value):
        if name == "SYN_DROPPED":
            self.synchronized = False
            self.frame_complete = False
            return
        if name == "SYN_REPORT":
            if self.type_a:
                self.type_a_active = self.type_a_frame
                self.type_a_frame = False
            self.frame_complete = True
            return
        self.frame_complete = False
        if name in CONTACT_KEYS:
            self.keys[name] = bool(value)
        elif name == "ABS_MT_SLOT":
            self.has_slots = True
            self.slot = value
        elif name == "ABS_MT_TRACKING_ID":
            if self.has_slots:
                self.slots[self.slot] = value
            else:
                self.type_a = True
                self.type_a_frame |= value >= 0
        elif name in ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y"):
            axis = 0 if name.endswith("_X") else 1
            pos = self.positions.setdefault(self.slot, [None, None])
            pos[axis] = value
            if not self.has_slots:
                self.type_a_frame = True
        elif name == "ABS_X":
            self.x = value
        elif name == "ABS_Y":
            self.y = value
        elif name == "SYN_MT_REPORT":
            self.type_a = True

    def position(self):
        if self.has_slots:
            active = next((slot for slot, value in self.slots.items() if value >= 0), None)
            if active is not None:
                return tuple(self.positions.get(active, (None, None)))
        if self.x is not None and self.y is not None:
            return self.x, self.y
        return tuple(self.positions.get(self.slot, (None, None)))

    def resynchronize(self, keys, slots, *, positions=None, slot=0, x=None, y=None,
                      type_a=False):
        """Only call after input-device sync and complete capability-aware queries."""
        self.keys = dict(keys)
        self.has_slots = slots is not None
        self.slots = dict(slots or {})
        self.positions = dict(positions or {})
        self.slot, self.x, self.y = slot or 0, x, y
        self.type_a = type_a
        self.type_a_frame = False
        # Unknown Type-A contacts must not be mistaken for an idle device.
        self.type_a_active = type_a and not keys
        self.frame_complete = True
        self.synchronized = True


class Wakeup:
    def __init__(self):
        self.reader, self.writer = os.pipe()
        os.set_blocking(self.reader, False)
        os.set_blocking(self.writer, False)
        self._lock = threading.Lock()

    def fileno(self):
        return self.reader

    def notify(self):
        with self._lock:
            try:
                os.write(self.writer, b"\0")
            except (BlockingIOError, OSError):
                pass

    def drain(self):
        with self._lock:
            try:
                while os.read(self.reader, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass

    def close(self):
        with self._lock:
            for fd in (self.reader, self.writer):
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.reader = self.writer = -1


class OutputDevice:
    def __init__(self):
        self.lock = threading.Lock()
        self.device = None

    def send(self, press, release=None):
        with self.lock:
            pinned = self.device
            if pinned is None:
                raise RuntimeError("Virtual output device is not initialized")
            try:
                pinned.send_events(press)
            finally:
                if release is not None:
                    pinned.send_events(release)


class CandidateError(Exception):
    def __init__(self, message, requested=None):
        super().__init__(message)
        self.requested = requested


def source_status(loaded):
    return {"identifier": loaded.source.identifier, "path": str(loaded.source.path),
            "revision": loaded.revision}


class RuntimeOwner:
    """Latest-request worker plus an explicit, input-thread commit boundary.

    ``prepare_device(prepared, context, previous)`` returns a device, its complete
    capability set, and resolved coactivator keys without changing live state.
    ``dispose_device(device)`` releases retired or unpublished native devices.
    ``publish(snapshot)`` publishes structural feedback only, after the atomic
    transition; callers enqueue command-backed metadata separately.
    """

    def __init__(self, prepare, prepare_device, persist, publish, bounds=None, *, dispose_device):
        self.prepare = prepare
        self.prepare_device = prepare_device
        self.dispose_device = dispose_device
        self.persist = persist
        self.publish = publish
        self.bounds = bounds
        self.instance_id = str(uuid.uuid4())
        self.wakeup = Wakeup()
        self.output = OutputDevice()
        self.contacts = ContactState()
        self.gesture = GestureState()
        self.current = None
        self._condition = threading.Condition()
        self._closed = False
        self._generation = 0
        self._work = False
        self._candidate = None
        self._context = {}
        self._window = (None, None)
        self._context_version = 0
        self._committed_context = -1
        self._epoch = 0
        self._messages = []
        self._metadata_serial = 0
        self._metadata_job = None
        self._metadata_result = None
        self._state = "pending"
        self._requested = {"identifier": None, "revision": None, "path": None}
        self._error = None
        self._recovery_error = None
        self._recovery_current = False
        self._worker = threading.Thread(target=self._prepare_loop, daemon=True, name="layout-prepare")
        self._metadata_worker = threading.Thread(target=self._metadata_loop, daemon=True, name="layout-values")
        self._worker.start()
        self._metadata_worker.start()

    def request(self, identifier=None):
        with self._condition:
            self._generation += 1
            generation = self._generation
            self._requested = {
                "identifier": identifier if identifier is not None else self._requested["identifier"],
                "revision": None, "path": None,
            }
            self._state, self._error = "pending", None
            self._candidate = None
            self._work = True
            self._condition.notify_all()
        self.wakeup.notify()
        return generation

    def submit_window(self, binary, title):
        with self._condition:
            if self._window == (binary, title):
                return
            self._window = binary, title
            self._context_version += 1
        self.wakeup.notify()

    def submit_context(self, **changes):
        with self._condition:
            self._context = {**self._context, **changes}
            self._context_version += 1
        self.wakeup.notify()

    def submit(self, kind, value):
        with self._condition:
            self._messages.append((kind, value))
        self.wakeup.notify()

    def messages(self):
        with self._condition:
            messages, self._messages = self._messages, []
        return messages

    def install_startup(self, prepared, *, requested=None, recovery_error=None):
        """Prepare the initial output before reading any gestures; recovery is explicit."""
        with self._condition:
            self._generation += 1
            self._candidate = (self._generation, prepared, bool(recovery_error))
            self._requested = requested or source_status(prepared.loaded)
            self._error = recovery_error
        # No gesture can precede initial installation. Real contacts are queried
        # before entering the event loop and block every subsequent replacement.
        return self.commit(initial=True)

    def _prepare_loop(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._work)
                if self._closed:
                    return
                generation = self._generation
                self._work = False
            try:
                prepared = self.prepare()
            except Exception as error:
                with self._condition:
                    if generation == self._generation:
                        self._state, self._error = "rejected", str(error)
                        requested = getattr(error, "requested", None)
                        if requested is not None:
                            self._requested = requested
                self.wakeup.notify()
                continue
            with self._condition:
                if generation == self._generation:
                    self._requested = source_status(prepared.loaded)
                    self._candidate = (generation, prepared, False)
            self.wakeup.notify()

    def commit(self, *, initial=False):
        if not initial and (not self.contacts.idle or self.gesture.active or self.gesture.release_pending):
            return False
        with self._condition:
            if self._closed:
                return False
            candidate = self._candidate
            context_version = self._context_version
            if candidate is None and (self.current is None or context_version == self._committed_context):
                return False
            generation = self._generation
            prepared = candidate[1] if candidate else self.current.prepared
            context = self._context.copy()
            window = self._window
            previous = self.current
        try:
            name, profile = select_profile(prepared.compiled, *window,
                                           suppress=prepared.settings.get("suppress", False))
            unchanged = (
                previous is not None and name == previous.profile_name and
                context == previous.context and
                source_status(prepared.loaded) == source_status(previous.prepared.loaded) and
                prepared.compiled == previous.prepared.compiled and
                prepared.geometry == previous.prepared.geometry and
                prepared.settings == previous.prepared.settings
            )
            if unchanged:
                with self._condition:
                    if generation != self._generation or context_version != self._context_version:
                        return False
                    self._committed_context = context_version
                    self._candidate = None
                    if candidate:
                        self._state = "recovered" if candidate[2] else "applied"
                        if not candidate[2]:
                            self._error = None
                    persist_again = candidate is not None and not self._recovery_current
                if persist_again:
                    self._persist(prepared)
                return False
            names, icons = function_metadata(profile, prepared.settings.get("slices", 4))
            titles = tuple(profile[item].get("title", item) if item is not None else None
                           for item in names)
            output, capabilities, coactivators = self.prepare_device(prepared, context, previous)
        except Exception as error:
            with self._condition:
                if generation == self._generation and context_version == self._context_version:
                    self._candidate = None
                    self._committed_context = context_version
                    self._state, self._error = "rejected", str(error)
            if initial:
                raise
            return False
        with self._condition:
            stale = (self._closed or generation != self._generation or
                     context_version != self._context_version)
            if not stale:
                self._epoch += 1
                snapshot = Snapshot(prepared, self._epoch, name, profile, names, titles, icons,
                                    context, output, capabilities, coactivators)
                # Senders never acquire _condition: replacement cannot split a pair.
                with self.output.lock:
                    self.output.device = output
                    self.current = snapshot
                    self.gesture.reset()
                self._metadata_serial += 1
                self._metadata_job = None
                self._metadata_result = None
                self._committed_context = context_version
                self._candidate = None
                if candidate:
                    self._state = "recovered" if candidate[2] else "applied"
                    if not candidate[2]:
                        self._error = None
                    self._recovery_current = False
        if stale:
            if previous is None or output is not previous.device:
                self._dispose_output(output)
            return False
        if previous is not None and previous.device is not output:
            self._dispose_output(previous.device)
        # These callbacks cannot expose partially prepared mappings or hold locks.
        self.publish(snapshot)
        if candidate:
            self._persist(prepared)
        return True

    def _dispose_output(self, device):
        if device is not None:
            try:
                self.dispose_device(device)
            except Exception:
                logging.getLogger("asus-dialpad-driver").exception(
                    "Cannot release retired virtual output device")

    def _persist(self, prepared):
        try:
            self.persist(prepared.loaded)
        except Exception as error:
            with self._condition:
                self._recovery_current = False
                self._recovery_error = str(error)
        else:
            with self._condition:
                self._recovery_current = True
                self._recovery_error = None

    def request_metadata(self, work):
        with self._condition:
            self._metadata_serial += 1
            token = self._epoch, self._metadata_serial
            self._metadata_job = token, work
            self._metadata_result = None
            self._condition.notify_all()
        return token

    def invalidate_metadata(self):
        with self._condition:
            self._metadata_serial += 1
            self._metadata_job = self._metadata_result = None

    def _metadata_loop(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._metadata_job is not None)
                if self._closed:
                    return
                token, work = self._metadata_job
                self._metadata_job = None
            try:
                result = work()
            except Exception:
                result = None
            with self._condition:
                if token == (self._epoch, self._metadata_serial):
                    self._metadata_result = token, result
            self.wakeup.notify()

    def metadata_result(self):
        with self._condition:
            result, self._metadata_result = self._metadata_result, None
            if result and result[0] == (self._epoch, self._metadata_serial):
                return result[1]
        return None

    def status(self):
        with self._condition:
            result = {
                "instance_id": self.instance_id, "generation": self._generation,
                "state": self._state, "requested": self._requested.copy(),
                "applied": source_status(self.current.prepared.loaded) if self.current else None,
                "recovery_current": self._recovery_current,
            }
            if self._error:
                result["error"] = self._error
            if self._recovery_error:
                result["recovery_error"] = self._recovery_error
            if self.bounds is not None:
                result["device_geometry"] = dict(self.bounds)
            return result

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            with self.output.lock:
                output, self.output.device = self.output.device, None
                self.current = None
                self.gesture.reset()
            self._candidate = None
        self._dispose_output(output)
        self.wakeup.close()
