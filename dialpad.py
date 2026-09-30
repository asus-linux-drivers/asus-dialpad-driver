#!/usr/bin/env python3

import logging
import os
import sys
import threading
from time import sleep, time

# https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/48
X11_LIBS_AVAILABLE = False
try:
    import Xlib.display
    import Xlib.X
    import Xlib.XK
    import xcffib
    import xcffib.xkb
    X11_LIBS_AVAILABLE = True
except:
    pass

from xkbcommon import xkb
from libevdev import EV_ABS, EV_KEY, EV_SYN, Device, InputEvent, device
from pyinotify import (
    WatchManager, Notifier, ProcessEvent, IN_CLOSE_WRITE, IN_MOVED_TO,
    IN_MOVED_FROM, IN_CREATE, IN_DELETE, IN_DELETE_SELF, IN_MOVE_SELF, IN_IGNORED,
    IN_Q_OVERFLOW,
)
from periphery import I2C
from typing import Optional
import re
import math
import subprocess
import configparser
import signal
import mmap
import shutil
import glob
import socket
import json
import selectors
from pathlib import Path

from dialpad_layout import resolve_layout, revision_bytes
from dialpad_layout_linux import (
    load_layout, compile_layout, validate_device_geometry, read_config,
    update_config, requested_layout, save_recovery, load_recovery, StatusServer,
    close_virtual_device, select_keyboard_device, uses_gnome_input_sources,
    read_gnome_input_source,
)
from dialpad_runtime import (
    CONTACT_KEYS, CandidateError, Geometry, PreparedLayout, RuntimeOwner,
    action_capabilities, matching_action, source_status,
)
SOCKET_PATH = "/tmp/dialpad.sock"
sock = None

def init_socket():
    global sock

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        log.info("DialPad socket initialized at %s", SOCKET_PATH)

    except Exception as e:
        log.error("Failed to initialize socket: %s", e)
        sock = None

PYWAYLAND_AVAILABLE = False
try:
    from pywayland.client import Display
    from pywayland.protocol.wayland import WlSeat
    PYWAYLAND_AVAILABLE = True
except:
    pass

SYSTEMD_JOURNAL_AVAILABLE = False
try:
    from systemd.journal import JournalHandler
    SYSTEMD_JOURNAL_AVAILABLE = True
except:
    pass

PYATSPI_AVAILABLE = False
try:
    import pyatspi
    PYATSPI_AVAILABLE = True
except:
    pass

QDBUS = shutil.which("qdbus") or shutil.which("qdbus6") or shutil.which("qdbus-qt6")

# Logging setup
logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s',
    level=os.environ.get('LOG', 'INFO')
)
log = logging.getLogger('asus-dialpad-driver')
if SYSTEMD_JOURNAL_AVAILABLE:
    log.addHandler(JournalHandler())

xauth_in_tmp_dir = glob.glob('/tmp/xauth_*')
if len(xauth_in_tmp_dir) > 0:
  latest_xauth_file = max(xauth_in_tmp_dir, key=os.path.getmtime)   
  os.environ['XAUTHORITY'] = latest_xauth_file
  log.info("X11 has xauth file in /tmp folder with filename changed each boot, currently {}".format(os.environ['XAUTHORITY']))

# Detect session type
xdg_session_type = os.environ.get('XDG_SESSION_TYPE')
if not xdg_session_type:
    log.error("XDG session type is not set. Exiting.")
    sys.exit(1)

# Setup display for X11
display = None
xkb_conn = None
display_var = None
display_wayland = None
display_wayland_var = None
keymap_loaded = False
multi_app_mode = None
multi_app_mode_titles = None
multi_app_mode_icons = None
coactivator_keys = None

if xdg_session_type == "x11":

    if not X11_LIBS_AVAILABLE:
        log.error("X11 libraries are not available. Please install python-xlib and xcffib.")
        sys.exit(1)

    try:
        display_var = os.environ.get('DISPLAY')
        display = Xlib.display.Display(display_var)
        log.info("X11 detected and connected succesfully to the display {}".format(display_var))
        xkb_conn = xcffib.connect()
        xkb_ext = xkb_conn(xcffib.xkb.key)
        xkb_ext.UseExtension(
            xcffib.xkb.MAJOR_VERSION,
            xcffib.xkb.MINOR_VERSION
        ).reply()

        xkb_conn_setup = xkb_conn.get_setup()
        log.info("X11 detected and connected succesfully to the xcffib")
    except Exception as e:
        log.error(f"Failed to connect to X11 display: {e}")
        sys.exit(1)
else:
    if not PYWAYLAND_AVAILABLE:
        log.error("Wayland library is not available. Please install pywayland.")
        sys.exit(1)

    try:
        display_wayland_var = os.environ.get('WAYLAND_DISPLAY')
        display_wayland = Display(display_wayland_var)
        display_wayland.connect()
        log.info("Wayland session detected and connected.")
    except Exception as e:
        log.error(f"Failed to connect to Wayland display: {e}")
        sys.exit(1)

dialpad: bool = False

# Keep the positional interface. An argv fallback is never persisted as layout.
model = sys.argv[1] if len(sys.argv) > 1 else None
config_file_dir = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else ".")
install_dir = Path(__file__).resolve().parent
trusted_python = "--trusted-python" in sys.argv[3:]
runtime = None
status_server = None
startup_requested = {"identifier": model, "revision": None, "path": None}
startup_recovery_error = None
try:
    selection = requested_layout(config_file_dir, model)
    startup_requested["identifier"] = selection
    startup_loaded = load_layout(selection, config_file_dir, install_dir,
                                 trusted_python=trusted_python)
    startup_requested = source_status(startup_loaded)
except Exception as error:
    startup_requested = getattr(error, "requested", None) or startup_requested
    startup_recovery_error = str(error)
    try:
        startup_loaded = load_recovery(config_file_dir)
        log.error("Requested layout rejected; recovering %s at %s: %s",
                  startup_loaded.source.identifier, startup_loaded.revision, error)
    except Exception as recovery_error:
        log.error("Cannot load requested layout (%s) or recovery (%s)", error, recovery_error)
        sys.exit(1)

# Figure out devices from devices file
touchpad: Optional[str] = None
touchpad_name: Optional[str] = None
device_id: Optional[str] = None
keyboard_device_id: Optional[str] = None
device_addr: Optional[int] = None
keyboard: Optional[str] = None

# Constants
try_times = 5
try_sleep = 0.1

