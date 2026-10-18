"""Run the actual daemon functions without opening real input nodes."""
import ast
import math
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from libevdev import EV_ABS, EV_KEY, EV_SYN, InputEvent, device

source = Path(__file__).resolve().parents[1] / "dialpad.py"
functions = {node.name: node for node in ast.parse(source.read_text()).body
             if isinstance(node, ast.FunctionDef)}


def load(name, namespace):
    exec(compile(ast.Module([functions[name]], type_ignores=[]), str(source), "exec"), namespace)


class FakeInput:
    def __init__(self, namespace, drops=1):
        self.namespace = namespace
        self.drops = drops
        self.round = self.sync_count = 0
        self.value = {EV_KEY.KEY_LEFTSHIFT: 0, EV_KEY.KEY_LEFTALT: 1}

    def events(self):
        if self.round < self.drops:
            self.round += 1
            yield InputEvent(EV_SYN.SYN_DROPPED, 0)
            raise device.EventsDroppedException()
        self.namespace["stop_threads"] = True
        yield InputEvent(EV_KEY.KEY_A, 1)

    def sync(self):
        self.sync_count += 1
        yield InputEvent(EV_KEY.KEY_LEFTSHIFT, 0)


class InputRecoveryTests(unittest.TestCase):
    def namespace(self):
        return dict(stop_threads=False, device=device, EV_SYN=EV_SYN, EV_KEY=EV_KEY,
                    EV_ABS=EV_ABS, InputEvent=InputEvent, dev=Mock(), log=Mock())

    def test_many_overruns_sync_physical_device_without_recursion(self):
        namespace = self.namespace()
        load("iter_input_events", namespace)
        physical = FakeInput(namespace, drops=1500)
        events = list(namespace["iter_input_events"](physical))
        self.assertEqual(physical.sync_count, 1500)
        self.assertEqual(len(events), 1501)
        self.assertTrue(all(event.matches(EV_SYN.SYN_DROPPED) for event in events[:-1]))
        self.assertTrue(events[-1].matches(EV_KEY.KEY_A))
        namespace["dev"].sync.assert_not_called()

    def test_sync_events_are_not_replayed_as_shortcuts(self):
        namespace = self.namespace()
        load("iter_input_events", namespace)
        events = list(namespace["iter_input_events"](FakeInput(namespace)))
        self.assertFalse(any(event.matches(EV_KEY.KEY_LEFTSHIFT) for event in events))

    def test_keyboard_resumes_with_actual_modifier_state(self):
        namespace = self.namespace()
        namespace.update(keyboard="9", active_modifiers={EV_KEY.KEY_LEFTSHIFT},
                         modifiers={EV_KEY.KEY_LEFTSHIFT}, coactivator_modifiers={EV_KEY.KEY_LEFTALT})
        physical = FakeInput(namespace)
        namespace["Device"] = lambda _: physical
        load("iter_input_events", namespace)
        load("listen_keyboard_events", namespace)
        with patch("builtins.open", return_value=Mock()):
            namespace["listen_keyboard_events"]()
        self.assertEqual(namespace["active_modifiers"], {EV_KEY.KEY_LEFTALT})
        namespace["log"].error.assert_not_called()

    def test_interrupted_center_gesture_releases_pointer_without_running_command(self):
        namespace = self.namespace()
        namespace.update(circle_diameter=919, center_button_diameter=364,
            circle_center_x=586, circle_center_y=573, max_x=4762,
            top_right_icon_width=250, top_right_icon_height=250,
            slices_minimum_count=3, activation_time=1, dialpad=True,
            active_modifiers=set(), coactivator_keys=[],
            multi_app_mode_titles=["Volume", "Scroll", "Brightness"],
            multi_app_mode_icons=[None]*3, app_specific_shortcuts={"Volume": {}},
            window_binary=None, multi_app_mode=True, socket_enabled=True,
            default_treshold=20, socket_send_progress_above_treshold=120,
            key_press_times={}, math=math, time=lambda: 1,
            send_to_socket=Mock(), set_touchpad_prop_send_events=Mock(),
            emulate_shortcuts=Mock(), execute_command=Mock())

        class InterruptedInput(FakeInput):
            def events(self):
                if self.round == 0:
                    self.round += 1
                    # Select Volume, move into the center, then lose events.
                    for code, value in [(EV_KEY.BTN_TOOL_FINGER, 1),
                                        (EV_ABS.ABS_MT_POSITION_X, 936),
                                        (EV_ABS.ABS_MT_POSITION_Y, 573),
                                        (EV_ABS.ABS_MT_POSITION_X, 586)]:
                        yield InputEvent(code, value)
                    yield InputEvent(EV_SYN.SYN_DROPPED, 0)
                    raise device.EventsDroppedException()
                self.namespace["stop_threads"] = True
                # A multi-contact device can lift all contacts without a
                # BTN_TOOL_FINGER change (it was already zero). BTN_TOUCH is
                # the reliable release signal, and a later gesture must work.
                self.value[EV_KEY.BTN_TOUCH] = 0
                yield InputEvent(EV_KEY.BTN_TOUCH, 0)
                for code, value in [(EV_KEY.BTN_TOOL_FINGER, 1),
                                    (EV_ABS.ABS_MT_POSITION_X, 936),
                                    (EV_ABS.ABS_MT_POSITION_Y, 573)]:
                    yield InputEvent(code, value)
                yield InputEvent(EV_KEY.BTN_TOOL_FINGER, 0)

        physical = InterruptedInput(namespace)
        physical.value = {EV_KEY.BTN_TOUCH: 1, EV_KEY.BTN_TOOL_FINGER: 0}
        namespace["d_t"] = physical
        load("iter_input_events", namespace)
        load("listen_touchpad_events", namespace)
        namespace["listen_touchpad_events"]()
        namespace["log"].exception.assert_not_called()
        namespace["emulate_shortcuts"].assert_not_called()
        namespace["execute_command"].assert_not_called()
        self.assertEqual(namespace["set_touchpad_prop_send_events"].call_args.args, (1,))
        self.assertEqual([call.args for call in namespace["set_touchpad_prop_send_events"].call_args_list],
                         [(0,), (1,), (0,), (1,)])
        self.assertEqual(physical.sync_count, 1)


if __name__ == "__main__":
    unittest.main()
