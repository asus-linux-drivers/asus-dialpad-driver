"""No driver import, display server, evdev devices, commands, or installed state."""

from pathlib import Path
import selectors
import threading
import time
from types import SimpleNamespace
import unittest

from dialpad_runtime import (
    CandidateError, ContactState, Geometry, GestureState, OutputDevice,
    PreparedLayout, RuntimeOwner, action_capabilities, function_metadata,
    matching_action, select_profile,
)


def prepared(revision, profiles=None, identifier="custom"):
    if profiles is None:
        profiles = {"none": {"center": [{"key": revision, "trigger": "release"}]}}
    capabilities, modifiers = action_capabilities(profiles)
    loaded = SimpleNamespace(
        source=SimpleNamespace(identifier=identifier, path=Path("/layouts") / (identifier + ".json")),
        revision=revision,
    )
    return PreparedLayout(loaded, profiles, Geometry(100, 100, 90, 20, (180, 200, 0, 20)),
                          {"slices": 4, "suppress": False}, capabilities, modifiers)


class RecordingDevice:
    def __init__(self, name):
        self.name = name
        self.events = []
        self.closed = False

    def send_events(self, events):
        if self.closed:
            raise RuntimeError("Cannot send events after device disposal")
        self.events.append(list(events))

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.next_layout = prepared("b")
        self.persisted = []
        self.published = []
        self.devices = []
        self.owner = RuntimeOwner(lambda: self.next_layout, self.make_device,
                                  self.persisted.append, self.published.append,
                                  dispose_device=RecordingDevice.close)
        self.addCleanup(self.owner.close)
        self.owner.install_startup(prepared("a"))
        self.owner.contacts.resynchronize({"BTN_TOUCH": False, "BTN_TOOL_FINGER": False},
                                          {0: -1, 1: -1})

    def make_device(self, layout, context, previous):
        if previous and layout.capabilities <= previous.capabilities:
            return previous.device, previous.capabilities, frozenset(context.get("coactivators", ()))
        device = RecordingDevice(layout.loaded.revision)
        self.devices.append(device)
        return device, layout.capabilities, frozenset(context.get("coactivators", ()))

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        with selectors.DefaultSelector() as selector:
            selector.register(self.owner.wakeup, selectors.EVENT_READ)
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail("Runtime did not complete the requested transition")
                selector.select(remaining)
                self.owner.wakeup.drain()

    def request_prepared(self, revision="b"):
        self.owner.request()
        self.wait_until(lambda: self.owner.status()["requested"]["revision"] == revision)

    def test_idle_request_wakes_selector_without_input(self):
        self.owner.wakeup.drain()
        with selectors.DefaultSelector() as selector:
            selector.register(self.owner.wakeup, selectors.EVENT_READ)
            self.owner.request()
            self.assertTrue(selector.select(1), "An idle input loop was not notified")
        self.wait_until(lambda: self.owner.status()["requested"]["revision"] == "b")
        self.assertTrue(self.owner.commit())
        self.assertEqual(self.owner.status()["applied"]["revision"], "b")

    def test_held_gesture_and_its_release_keep_old_mapping(self):
        contacts = self.owner.contacts
        contacts.feed("BTN_TOUCH", 1)
        contacts.feed("SYN_REPORT", 0)
        self.owner.gesture.begin(self.owner.current, 1)
        old_snapshot = self.owner.current
        self.request_prepared()
        self.assertFalse(self.owner.commit())
        contacts.feed("BTN_TOUCH", 0)
        contacts.feed("SYN_REPORT", 0)
        self.assertFalse(self.owner.commit(), "Old release action has not run yet")
        gesture = self.owner.gesture
        gesture.active = False
        gesture.release_pending = True
        release = matching_action(gesture.snapshot.profile, "center", False, frozenset(), 1)
        self.assertEqual(release["key"], "a")
        self.owner.output.send([("a", 1)], [("a", 0)])
        self.assertFalse(self.owner.commit(), "Publication raced the old release")
        gesture.finish()
        self.assertTrue(self.owner.commit())
        self.assertEqual(old_snapshot.device.events, [[("a", 1)], [("a", 0)]])
        self.assertEqual(self.owner.current.profile["center"][0]["key"], "b")
        self.assertIsNone(self.owner.gesture.selected_function)

    def test_all_tool_keys_and_slots_must_release(self):
        contacts = self.owner.contacts
        contacts.feed("BTN_TOOL_DOUBLETAP", 1)
        contacts.feed("ABS_MT_SLOT", 1)
        contacts.feed("ABS_MT_TRACKING_ID", 23)
        contacts.feed("BTN_TOOL_FINGER", 0)
        contacts.feed("SYN_REPORT", 0)
        self.request_prepared()
        self.assertFalse(self.owner.commit())
        contacts.feed("BTN_TOOL_DOUBLETAP", 0)
        contacts.feed("SYN_REPORT", 0)
        self.assertFalse(self.owner.commit(), "A live MT slot is still held")
        contacts.feed("ABS_MT_TRACKING_ID", -1)
        self.assertFalse(self.owner.commit(), "Release frame is incomplete")
        contacts.feed("SYN_REPORT", 0)
        self.assertTrue(self.owner.commit())

    def test_dropped_events_need_fresh_contact_query(self):
        self.request_prepared()
        contacts = self.owner.contacts
        contacts.feed("SYN_DROPPED", 0)
        contacts.feed("BTN_TOUCH", 0)
        contacts.feed("SYN_REPORT", 0)
        self.assertFalse(self.owner.commit(), "Dropped input is not proof of release")
        contacts.resynchronize({"BTN_TOUCH": False}, {0: 50})
        self.assertFalse(self.owner.commit())
        contacts.resynchronize({"BTN_TOUCH": False}, {0: -1})
        self.assertTrue(self.owner.commit())

    def test_noncontact_partial_frame_also_defers_commit(self):
        self.request_prepared()
        self.owner.contacts.feed("ABS_X", 100)
        self.assertFalse(self.owner.commit())
        self.owner.contacts.feed("SYN_REPORT", 0)
        self.assertTrue(self.owner.commit())

    def test_parse_or_compile_failure_retains_usable_output(self):
        old = self.owner.current
        def fail():
            raise CandidateError("invalid event", {"identifier": "broken", "revision": "bad",
                                                    "path": "/layouts/broken.json"})
        self.owner.prepare = fail
        self.owner.request("broken")
        self.wait_until(lambda: self.owner.status()["state"] == "rejected")
        self.assertFalse(self.owner.commit())
        self.owner.output.send(["down"], ["up"])
        self.assertIs(self.owner.current, old)
        self.assertEqual(old.device.events, [["down"], ["up"]])
        status = self.owner.status()
        self.assertEqual(status["requested"]["revision"], "bad")
        self.assertEqual(status["applied"]["revision"], "a")
        self.assertIn("invalid event", status["error"])

    def test_device_creation_failure_never_publishes_candidate(self):
        old = self.owner.current
        self.request_prepared()
        def fail(*args):
            raise OSError("uinput denied")
        self.owner.prepare_device = fail
        self.assertFalse(self.owner.commit())
        self.owner.output.send(["old press"], ["old release"])
        self.assertIs(self.owner.current, old)
        self.assertEqual(old.device.events, [["old press"], ["old release"]])
        self.assertEqual(self.owner.status()["state"], "rejected")
        self.assertEqual([snapshot.prepared.loaded.revision for snapshot in self.published], ["a"])

    def test_slow_preparation_cannot_overwrite_new_request(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = iter((prepared("b"), prepared("c")))
        def prepare_next():
            item = next(calls)
            if item.loaded.revision == "b":
                started.set()
                if not release.wait(3):
                    raise TimeoutError("preparation barrier")
            return item
        self.owner.prepare = prepare_next
        self.owner.request()
        self.assertTrue(started.wait(1))
        newest = self.owner.request()
        release.set()
        self.wait_until(lambda: self.owner.status()["requested"]["revision"] == "c")
        self.assertTrue(self.owner.commit())
        self.assertEqual(self.owner.status()["generation"], newest)
        self.assertEqual(self.owner.status()["applied"]["revision"], "c")
        self.assertEqual(self.persisted[-1].revision, "c")
        self.assertNotIn("b", [snapshot.prepared.loaded.revision for snapshot in self.published])

    def test_stale_preparation_error_does_not_reject_newer_revision(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        first = True
        def prepare_next():
            nonlocal first
            if first:
                first = False
                started.set()
                if not release.wait(3):
                    raise TimeoutError("preparation barrier")
                raise ValueError("stale corruption")
            return prepared("c")
        self.owner.prepare = prepare_next
        self.owner.request()
        self.assertTrue(started.wait(1))
        self.owner.request()
        release.set()
        self.wait_until(lambda: self.owner.status()["requested"]["revision"] == "c")
        self.owner.commit()
        self.assertEqual(self.owner.status()["state"], "applied")
        self.assertNotIn("error", self.owner.status())

    def test_keymap_change_during_device_preparation_is_revalidated(self):
        self.request_prepared()
        old = self.owner.current
        changed = False
        def create(layout, context, previous):
            nonlocal changed
            result = self.make_device(layout, context, previous)
            if not changed:
                changed = True
                self.owner.submit_context(coactivators=("shift-new",))
            return result
        self.owner.prepare_device = create
        self.assertFalse(self.owner.commit())
        self.assertIs(self.owner.current, old)
        self.assertTrue(self.devices[-1].closed)
        self.assertFalse(old.device.closed)
        self.assertTrue(self.owner.commit())
        self.assertEqual(self.owner.current.coactivators, frozenset(("shift-new",)))
        self.assertEqual(self.owner.status()["applied"]["revision"], "b")
        self.assertTrue(old.device.closed)
        self.assertFalse(self.owner.current.device.closed)

    def test_output_press_release_pair_pins_device_during_replacement(self):
        pressed, release, preparing = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        old = self.owner.current.device
        original_send = old.send_events
        def send(events):
            original_send(events)
            if events == ["down"]:
                pressed.set()
                if not release.wait(3):
                    raise TimeoutError("output barrier")
        old.send_events = send
        self.request_prepared()
        sender = threading.Thread(target=lambda: self.owner.output.send(["down"], ["up"]))
        sender.start()
        self.assertTrue(pressed.wait(1))
        def create(layout, context, previous):
            result = self.make_device(layout, context, previous)
            preparing.set()
            return result
        self.owner.prepare_device = create
        committer = threading.Thread(target=self.owner.commit)
        committer.start()
        self.assertTrue(preparing.wait(1))
        self.assertIs(self.owner.output.device, old)
        self.assertFalse(old.closed)
        release.set()
        sender.join(2)
        committer.join(2)
        self.assertFalse(sender.is_alive())
        self.assertFalse(committer.is_alive())
        self.assertEqual(old.events, [["down"], ["up"]])
        self.assertEqual(self.owner.current.device.events, [])
        self.assertEqual(self.owner.status()["applied"]["revision"], "b")
        self.assertTrue(old.closed)
        self.assertFalse(self.owner.current.device.closed)

    def test_context_publication_reuses_live_device_without_disposal(self):
        old = self.owner.current.device
        self.owner.submit_context(coactivators=("shift",))
        self.assertTrue(self.owner.commit())
        self.assertIs(self.owner.current.device, old)
        self.assertFalse(old.closed)
        self.owner.output.send(["press"], ["release"])
        self.assertEqual(old.events, [["press"], ["release"]])

    def test_stale_reused_device_remains_usable(self):
        old = self.owner.current.device
        self.owner.submit_context(coactivators=("shift",))
        def stale(layout, context, previous):
            result = self.make_device(layout, context, previous)
            self.owner.submit_context(coactivators=("control",))
            return result
        self.owner.prepare_device = stale
        self.assertFalse(self.owner.commit())
        self.assertIs(self.owner.output.device, old)
        self.owner.output.send(["press"], ["release"])
        self.assertEqual(old.events, [["press"], ["release"]])

    def test_shutdown_during_preparation_disposes_both_devices_without_publication(self):
        old = self.owner.current.device
        self.request_prepared()
        def stopped(layout, context, previous):
            result = self.make_device(layout, context, previous)
            self.owner.close()
            return result
        self.owner.prepare_device = stopped
        self.assertFalse(self.owner.commit())
        self.assertTrue(old.closed)
        self.assertTrue(self.devices[-1].closed)
        self.assertIsNone(self.owner.output.device)
        self.assertIsNone(self.owner.status()["applied"])
        self.assertEqual([item.prepared.loaded.revision for item in self.published], ["a"])

    def test_output_attempts_release_even_when_press_fails(self):
        output = OutputDevice()
        output.device = RecordingDevice("failure")
        original = output.device.send_events
        def send(events):
            original(events)
            if events == ["press"]:
                raise OSError("partial press")
        output.device.send_events = send
        with self.assertRaisesRegex(OSError, "partial press"):
            output.send(["press"], ["release"])
        self.assertEqual(output.device.events, [["press"], ["release"]])

    def test_recovery_failure_does_not_undo_live_commit(self):
        self.request_prepared()
        def fail(loaded):
            raise OSError("recovery disk full")
        self.owner.persist = fail
        self.assertTrue(self.owner.commit())
        status = self.owner.status()
        self.assertEqual(status["state"], "applied")
        self.assertEqual(status["applied"]["revision"], "b")
        self.assertFalse(status["recovery_current"])
        self.assertIn("disk full", status["recovery_error"])
        self.assertNotIn("error", status)

    def test_startup_recovery_reports_requested_and_recovered_separately(self):
        owner = RuntimeOwner(lambda: prepared("new"), self.make_device,
                             self.persisted.append, self.published.append,
                             dispose_device=RecordingDevice.close)
        self.addCleanup(owner.close)
        requested = {"identifier": "broken", "revision": "invalid-bytes", "path": "/broken.json"}
        owner.install_startup(prepared("durable"), requested=requested, recovery_error="invalid JSON")
        status = owner.status()
        self.assertEqual(status["state"], "recovered")
        self.assertEqual(status["requested"], requested)
        self.assertEqual(status["applied"]["revision"], "durable")
        self.assertTrue(status["recovery_current"])
        self.assertEqual(status["error"], "invalid JSON")

    def test_late_metadata_cannot_overwrite_new_layout(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def old_query():
            started.set()
            if not release.wait(3):
                raise TimeoutError("metadata barrier")
            return {"value": "old"}
        self.owner.request_metadata(old_query)
        self.assertTrue(started.wait(1))
        self.request_prepared()
        self.owner.commit()
        self.owner.request_metadata(lambda: {"value": "new"})
        release.set()
        received = []
        def result_ready():
            value = self.owner.metadata_result()
            if value is not None:
                received.append(value)
            return bool(received)
        self.wait_until(result_ready)
        self.assertEqual(received, [{"value": "new"}])

    def test_late_metadata_cannot_overwrite_new_selection_same_layout(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def old_query():
            started.set()
            release.wait(3)
            return {"title": "old selection"}
        self.owner.request_metadata(old_query)
        self.assertTrue(started.wait(1))
        self.owner.invalidate_metadata()
        self.owner.request_metadata(lambda: {"title": "new selection"})
        release.set()
        result = []
        def ready():
            value = self.owner.metadata_result()
            if value is not None:
                result.append(value)
            return bool(result)
        self.wait_until(ready)
        self.assertEqual(result, [{"title": "new selection"}])

    def test_unchanged_notification_preserves_confirmed_function(self):
        self.owner.gesture.selected_function = "Volume"
        self.owner.gesture.display_title = "Volume label"
        self.owner.gesture.center_activated = True
        self.next_layout = prepared("a")
        self.request_prepared("a")
        self.assertFalse(self.owner.commit())
        self.assertEqual(self.owner.status()["state"], "applied")
        self.assertEqual(self.owner.gesture.selected_function, "Volume")
        self.owner.submit_window("unknown-application", "Different title, same fallback")
        self.assertFalse(self.owner.commit())
        self.assertTrue(self.owner.gesture.center_activated)

    def test_window_mapping_is_queued_and_selection_reset_at_boundary(self):
        self.next_layout = prepared("c", {"editor": {}, "none": {"center": [{"key": "x"}]}})
        self.request_prepared("c")
        self.owner.commit()
        self.owner.contacts.feed("BTN_TOUCH", 1)
        self.owner.contacts.feed("SYN_REPORT", 0)
        self.owner.gesture.begin(self.owner.current, 0)
        self.owner.gesture.selected_function = "old"
        self.owner.submit_window("editor", "Window")
        self.assertFalse(self.owner.commit())
        self.assertEqual(self.owner.current.profile_name, "none")
        self.owner.contacts.feed("BTN_TOUCH", 0)
        self.owner.contacts.feed("SYN_REPORT", 0)
        self.owner.gesture.finish()
        self.assertTrue(self.owner.commit())
        self.assertEqual(self.owner.current.profile, {})
        self.assertEqual(self.owner.current.profile_name, "editor")
        self.assertIsNone(self.owner.gesture.selected_function)

    def test_function_identity_and_display_title_are_distinct(self):
        self.next_layout = prepared("c", {"none": {
            "Notifications": {"command": "notify", "title": "Display label"},
            "Edit": {"clockwise": [{"key": "redo"}]},
        }})
        self.request_prepared("c")
        self.owner.commit()
        snapshot = self.owner.current
        self.assertEqual(snapshot.function_names, ("Notifications", "Edit", None, None))
        self.assertEqual(snapshot.function_titles, ("Display label", "Edit", None, None))
        self.assertEqual(snapshot.profile[snapshot.function_names[0]]["command"], "notify")


class MappingAndContactTests(unittest.TestCase):
    def test_modifier_precedence_trigger_duration_and_control_only_action(self):
        modified = {"key": "mute", "modifier": "shift", "trigger": "release", "duration": 1}
        control = {"trigger": "release", "duration": 0.5}
        profile = {"center": [modified, control]}
        self.assertIsNone(matching_action(profile, "center", False, {"shift"}, 0.5))
        self.assertIs(matching_action(profile, "center", False, {"shift"}, 1), modified)
        self.assertIsNone(matching_action(profile, "center", True, set(), 1))
        self.assertIs(matching_action(profile, "center", False, set(), 0.5), control)

    def test_empty_rule_and_ordered_binary_before_title_precedence(self):
        profiles = {"code": {}, "editor": {"center": []}, "none": {"center": [{"key": "mute"}]}}
        self.assertEqual(select_profile(profiles, "/opt/code-editor", "editor"), ("code", {}))
        self.assertEqual(select_profile(profiles, "other", "My EDITOR"), ("editor", {"center": []}))
        self.assertEqual(select_profile(profiles, "code", "editor", True)[0], "none")

    def test_metadata_icon_map_is_not_mistaken_for_action_bindings(self):
        profiles = {"none": {
            "clockwise": [{"key": ["wheel", "hires"], "event_values": [1, 120]}],
            "Notify": {"command": "notify", "icons": {"center": "center.svg"}},
            "Edit": {"counterclockwise": [{"key": ["ctrl", "z"], "modifier": "shift"}]},
        }}
        self.assertEqual(action_capabilities(profiles),
                         (frozenset(("wheel", "hires", "ctrl", "z")), frozenset(("shift",))))
        self.assertEqual(function_metadata(profiles["none"], 4),
                         (("Notify", "Edit", None, None), (None, None, None, None)))

    def test_primary_mt_position_ignores_a_released_selected_slot(self):
        contacts = ContactState()
        contacts.resynchronize({"BTN_TOUCH": True}, {0: 10, 1: -1},
                               positions={0: [100, 200], 1: [999, 999]}, slot=1)
        self.assertEqual(contacts.position(), (100, 200))
        contacts.feed("ABS_MT_SLOT", 1)
        contacts.feed("ABS_MT_POSITION_X", 888)
        self.assertEqual(contacts.position(), (100, 200))

    def test_type_a_unknown_contacts_are_conservative_after_sync(self):
        contacts = ContactState()
        contacts.resynchronize({}, None, type_a=True)
        self.assertFalse(contacts.idle)
        contacts.feed("ABS_MT_POSITION_X", 1)
        contacts.feed("ABS_MT_POSITION_Y", 2)
        contacts.feed("SYN_MT_REPORT", 0)
        contacts.feed("SYN_REPORT", 0)
        self.assertFalse(contacts.idle)
        contacts.feed("SYN_REPORT", 0)
        self.assertTrue(contacts.idle)

    def test_gesture_reset_clears_all_cross_event_state(self):
        gesture = GestureState()
        gesture.begin(object(), 10)
        gesture.selected_function = "Edit"
        gesture.display_title = "Undo"
        gesture.center_activated = True
        gesture.touch_x, gesture.touch_y = 4, 5
        gesture.last_angle, gesture.angle_accumulator = 90, 45
        gesture.pending_rotation = "clockwise"
        gesture.tap_disabled = True
        gesture.release_pending = True
        gesture.reset()
        self.assertEqual(gesture, GestureState())

    def test_geometry_uses_measured_origin_and_clockwise_top_zero(self):
        geometry = Geometry.from_layout({
            "circle_center_x": 100, "circle_center_y": 100, "circle_diameter": 100,
            "center_button_diameter": 20, "top_right_icon_width": 30,
            "top_right_icon_height": 20,
        }, {"min_x": 10, "max_x": 210, "min_y": 20, "max_y": 220})
        self.assertEqual(geometry.icon_bounds, (180, 210, 20, 40))
        self.assertEqual(geometry.position(100, 50), (50, 0))
        self.assertEqual(geometry.position(150, 100), (50, 90))


if __name__ == "__main__":
    unittest.main()