# Look into the devices file #
while try_times > 0:

    touchpad_detected = 0
    keyboard_detected = 0

    with open('/proc/bus/input/devices', 'r') as f:
        devices_text = f.read()
        lines = devices_text.splitlines()
        keyboard = select_keyboard_device(devices_text)
        if keyboard is not None:
            keyboard_detected = 2
            log.info("Using physical keyboard /dev/input/event%s", keyboard)
        for line in lines:
            # Look for the touchpad #

            # https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver/issues/87
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/95
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/110
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/161
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/198
            # https://github.com/asus-linux-drivers/asus-stylus-driver/issues/17
            if (touchpad_detected == 0 and ("Name=\"ASUE" in line or "Name=\"ELAN" in line or "Name=\"ASUP" in line or "Name=\"ASUF" in line or "Name=\"ASCE" in line or "Name=\"ASCF" in line or "Name=\"ASCP" in line) and "Touchpad" in line and not "9009" in line and not "9008" in line):
                touchpad_detected = 1
                log.info('Detecting touchpad from string: \"%s\"', line.strip())
                touchpad_name = line.split("\"")[1]

                # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/161
                if ("ASUF1416" in line or "ASUF1205" in line or "ASUF1204" in line):
                  device_addr = 0x38
                else:
                  device_addr = 0x15

            if touchpad_detected == 1:
                if "S: " in line:
                    # search device id
                    device_id = re.sub(r".*i2c-(\d+)/.*$", r'\1', line).replace("\n", "")
                    log.info('Set touchpad device id %s from %s', device_id, line.strip())

                if "H: " in line:
                    touchpad = line.split("event")[1]
                    touchpad = touchpad.split(" ")[0]
                    touchpad_detected = 2
                    log.info('Set touchpad id %s from %s', touchpad, line.strip())


    if touchpad_detected != 2 or keyboard_detected != 2:
        try_times -= 1
        if try_times == 0:
            with open('/proc/bus/input/devices', 'r') as f:
                lines = f.readlines()
                for line in lines:
                    log.error(line)
            if keyboard_detected != 2:
                log.error("Can't find keyboard (code: %s)", keyboard_detected)
                # keyboard is optional, no sys.exit(1)!
            if touchpad_detected != 2:
                log.error("Can't find touchpad (code: %s)", touchpad_detected)
                sys.exit(1)
            if touchpad_detected == 2 and not device_id.isnumeric():
                log.error("Can't find device id")
                sys.exit(1)
    else:
        break

    sleep(try_sleep)

# Open a handle to "/dev/i2c-x", representing the I2C bus
path = f"/dev/i2c-{device_id}"
try:
    # Prefer python-periphery if it works
    i2c = I2C(path)
    i2c.close()
    log.debug("Successfully opened I2C bus via python-periphery at %s", path)
except Exception as e:
    log.debug("periphery.I2C failed to open %s: %s, trying raw open()", path, e)
    try:
        # Fallback: try to open the device file directly
        with open(path, "rb+", buffering=0) as f:
            pass
        log.debug("Successfully opened I2C bus via raw open() at %s", path)
    except Exception as e2:
        log.error(
            "Can not open the I2C bus connection (id: %s) at %s: %s",
            device_id,
            path,
            e2,
        )
        sys.exit(1)

# Config
CONFIG_FILE_NAME = "dialpad_dev"
CONFIG_SECTION = "main"
CONFIG_ENABLED = "enabled"
CONFIG_SOCKET_ENABLED = "socket_enabled"
CONFIG_SOCKET_ENABLED_DEFAULT = True
CONFIG_ENABLED_DEFAULT = False
CONFIG_SLICES_MINIMUM_COUNT = "slices_minimum_count"
CONFIG_SLICES_MINIMUM_COUNT_DEFAULT = 4
CONFIG_DEFAULT_TRESHOLD = "default_treshold"
CONFIG_DEFAULT_TRESHOLD_DEFAULT = 90
CONFIG_DISABLE_DUE_INACTIVITY_TIME = "disable_due_inactivity_time"
CONFIG_DISABLE_DUE_INACTIVITY_TIME_DEFAULT = 120
CONFIG_TOUCHPAD_DISABLES_DIALPAD = "touchpad_disables_dialpad"
CONFIG_TOUCHPAD_DISABLES_DIALPAD_DEFAULT = True
CONFIG_ACTIVATION_TIME = "activation_time"
CONFIG_ACTIVATION_TIME_DEFAULT = True
CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS = "config_supress_app_specifics_shortcuts"
CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS_DEFAULT = False
CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY = "top_right_icon_coactivator_key"
CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY_DEFAULT = ""  # Empty means no co-activator required
CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD = "socket_send_progress_above_treshold"
CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD_DEFAULT = 120

config_file_path = os.path.join(config_file_dir, CONFIG_FILE_NAME)
config = configparser.ConfigParser(interpolation=None)
config_lock = threading.Lock()
CONFIG_DEFAULTS = {
    CONFIG_ENABLED: CONFIG_ENABLED_DEFAULT,
    CONFIG_SOCKET_ENABLED: CONFIG_SOCKET_ENABLED_DEFAULT,
    CONFIG_SLICES_MINIMUM_COUNT: CONFIG_SLICES_MINIMUM_COUNT_DEFAULT,
    CONFIG_DEFAULT_TRESHOLD: CONFIG_DEFAULT_TRESHOLD_DEFAULT,
    CONFIG_DISABLE_DUE_INACTIVITY_TIME: CONFIG_DISABLE_DUE_INACTIVITY_TIME_DEFAULT,
    CONFIG_TOUCHPAD_DISABLES_DIALPAD: CONFIG_TOUCHPAD_DISABLES_DIALPAD_DEFAULT,
    CONFIG_ACTIVATION_TIME: CONFIG_ACTIVATION_TIME_DEFAULT,
    CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS: CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS_DEFAULT,
    CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY: CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY_DEFAULT,
    CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD: CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD_DEFAULT,
}

# libevdev.events() is nonblocking only if the actual input fd is nonblocking.
fd_t = open('/dev/input/event' + str(touchpad), 'rb', buffering=0)
os.set_blocking(fd_t.fileno(), False)
d_t = Device(fd_t)

# Get touchpad dimensions
abs_x = d_t.absinfo[EV_ABS.ABS_X]
abs_y = d_t.absinfo[EV_ABS.ABS_Y]
min_x, max_x = abs_x.minimum, abs_x.maximum
min_y, max_y = abs_y.minimum, abs_y.maximum
log.info('Touchpad min-max: x %d-%d, y %d-%d', min_x, max_x, min_y, max_y)
device_bounds = {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y}

last_event_time = 0

def send_to_socket(payload: dict):
    if not sock:
        return

    msg = {
        "ts": time(),
        **payload
    }

    try:
        sock.sendto(
            (json.dumps(msg) + "\n").encode("utf-8"),
            SOCKET_PATH
        )
    except Exception:
        pass

def config_set(key, value):
    global config
    # Only this key is dirty. The adapter re-reads under its stable sidecar lock.
    # Never wait for a runtime request while holding this or the adapter's lock.
    with config_lock:
        config = update_config(config_file_dir, {key: value})
    if runtime is not None:
        # Invalidate an already prepared stale enabled/settings snapshot now;
        # the later inotify notification may safely coalesce with this request.
        runtime.request()
    return value


def parse_settings(parser):
    def raw(key):
        default = CONFIG_DEFAULTS[key]
        fallback = str(int(default)) if isinstance(default, bool) else str(default)
        return parser.get(CONFIG_SECTION, key, fallback=fallback)

    def boolean(key):
        value = raw(key).lower()
        if value in ("1", "yes", "true", "on"):
            return True
        if value in ("0", "no", "false", "off"):
            return False
        raise ValueError(f"Invalid boolean [main].{key}: {value}")

    def number(key, integer=False):
        value = int(raw(key)) if integer else float(raw(key))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid nonnegative [main].{key}: {value}")
        return value

    settings = {
        "enabled": boolean(CONFIG_ENABLED),
        "socket": boolean(CONFIG_SOCKET_ENABLED),
        "slices": number(CONFIG_SLICES_MINIMUM_COUNT, True),
        "threshold": number(CONFIG_DEFAULT_TRESHOLD),
        "disable_time": number(CONFIG_DISABLE_DUE_INACTIVITY_TIME),
        "touchpad_disables": boolean(CONFIG_TOUCHPAD_DISABLES_DIALPAD),
        "activation_time": number(CONFIG_ACTIVATION_TIME),
        "suppress": boolean(CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS),
        "coactivator_names": tuple(raw(CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY).split()),
        "progress_threshold": number(CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD),
    }
    if settings["slices"] < 1 or settings["threshold"] <= 0:
        raise ValueError("slices_minimum_count and default_treshold must be positive")
    return settings


def prepare_loaded_layout(loaded, settings):
    validate_device_geometry(loaded.layout, device_bounds)
    compiled = compile_layout(loaded.layout)
    capabilities, modifier_keys = action_capabilities(compiled)
    return PreparedLayout(loaded, compiled, Geometry.from_layout(loaded.layout.geometry, device_bounds),
                          settings, capabilities, modifier_keys)


def prepare_requested_layout():
    requested = {"identifier": None, "revision": None, "path": None}
    try:
        parser = read_config(config_file_dir)
        selection = parser.get("main", "layout", fallback="").strip() or model
        requested["identifier"] = selection
        source = resolve_layout(selection, config_file_dir, install_dir)
        requested["path"] = str(source.path)
        loaded = load_layout(selection, config_file_dir, install_dir, trusted_python=trusted_python)
        requested = source_status(loaded)
        return prepare_loaded_layout(loaded, parse_settings(parser))
    except Exception as error:
        raise CandidateError(str(error), getattr(error, "requested", None) or requested) from error




def load_all_config_values(settings):
    global disable_due_inactivity_time, touchpad_disables_dialpad, activation_time
    global slices_minimum_count, default_treshold, suppress_app_specifics_shortcuts
    global coactivator_keys, socket_enabled, socket_send_progress_above_treshold
    disable_due_inactivity_time = settings["disable_time"]
    touchpad_disables_dialpad = settings["touchpad_disables"]
    activation_time = settings["activation_time"]
    slices_minimum_count = settings["slices"]
    default_treshold = settings["threshold"]
    suppress_app_specifics_shortcuts = settings["suppress"]
    coactivator_keys = settings["coactivator_names"]
    socket_enabled = settings["socket"]
    socket_send_progress_above_treshold = settings["progress_threshold"]
    if settings["enabled"] != dialpad:
        if settings["enabled"]:
            activate_dialpad(persist=False)
        else:
            deactivate_dialpad(persist=False)

def send_value_to_touchpad_via_i2c(value):
    global device_id, device_addr

    data = [
        0x05, 0x00, 0x3d, 0x03, 0x06, 0x00, 0x07, 0x00,
        0x0d, 0x14, 0x03, int(value, 16), 0xad,
    ]

    path = f"/dev/i2c-{device_id}"

    # https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/44
    # 1) Try i2ctransfer first
    try:
        hex_data = [f"0x{b:02x}" for b in data]
        cmd = [
            "i2ctransfer",
            "-f", "-y",
            str(device_id),
            f"w{len(data)}@0x{device_addr:x}",
        ] + hex_data

        log.debug("Trying I2C via i2ctransfer: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True)
        log.debug("I2C transfer successful via i2ctransfer")
        return True

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode().strip() if e.stderr else str(e)
        log.error("i2ctransfer failed: %s; falling back to python-periphery", stderr)

    except Exception as e:
        log.error("Error during i2ctransfer: %s; falling back to python-periphery", e)

    # 2) Fallback: python-periphery
    try:
        with I2C(path) as i2c:
            msg = I2C.Message(data)
            i2c.transfer(device_addr, [msg])
            log.debug("Sent I2C data via python-periphery on %s", path)
            return True

    except Exception as e:
        log.error("periphery.I2C transfer failed on %s: %s", path, e)

    return False

x11_last_set_layout = None


def prepare_virtual_device(prepared, context, previous):
    global x11_last_set_layout
    layout_name = context.get("layout_name")
    if (display and context.get("set_x11_layout") and layout_name and
            x11_last_set_layout != layout_name):
        subprocess.run(["setxkbmap", layout_name, "-display", display_var], check=True)
        x11_last_set_layout = layout_name
    # Layout bindings are explicit evdev codes, not guesses from keyboard symbols.
    # Keymap changes only affect named co-activators, resolved before publication.
    coactivators = resolve_coactivators(prepared.settings["coactivator_names"], context)
    capabilities = prepared.capabilities
    if previous is None and not capabilities:
        return None, capabilities, coactivators
    if previous is not None and capabilities <= previous.capabilities:
        return previous.device, previous.capabilities, coactivators
    candidate = Device()
    candidate.name = " ".join(touchpad_name.split()[:2]) + " DialPad"
    for code in capabilities:
        candidate.enable(code)
    output = candidate.create_uinput_device()
    # Preserve the existing compositor discovery delay, with the old device alive.
    sleep(0.5)
    return output, capabilities, coactivators


def publish_runtime_snapshot(snapshot):
    global multi_app_mode, multi_app_mode_titles, multi_app_mode_icons
    multi_app_mode = any(name is not None for name in snapshot.function_names)
    multi_app_mode_titles = list(snapshot.function_titles)
    multi_app_mode_icons = list(snapshot.function_icons)
    load_all_config_values(snapshot.prepared.settings)
    if socket_enabled:
        send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons,
                        "title": None, "selected_index": None, "value": None})
        request_ring_metadata(snapshot)

def get_active_window_gnome_wayland_title():
    global gnome_failure_count, gnome_max_failure_count

    if gnome_failure_count >= gnome_max_failure_count:
        return None

    try:
        session_bus = dbus.SessionBus()
        shell = session_bus.get_object('org.gnome.Shell', '/org/gnome/Shell')
        active_window = shell.Get('org.gnome.Shell', 'focusWindow')
        return active_window.get('title', None)
    except Exception as e:
        gnome_failure_count += 1
        log.debug("GNOME window title fetch failed (%d/%d): %s", gnome_failure_count, gnome_max_failure_count, e)
        return None

def binary_from_pid(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except Exception:
        return None
    
def get_active_window_info_x11():
    try:
        root = display.screen().root
        window_id = root.get_full_property(
            display.intern_atom('_NET_ACTIVE_WINDOW'),
            Xlib.X.AnyPropertyType
        ).value[0]

        window = display.create_resource_object('window', window_id)

        # title
        window_name = window.get_full_property(
            display.intern_atom('_NET_WM_NAME'),
            Xlib.X.AnyPropertyType
        )
        title = window_name.value.decode() if window_name else None

        # pid -> elf
        pid_prop = window.get_full_property(
            display.intern_atom('_NET_WM_PID'),
            Xlib.X.AnyPropertyType
        )
        binary = binary_from_pid(pid_prop.value[0]) if pid_prop else None

        return binary, title

    except Exception as e:
        log.debug("Error retrieving active window info (X11): %s", e)
        return None, None

gsettings_failure_count = 0
gsettings_max_failure_count = 1

qdbus_failure_count = 0
qdbus_max_failure_count = 1

if QDBUS is None:
    qdbus_failure_count = qdbus_max_failure_count

gnome_failure_count = 0
gnome_max_failure_count = 1

xinput_failure_count = 0
xinput_max_failure_count = 1

synclient_status_failure_count = 0
synclient_status_max_failure_count = 1

niri_failure_count = 0
niri_max_failure_count = 1

sway_failure_count = 0
sway_max_failure_count = 1

hyprland_failure_count = 0
hyprland_max_failure_count = 1

def get_active_window_info_kde_wayland():
    global qdbus_failure_count, qdbus_max_failure_count

    if qdbus_failure_count >= qdbus_max_failure_count:
        return None, None

    try:
        win_id = subprocess.check_output([
            QDBUS, 'org.kde.KWin', '/KWin', 'org.kde.KWin.activeWindow'
        ]).decode().strip()

        title = subprocess.check_output([
            QDBUS, 'org.kde.KWin',
            f'/org/kde/KWin/Window/{win_id}',
            'org.kde.KWin.Window.caption'
        ]).decode().strip()

        pid = subprocess.check_output([
            QDBUS, 'org.kde.KWin',
            f'/org/kde/KWin/Window/{win_id}',
            'org.kde.KWin.Window.pid'
        ]).decode().strip()

        binary = binary_from_pid(pid)

        return binary, title

    except Exception as e:
        qdbus_failure_count += 1
        log.debug(
            "KDE Wayland window fetch failed (%d/%d): %s",
            qdbus_failure_count,
            qdbus_max_failure_count,
            e
        )
        return None, None

def get_active_window_info_gnome_wayland():
    title = get_active_window_gnome_wayland_title()
    return None, title

def get_active_window_info_niri():
    global niri_failure_count, niri_max_failure_count

    if niri_failure_count >= niri_max_failure_count:
        return None, None

    try:
        out = subprocess.check_output(
            ['niri', 'msg', '--json', 'focused-window'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        win = json.loads(out)
        title = win.get('title')
        pid = win.get('pid')
        binary = binary_from_pid(pid) if pid else None
        return binary, title
    except Exception as e:
        niri_failure_count += 1
        log.debug("niri window fetch failed (%d/%d): %s", niri_failure_count, niri_max_failure_count, e)
        return None, None

def get_active_window_info_sway():
    global sway_failure_count, sway_max_failure_count

    if sway_failure_count >= sway_max_failure_count:
        return None, None

    try:
        out = subprocess.check_output(
            ['swaymsg', '-t', 'get_tree'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        tree = json.loads(out)

        def find_focused(node):
            if node.get('focused'):
                return node
            for child in node.get('nodes', []) + node.get('floating_nodes', []):
                result = find_focused(child)
                if result:
                    return result
            return None

        focused = find_focused(tree)
        if focused:
            title = focused.get('name')
            pid = focused.get('pid')
            binary = binary_from_pid(pid) if pid else None
            return binary, title
        return None, None
    except Exception as e:
        sway_failure_count += 1
        log.debug("sway window fetch failed (%d/%d): %s", sway_failure_count, sway_max_failure_count, e)
        return None, None

def get_active_window_info_hyprland():
    global hyprland_failure_count, hyprland_max_failure_count

    if hyprland_failure_count >= hyprland_max_failure_count:
        return None, None

    try:
        out = subprocess.check_output(
            ['hyprctl', 'activewindow', '-j'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        win = json.loads(out)
        title = win.get('title')
        pid = win.get('pid')
        binary = binary_from_pid(pid) if pid else None
        return binary, title
    except Exception as e:
        hyprland_failure_count += 1
        log.debug("Hyprland window fetch failed (%d/%d): %s", hyprland_failure_count, hyprland_max_failure_count, e)
        return None, None

def get_active_window_title():

    if xdg_session_type == "x11" and display:
        binary, title = get_active_window_info_x11()
        if binary or title:
            return binary, title
    else:
        binary, title = get_active_window_info_kde_wayland()
        if binary or title:
            return binary, title

        binary, title = get_active_window_info_gnome_wayland()
        if title:
            return binary, title

        binary, title = get_active_window_info_niri()
        if binary or title:
            return binary, title

        binary, title = get_active_window_info_sway()
        if binary or title:
            return binary, title

        binary, title = get_active_window_info_hyprland()
        if binary or title:
            return binary, title

    log.debug("Unsupported session type or display not connected.")

    return None, None


def emulate_shortcuts(shortcuts, touch_input, pressed, held_modifiers, duration_held=0):
    shortcut = matching_action(shortcuts, touch_input, pressed, held_modifiers, duration_held)
    if shortcut is None:
        return None
    if shortcut.get("key") is not None:
        send_key_event(shortcut["key"], shortcut.get("event_values"))
    if shortcut.get("command"):
        execute_command(shortcut["command"])
    return shortcut

def send_key_event(key_code, event_values=None):
    codes = key_code if isinstance(key_code, (list, tuple)) else (key_code,)
    try:
        if event_values is not None:
            events = [InputEvent(code, value) for code, value in zip(codes, event_values)]
            events.append(InputEvent(EV_SYN.SYN_REPORT, 0))
            runtime.output.send(events)
        else:
            presses = [InputEvent(code, 1) for code in codes]
            presses.append(InputEvent(EV_SYN.SYN_REPORT, 0))
            releases = [InputEvent(code, 0) for code in reversed(codes)]
            releases.append(InputEvent(EV_SYN.SYN_REPORT, 0))
            runtime.output.send(presses, releases)
    except Exception:
        log.exception("Error sending key event")

def activate_dialpad(persist=True):
    global dialpad, multi_app_mode_titles, multi_app_mode_icons

    # unlock
    send_value_to_touchpad_via_i2c("0x60")
    # activate
    send_value_to_touchpad_via_i2c("0x01")

    if persist:
        config_set(CONFIG_ENABLED, True)

    #set_touchpad_prop_tap_to_click(false)

    dialpad = True

    if socket_enabled:
        send_to_socket({CONFIG_ENABLED: dialpad})

        if multi_app_mode:
            send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons,
                            "title": None, "selected_index": None})

def deactivate_dialpad(persist=True):
    global dialpad

    # lock
    send_value_to_touchpad_via_i2c("0x61")
    # deactivate
    send_value_to_touchpad_via_i2c("0x00")

    if persist:
        config_set(CONFIG_ENABLED, False)

    dialpad = False

    if socket_enabled:
        send_to_socket({CONFIG_ENABLED: dialpad})

# Function to enable/disable the DialPad
def toggle_top_right_icon(current_state_is_enabled):

    if current_state_is_enabled:
        deactivate_dialpad()
    else:
        activate_dialpad()

    log.info(f"Toggling top-right icon: {'Disabling' if current_state_is_enabled else 'Enabling'}")




def qdbusSet(cmd):
    global qdbus_failure_count, qdbus_max_failure_count

    if qdbus_failure_count < qdbus_max_failure_count:
        try:
            sudo_user = os.environ.get('SUDO_USER')

            if sudo_user is not None:
                final_cmd = ['runuser', '-u', sudo_user] + cmd
            else:
                final_cmd = cmd

            log.debug(final_cmd)
            ret = subprocess.call(final_cmd)

            if ret != 0:
                raise subprocess.CalledProcessError(ret, final_cmd)

        except Exception as e:
            log.debug(e, exc_info=True)
            qdbus_failure_count+=1
    else:
        log.debug('Qdbus failed more then: \"%s\" so is not try anymore', qdbus_max_failure_count)

def qdbusSetTouchpadEnabled(value):
    cmd = [
        QDBUS,
        'org.kde.KWin',
        f'/org/kde/KWin/InputDevice/event{touchpad}',
        'org.freedesktop.DBus.Properties.Set',
        'org.kde.KWin.InputDevice',
        'enabled',
        str(bool(value)).lower()
    ]
    qdbusSet(cmd)

def gsettingsSet(path, name, value):
    global gsettings_failure_count, gsettings_max_failure_count

    if gsettings_failure_count < gsettings_max_failure_count:
        try:
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user is not None:
                cmd = ['runuser', '-u', sudo_user, 'gsettings', 'set', path, name, str(value)]
            else:
                cmd = ['gsettings', 'set', path, name, str(value)]

            log.debug(cmd)
            ret = subprocess.call(cmd)
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)
        except Exception as e:
            log.debug(e, exc_info=True)
            gsettings_failure_count+=1
    else:
        log.debug('Gsettings failed more than: "%s" so is not trying anymore', gsettings_max_failure_count)

def gsettingsSetTouchpadSendEvents(value):
    gsettingsSet('org.gnome.desktop.peripherals.touchpad', 'send-events', 'enabled' if value else 'disabled')

def set_touchpad_prop_send_events(value):
    global touchpad_name, gsettings_failure_count, gsettings_max_failure_count, qdbus_max_failure_count, qdbus_failure_count, xinput_failure_count, xinput_max_failure_count, synclient_status_failure_count, synclient_status_max_failure_count

    # 1. priority - gsettings (gnome) or qdbus (kde)
    if gsettings_failure_count < gsettings_max_failure_count:
        gsettingsSetTouchpadSendEvents(value)
    if qdbus_failure_count < qdbus_max_failure_count:
        qdbusSetTouchpadEnabled(value)
    else:
        log.debug('Qdbus failed more than: "%s" so is not trying anymore', qdbus_max_failure_count)

    # 2. priority - xinput
    if xinput_failure_count > xinput_max_failure_count:
        log.debug('Setting libinput Send Events via xinput failed more than: "%s" times so is not trying anymore', xinput_max_failure_count)
    else:
        try:
            cmd = ["xinput", "enable" if value else "disable", touchpad_name]
            log.debug(cmd)
            subprocess.call(cmd)
            ret = subprocess.call(cmd)
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)
            return
        except:
            xinput_failure_count+=1
            log.debug('Setting libinput Send Events via xinput failed')

    # 3. priority - synclient
    if synclient_status_failure_count > synclient_status_max_failure_count:
        log.debug('Setting libinput Send Events via synclient failed more than: "%s" times so is not trying anymore', xinput_max_failure_count)
    try:
        cmd = ["synclient", "TouchpadOff=" + str(value)]
        log.debug(cmd)
        subprocess.call(cmd)
        ret = subprocess.call(cmd)
        if ret != 0:
            raise subprocess.CalledProcessError(ret, cmd)
        return
    except:
        synclient_status_failure_count+=1


def are_modifier_keys_pressed(modifier_names):
    if not modifier_names:
        return True
    return bool(runtime.current.coactivators) and runtime.current.coactivators <= pressed_keys


def get_current_value(shortcut_entry):
    value = shortcut_entry.get("value_query")
    if value:
        try:
            result = subprocess.check_output(value, shell=True).decode().strip()
            return result
        except Exception as e:
            log.debug(f"Failed to get value for shortcut: {e}")
            return None
    return None


def execute_command(command):
    try:
        subprocess.run(command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to execute command: {e}")


pressed_keys = frozenset()


def request_ring_metadata(snapshot):
    # Commands execute on the metadata worker, never in commit/output locks.
    # Epoch plus query serial fences results from an old layout or selection.
    names = snapshot.function_names
    profile, titles = snapshot.profile, snapshot.function_titles
    previous_icons = tuple(multi_app_mode_icons)
    def query():
        icons = list(previous_icons)
        for index, name in enumerate(names):
            if name is not None:
                entry = profile[name]
                if entry.get("icons") and entry.get("value_query"):
                    icons[index] = entry["icons"].get(get_current_value(entry), icons[index])
        return {"titles": list(titles), "icons": icons, "title": None, "selected_index": None}
    runtime.request_metadata(query)


def request_action_metadata(entry, fallback, payload):
    if not socket_enabled:
        return
    def query():
        value = get_current_value(entry)
        if value is None:
            value = get_current_value(fallback)
        return {**payload, "value": value, "unit": entry.get("unit", fallback.get("unit"))}
    runtime.request_metadata(query)

def gesture_modifiers(snapshot):
    return pressed_keys & snapshot.prepared.modifiers


def gesture_feedback(gesture):
    """Keep the pinned function identity separate from its display title."""
    selected_index = (gesture.snapshot.function_names.index(gesture.selected_function)
                      if gesture.selected_function is not None else None)
    return {"selected_index": selected_index, "title": gesture.display_title}


def center_action(gesture, pressed, now):
    snapshot = gesture.snapshot
    profile = snapshot.profile
    selected = profile.get(gesture.selected_function, {}) if gesture.selected_function else {}
    mapping = selected if gesture.center_activated and "center" in selected else profile
    shortcut = emulate_shortcuts(mapping, "center", pressed, gesture_modifiers(snapshot),
                                 now - gesture.center_entered)
    if shortcut is None:
        return False
    if gesture.selected_function:
        if selected.get("command"):
            execute_command(selected["command"])
        else:
            gesture.center_activated = not gesture.center_activated
        gesture.display_title = selected.get("title", gesture.selected_function)
        request_action_metadata(shortcut, selected, gesture_feedback(gesture))
    else:
        gesture.display_title = shortcut.get("title", gesture.display_title)
        request_action_metadata(shortcut, {}, {"input": "center", **gesture_feedback(gesture)})
    return True


def rotation_action(gesture, direction, pressed, now):
    profile = gesture.snapshot.profile
    selected = profile.get(gesture.selected_function, {}) if gesture.selected_function else {}
    mapping = selected if gesture.center_activated else profile
    shortcut = emulate_shortcuts(mapping, direction, pressed, gesture_modifiers(gesture.snapshot),
                                 now - gesture.started)
    if shortcut is not None:
        gesture.display_title = shortcut.get("title", selected.get("title",
                                    gesture.selected_function or gesture.display_title))
        request_action_metadata(shortcut, selected,
                                {"input": direction, **gesture_feedback(gesture)})
    return shortcut


def finish_gesture(now, cancelled=False):
    gesture = runtime.gesture
    if not gesture.active:
        return
    gesture.release_pending = True
    snapshot = gesture.snapshot
    try:
        if not cancelled and dialpad:
            if gesture.pending_rotation:
                rotation_action(gesture, gesture.pending_rotation, False, now)
            if gesture.center_triggered:
                center_action(gesture, False, now)
        if gesture.tap_disabled:
            set_touchpad_prop_send_events(1)
            gesture.tap_disabled = False
        if cancelled:
            gesture.reset()
        else:
            gesture.finish()
        if not gesture.center_activated and any(snapshot.function_names) and socket_enabled:
            send_to_socket({"titles": list(snapshot.function_titles),
                            "icons": list(multi_app_mode_icons), "title": None,
                            "selected_index": None})
            request_ring_metadata(snapshot)
    finally:
        gesture.release_pending = False


def process_touch_frame(now):
    contacts, gesture = runtime.contacts, runtime.gesture
    if not contacts.synchronized or not contacts.frame_complete:
        return
    if not contacts.touching:
        finish_gesture(now)
        return
    if not gesture.active:
        gesture.begin(runtime.current, now)
    gesture.touch_x, gesture.touch_y = contacts.position()
    if gesture.touch_x is None or gesture.touch_y is None:
        return
    snapshot = gesture.snapshot
    geometry = snapshot.geometry
    x, y = gesture.touch_x, gesture.touch_y
    left, right, top, bottom = geometry.icon_bounds
    inside_icon = left <= x <= right and top <= y <= bottom
    if inside_icon:
        gesture.within_icon = True
        if (gesture.started is not None and not gesture.icon_activated and
                not gesture.coactivator_blocked and now - gesture.started >= activation_time):
            if dialpad or are_modifier_keys_pressed(coactivator_keys):
                toggle_top_right_icon(dialpad)
                gesture.icon_activated = True
            else:
                gesture.coactivator_blocked = True
    elif gesture.within_icon:
        gesture.within_icon = False
        gesture.icon_activated = False
        gesture.coactivator_blocked = True

    distance, angle = geometry.position(x, y)
    if distance > geometry.radius or not dialpad:
        gesture.center_triggered = False
        gesture.last_angle = None
        return
    if not gesture.tap_disabled:
        set_touchpad_prop_send_events(0)
        gesture.tap_disabled = True
    if distance < geometry.center_radius:
        if not gesture.center_triggered:
            gesture.center_triggered = True
            gesture.center_entered = now
            gesture.center_immediate = False
            gesture.last_angle = None
            if socket_enabled:
                send_to_socket({"input": "center", "value": 1, **gesture_feedback(gesture)})
        if not gesture.center_immediate:
            gesture.center_immediate = center_action(gesture, True, now)
        return
    if gesture.center_triggered:
        gesture.center_triggered = False
        gesture.center_immediate = False
        if socket_enabled:
            send_to_socket({"input": "center", "value": 0, **gesture_feedback(gesture)})

    if any(snapshot.function_names) and not gesture.center_activated:
        current_slice = int(angle // (360 / len(snapshot.function_names)))
        if current_slice != gesture.last_slice:
            gesture.last_slice = current_slice
            gesture.selected_function = snapshot.function_names[current_slice]
            entry = snapshot.profile.get(gesture.selected_function, {})
            gesture.display_title = entry.get("title", gesture.selected_function)
            runtime.invalidate_metadata()
            if socket_enabled:
                send_to_socket({"titles": list(snapshot.function_titles),
                                "icons": list(multi_app_mode_icons),
                                **gesture_feedback(gesture)})
        return
    if gesture.last_angle is None:
        gesture.last_angle = gesture.angle_start = angle
        gesture.angle_accumulator = 0
        return
    delta = (angle - gesture.last_angle + 180) % 360 - 180
    gesture.last_angle = angle
    gesture.angle_accumulator += delta
    selected = snapshot.profile.get(gesture.selected_function, {}) if gesture.selected_function else {}
    direction = "clockwise" if gesture.angle_accumulator > 0 else "counterclockwise"
    mapping = selected if gesture.center_activated else snapshot.profile
    held_modifiers = gesture_modifiers(snapshot)
    held_for = now - gesture.started
    action = (matching_action(mapping, direction, True, held_modifiers, held_for) or
              matching_action(mapping, direction, False, held_modifiers, held_for))
    threshold = (action or {}).get("treshold", selected.get("treshold", default_treshold))
    if socket_enabled and threshold >= socket_send_progress_above_treshold and delta:
        send_to_socket({"value_angle_start": gesture.angle_start,
                        "value": int(max(-100, min(100, gesture.angle_accumulator / threshold * 100))),
                        **gesture_feedback(gesture), "value_show_only_progress": True})
    if abs(gesture.angle_accumulator) >= threshold:
        shortcut = rotation_action(gesture, direction, True, now)
        if shortcut is None:
            gesture.pending_rotation = direction
        gesture.angle_accumulator = 0
        if socket_enabled and threshold >= socket_send_progress_above_treshold:
            send_to_socket({"value": 0, **gesture_feedback(gesture),
                            "value_show_only_progress": True})


def synchronize_touchpad():
    # libevdev updates its cached keys/slots while consuming INPUT-device sync.
    # Never call sync() on the output device and never infer idle from one key.
    runtime.contacts.synchronized = False
    finish_gesture(time(), cancelled=True)
    for event in d_t.sync():
        runtime.contacts.feed(event.code.name, event.value)
    keys = {}
    for name in CONTACT_KEYS:
        code = getattr(EV_KEY, name, None)
        if code is not None and d_t.has(code):
            keys[name] = bool(d_t.value[code])
    slots, positions = None, {}
    if d_t.num_slots is not None:
        slots = {}
        for index, slot in enumerate(d_t.slots):
            tracking_id = slot[EV_ABS.ABS_MT_TRACKING_ID]
            if tracking_id is None:
                raise RuntimeError("Cannot prove multitouch contact state without tracking IDs")
            slots[index] = tracking_id
            positions[index] = [slot[EV_ABS.ABS_MT_POSITION_X], slot[EV_ABS.ABS_MT_POSITION_Y]]
    runtime.contacts.resynchronize(
        keys, slots, positions=positions, slot=d_t.current_slot,
        x=d_t.value[EV_ABS.ABS_X], y=d_t.value[EV_ABS.ABS_Y],
        type_a=slots is None and d_t.has(EV_ABS.ABS_MT_POSITION_X),
    )


def listen_touchpad_events():
    global last_event_time, pressed_keys, multi_app_mode_icons
    synchronize_touchpad()
    with selectors.DefaultSelector() as selector:
        selector.register(fd_t, selectors.EVENT_READ)
        selector.register(runtime.wakeup, selectors.EVENT_READ)
        while not stop_threads:
            selector.select(0.1)
            runtime.wakeup.drain()
            for kind, value in runtime.messages():
                if kind == "pressed_keys":
                    pressed_keys = value
            # Drain actual nonblocking input before examining the commit boundary.
            try:
                for event in d_t.events():
                    last_event_time = time()
                    runtime.contacts.feed(event.code.name, event.value)
                    if event.matches(EV_SYN.SYN_REPORT):
                        process_touch_frame(last_event_time)
            except device.EventsDroppedException:
                synchronize_touchpad()
            # Held icon/center timers still work when the finger stops moving.
            process_touch_frame(time())
            if (runtime.contacts.idle and disable_due_inactivity_time and dialpad and
                    last_event_time and time() > last_event_time + disable_due_inactivity_time):
                deactivate_dialpad()
            runtime.commit()
            payload = runtime.metadata_result()
            if payload is not None and socket_enabled:
                if "icons" in payload:
                    multi_app_mode_icons = list(payload["icons"])
                send_to_socket(payload)

def check_config_values_changes():
    # Watch entries, not replaced inodes. Lock-held notifications still request a
    # re-read; generation coalescing never discards external configuration edits.
    roots = {Path(config_file_dir), install_dir}
    roots |= {root.resolve() for root in roots}
    config_roots = {Path(config_file_dir), Path(config_file_dir).resolve()}
    layout_dirs = {root / "layouts" for root in roots}
    watched = {}
    source_targets = set()
    mask = (IN_CLOSE_WRITE | IN_MOVED_TO | IN_MOVED_FROM | IN_CREATE | IN_DELETE |
            IN_DELETE_SELF | IN_MOVE_SELF | IN_IGNORED)

    class Handler(ProcessEvent):
        def process_IN_Q_OVERFLOW(self, event):
            runtime.request()

        def process_default(self, event):
            path = Path(event.pathname).absolute() if getattr(event, "pathname", None) else None
            name = getattr(event, "name", "")
            invalidated = event.mask & (IN_IGNORED | IN_DELETE_SELF | IN_MOVE_SELF)
            if invalidated:
                for watched_path, descriptor in tuple(watched.items()):
                    if descriptor == event.wd:
                        watched.pop(watched_path)
                if not event.mask & IN_IGNORED:
                    watch_manager.rm_watch(event.wd)
            relevant = bool(invalidated)
            if path:
                relevant |= name == CONFIG_FILE_NAME and path.parent in config_roots
                relevant |= path in roots or path in layout_dirs
                relevant |= path in source_targets or path.resolve() in source_targets
                search_dirs = layout_dirs | {item.resolve() for item in layout_dirs}
                if path.parent in search_dirs:
                    selected = runtime.status()["requested"]["identifier"] or model
                    relevant |= name in (f"{selected}.json", f"{selected}.py")
            if relevant:
                runtime.request()

    def refresh_watches():
        nonlocal source_targets
        status = runtime.status()
        targets = {
            Path(identity["path"]).resolve()
            for identity in (status["requested"], status["applied"])
            if identity and identity.get("path")
        }
        identifier = status["requested"]["identifier"] or model
        try:
            targets.add(resolve_layout(identifier, config_file_dir, install_dir).path.resolve())
        except Exception:
            pass
        changed = targets != source_targets
        source_targets = targets
        directories = roots | layout_dirs | {root.parent for root in roots}
        directories |= {target.parent for target in targets}
        desired = set()
        for directory in directories:
            directory = directory.resolve()
            # Watch the closest existing ancestor until a missing search/target
            # directory is created, then move coverage down to its actual parent.
            while not directory.is_dir() and directory != directory.parent:
                directory = directory.parent
            desired.add(str(directory))
        for path in tuple(watched):
            if path not in desired:
                watch_manager.rm_watch(watched.pop(path))
                changed = True
        for path in desired:
            if path not in watched:
                result = watch_manager.add_watch(path, mask)
                if result.get(path, -1) >= 0:
                    watched[path] = result[path]
                    changed = True
        return changed

    notifier = Notifier(watch_manager, Handler())
    try:
        refresh_watches()
        # Close the startup read/watch gap, without erasing an unchanged recovery
        # report merely because its invalid requested source still exists.
        observed = {"identifier": None, "path": None, "revision": None}
        settings_changed = True
        try:
            parser = read_config(config_file_dir)
            identifier = parser.get("main", "layout", fallback="").strip() or model
            observed["identifier"] = identifier
            settings_changed = parse_settings(parser) != runtime.current.prepared.settings
            source = resolve_layout(identifier, config_file_dir, install_dir)
            observed["path"] = str(source.path)
            observed["revision"] = revision_bytes(source.path.read_bytes())
        except Exception:
            pass
        if observed != runtime.status()["requested"] or settings_changed:
            runtime.request()
        while not stop_threads:
            if notifier.check_events(timeout=500):
                notifier.read_events()
                notifier.process_events()
            if refresh_watches():
                # New coverage can hide earlier creations in that directory.
                runtime.request()
    finally:
        notifier.stop()


def gsettingsGet(path, name):
    global gsettings_failure_count, gsettings_max_failure_count

    if gsettings_failure_count < gsettings_max_failure_count:
        try:
            cmd = ['gsettings', 'get', path, name]
            result = subprocess.check_output(cmd).rstrip()
            return result
        except Exception as e:
            log.debug(e, exc_info=True)
            gsettings_failure_count+=1
    else:
        log.debug('Gsettings failed more then: \"%s\" so is not try anymore', gsettings_max_failure_count)



def resolve_coactivators(names, context):
    if not names:
        return frozenset()
    aliases = {
        "Control": "Control_L", "Shift": "Shift_L", "Lock": "Caps_Lock",
        "Mod1": "Alt_L", "Mod2": "Num_Lock", "Mod3": "Caps_Lock",
        "Mod4": "Super_L", "Mod5": "ISO_Level3_Shift", "NumLock": "Num_Lock",
        "Alt": "Alt_L", "LevelThree": "ISO_Level3_Shift", "LAlt": "Alt_L",
        "RAlt": "Alt_R", "RControl": "Control_R", "LControl": "Control_L",
        "ScrollLock": "Scroll_Lock", "LevelFive": "ISO_Level5_Shift",
        "AltGr": "ISO_Level3_Shift", "Meta": "Super_L", "Super": "Super_L",
        "Hyper": "Hyper_L",
    }
    indices = {"Shift": 0, "Lock": 1, "Control": 2, "Alt": 3, "Mod1": 3,
               "Mod2": 4, "Mod3": 5, "Mod4": 6, "Mod5": 7, "AltGr": 7}
    resolved = set()
    if display and X11_LIBS_AVAILABLE:
        if context.get("x11_mapping") is not None:
            display.refresh_keyboard_mapping(context["x11_mapping"])
        mapping = display.get_modifier_mapping()
        for name in names:
            if name in indices:
                keycode = next((code for code in mapping[indices[name]] if code), 0)
            else:
                keycode = display.keysym_to_keycode(Xlib.XK.string_to_keysym(aliases.get(name, name)))
            if not 8 <= keycode < len(EV_KEY.codes) + 8:
                raise ValueError(f"Cannot resolve co-activator {name!r} in the X11 keymap")
            resolved.add(EV_KEY.codes[keycode - 8])
        return frozenset(resolved)
    state = context.get("keyboard_state")
    if state is None:
        raise ValueError("Wayland keymap is unavailable for configured co-activators")
    keymap = state.get_keymap()
    mod_names = {keymap.mod_get_name(index): index for index in range(keymap.num_mods())}
    for name in names:
        desired_mod = {"Alt": "Mod1", "Meta": "Mod4", "Super": "Mod4",
                       "AltGr": "Mod5"}.get(name, name)
        target_symbol = xkb.keysym_from_name(aliases.get(name, name))
        found = None
        for keycode in keymap:
            if not 8 <= keycode < len(EV_KEY.codes) + 8:
                continue
            clean = keymap.state_new()
            clean.update_key(keycode, xkb.KeyDirection.XKB_KEY_DOWN)
            if desired_mod in mod_names and clean.mod_index_is_active(
                    mod_names[desired_mod], xkb.StateComponent.XKB_STATE_MODS_EFFECTIVE):
                found = EV_KEY.codes[keycode - 8]
                break
            count = keymap.num_layouts_for_key(keycode)
            layout_index = context.get("layout_index")
            layouts = (layout_index % count,) if count and layout_index is not None else range(count)
            if any(target_symbol in keymap.key_get_syms_by_level(keycode, index, 0)
                   for index in layouts):
                found = EV_KEY.codes[keycode - 8]
                break
        if found is None:
            raise ValueError(f"Cannot resolve co-activator {name!r} in the Wayland keymap")
        resolved.add(found)
    return frozenset(resolved)


def listen_keyboard_events():
    if keyboard is None:
        return
    try:
        with open('/dev/input/event' + str(keyboard), 'rb', buffering=0) as fd_k:
            os.set_blocking(fd_k.fileno(), False)
            d_k = Device(fd_k)
            keys = {code for code in d_k.evbits.get(EV_KEY, ()) if d_k.value[code]}
            runtime.submit("pressed_keys", frozenset(keys))
            with selectors.DefaultSelector() as selector:
                selector.register(fd_k, selectors.EVENT_READ)
                while not stop_threads:
                    if not selector.select(0.5):
                        continue
                    try:
                        for event in d_k.events():
                            if event.type == EV_KEY:
                                if event.value:
                                    keys.add(event.code)
                                else:
                                    keys.discard(event.code)
                    except device.EventsDroppedException:
                        for event in d_k.sync():
                            pass
                        keys = {code for code in d_k.evbits.get(EV_KEY, ()) if d_k.value[code]}
                    runtime.submit("pressed_keys", frozenset(keys))
    except Exception:
        log.exception("Keyboard listener failed")


def window_was_changed(binary, title=None):
    runtime.submit_window(binary, title)


def check_window():
    while not stop_threads:
        window_was_changed(*get_active_window_title())
        sleep(0.5)


def check_gnome_layout():
    previous = None
    while not stop_threads:
        try:
            current = read_gnome_input_source(gsettingsGet)
            if current is not None and current != previous:
                index, name = current
                runtime.submit_context(layout_index=index, layout_name=name,
                                       set_x11_layout=bool(previous and display))
                previous = current
        except Exception:
            log.debug("Cannot read GNOME keyboard layout", exc_info=True)
        sleep(0.5)


def cleanup():
    global stop_threads
    stop_threads = True
    if runtime is not None:
        try:
            finish_gesture(time(), cancelled=True)
        except Exception:
            log.exception("Cannot restore touchpad event delivery")
    if dialpad:
        try:
            deactivate_dialpad()
        except Exception:
            log.exception("Cannot deactivate DialPad")
    if status_server is not None:
        status_server.close()
    if runtime is not None:
        runtime.close()
    fd_t.close()
    if display_wayland:
        display_wayland.disconnect()
    if display:
        try:
            display.close()
        except Exception:
            pass
    if xkb_conn is not None:
        xkb_conn.disconnect()
    if sock:
        sock.close()
    # The datagram endpoint belongs to the floating UI, not this sender.

threads = []
stop_threads = False
watch_manager = None


def wl_keyboard_keymap_handler(keyboard, format_, fd, size):
    global keymap_loaded
    try:
        with mmap.mmap(fd, size, prot=mmap.PROT_READ, flags=mmap.MAP_PRIVATE) as data:
            keymap = xkb.Context().keymap_new_from_buffer(data, length=size - 1)
        runtime.submit_context(keyboard_state=keymap.state_new())
        keymap_loaded = True
    finally:
        os.close(fd)

def wl_registry_handler(registry, id_, interface, version):
  log.debug(registry)
  log.debug(id_)
  log.debug(interface)
  log.debug(version)
  if interface == "wl_seat":
    seat = registry.bind(id_, WlSeat, version)
    keyboard = seat.get_keyboard()
    keyboard.dispatcher["keymap"] = wl_keyboard_keymap_handler

def load_keymap_listener_wayland():
    global stop_threads, display_wayland_var, display_wayland

    try:
        registry = display_wayland.get_registry()
        registry.dispatcher["global"] = wl_registry_handler
        display_wayland.dispatch(block=True)
        display_wayland.roundtrip()

        while not stop_threads and display_wayland.dispatch(block=True) != -1:
            pass
    except:
        log.exception("Wayland load keymap listener error. Exiting")
        os.kill(os.getpid(), signal.SIGUSR1)

def load_keymap_listener_x11():
    try:
        while not stop_threads:
            event = display.next_event()
            if event.type == Xlib.X.MappingNotify and event.request == Xlib.X.MappingKeyboard:
                runtime.submit_context(x11_mapping=event)
    except Exception:
        if not stop_threads:
            log.exception("X11 keymap listener failed")
            os.kill(os.getpid(), signal.SIGUSR1)


def _extract_app_info(acc):
    try:
        app = acc.getApplication()
        app_name = app.name if app else None
        title = acc.name
        return app_name, title
    except Exception:
        return None, None

def on_window_activated(event):
    try:
        if event.detail1 == 1:
            binary, title = _extract_app_info(event.source)
            if binary or title:
                window_was_changed(binary, title)
    except Exception:
        log.debug("Cannot identify activated window", exc_info=True)
        
def check_window_pyatspi():
    pyatspi.Registry.registerEventListener(
        on_window_activated,
        "object:state-changed:active",
    )

    pyatspi.Registry.start()

try:
    init_socket()
    config = update_config(config_file_dir, {}, defaults_if_missing=CONFIG_DEFAULTS)
    settings = parse_settings(config)
    try:
        initial = prepare_loaded_layout(startup_loaded, settings)
    except Exception as error:
        # Compilation and measured geometry may fail after static source loading.
        startup_recovery_error = str(error)
        initial = prepare_loaded_layout(load_recovery(config_file_dir), settings)
    runtime = RuntimeOwner(prepare_requested_layout, prepare_virtual_device,
                           lambda loaded: save_recovery(config_file_dir, loaded),
                           publish_runtime_snapshot, device_bounds,
                           dispose_device=close_virtual_device)
    if os.environ.get("XDG_RUNTIME_DIR"):
        # Unsafe endpoints and duplicate owners are fatal, not silent fallbacks.
        status_server = StatusServer(config_file_dir, runtime.status)
        status_server.start()
    else:
        log.warning("XDG_RUNTIME_DIR is unset; live layout status is unavailable")

    if xdg_session_type == "wayland":
        thread = threading.Thread(target=load_keymap_listener_wayland, daemon=True)
        threads.append(thread)
        thread.start()
        while not keymap_loaded:
            sleep(0.1)
    else:
        keymap_loaded = True
        thread = threading.Thread(target=load_keymap_listener_x11, daemon=True)
        threads.append(thread)
        thread.start()
    runtime.submit_window(*get_active_window_title())
    try:
        while not runtime.install_startup(initial, requested=startup_requested,
                                          recovery_error=startup_recovery_error):
            runtime.wakeup.drain()
    except Exception as error:
        if startup_recovery_error:
            raise
        startup_recovery_error = str(error)
        initial = prepare_loaded_layout(load_recovery(config_file_dir), settings)
        while not runtime.install_startup(initial, requested=startup_requested,
                                          recovery_error=startup_recovery_error):
            runtime.wakeup.drain()
    watch_manager = WatchManager()
    listeners = [listen_keyboard_events, check_config_values_changes]
    if uses_gnome_input_sources(os.environ):
        listeners.append(check_gnome_layout)
    for target in listeners:
        thread = threading.Thread(target=target, daemon=True)
        threads.append(thread)
        thread.start()

    desktop_by_pyatspi = None
    if PYATSPI_AVAILABLE:
        try:
            desktop_by_pyatspi = pyatspi.Registry.getDesktop(0)
        except Exception:
            pass
    target = check_window_pyatspi if desktop_by_pyatspi else check_window
    thread = threading.Thread(target=target, daemon=True)
    threads.append(thread)
    thread.start()
    listen_touchpad_events()
except Exception:
    log.exception("DialPad runtime failed")
finally:
    cleanup()
    log.info("Exiting")
    sys.exit(1)