#!/usr/bin/env python3

import logging
import os
import importlib
import sys
import threading
from time import sleep, time
import Xlib.display
import Xlib.X
import Xlib.XK
from xkbcommon import xkb
from libevdev import EV_ABS, EV_KEY, EV_REL, EV_SYN, Device, InputEvent, device
from pyinotify import WatchManager, IN_CLOSE_WRITE, IN_IGNORED, IN_MOVED_TO, AsyncNotifier
from periphery import I2C
from typing import Optional
import re
import math
from pywayland.client import Display
from pywayland.protocol.wayland import WlSeat
import subprocess
import configparser
import ast
from xkbcommon import xkb
import signal
import mmap
import shutil
import xcffib
import xcffib.xkb
import glob
import socket
import json

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

SYSTEMD_JOURNAL_AVAILABLE = False
try:
    from systemd.journal import JournalHandler
    SYSTEMD_JOURNAL_AVAILABLE = True
except ImportError:
    pass

QDBUS = shutil.which("qdbus") or shutil.which("qdbus6")

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
keyboard_state = None
display_wayland_var = None
keymap_loaded = False
listening_touchpad_events_started = False
active_modifiers = set()
modifiers = set()
activation_lock = threading.Lock()
multi_app_mode = None
multi_app_mode_titles = None
multi_app_mode_icons = None
app_specific_shortcuts = {}
app_name = None
multi_app_mode_grid = 4

if xdg_session_type == "x11":
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
    try:
        display_wayland_var = os.environ.get('WAYLAND_DISPLAY')
        display_wayland = Display(display_wayland_var)
        display_wayland.connect()
        log.info("Wayland session detected and connected.")
    except Exception as e:
        log.error(f"Failed to connect to Wayland display: {e}")
        sys.exit(1)

dialpad: bool = False

# DialPad layout model
model = None
if len(sys.argv) > 1:
    model = sys.argv[1]
try:
    model_layout = importlib.import_module('layouts.' + model)
except:
    layouts_dir = "layouts"
    available_layouts = [os.path.splitext(f)[0] for f in os.listdir(layouts_dir) if f.endswith(".py")]

    log.error(
        f"DialPad layout *.py from dir '{layouts_dir}' is required as first argument. "
        f"Re-run install script or add missing first argument. "
        f"Available layouts: {', '.join(available_layouts)}"
    )
    sys.exit(1)

# Config file dir
config_file_dir = ""
if len(sys.argv) > 2:
    config_file_dir = sys.argv[2]
# When is given config dir empty or is used default -> to ./ because inotify needs check folder (nor nothing = "")
if config_file_dir == "":
     config_file_dir = "./"

# Layout
circle_diameter = getattr(model_layout, "circle_diameter", 0)
center_button_diameter = getattr(model_layout, "center_button_diameter", 0)
circle_center_x = getattr(model_layout, "circle_center_x", 0)
circle_center_y = getattr(model_layout, "circle_center_y", 0)
top_right_icon_width = getattr(model_layout, "top_right_icon_width", 0)
top_right_icon_height = getattr(model_layout, "top_right_icon_height", 0)

# App-specific configuration (add more mappings as needed)
app_shortcuts = getattr(model_layout, "app_shortcuts", {})

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
        lines = f.readlines()
        for line in lines:
            # Look for the touchpad #

            # https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver/issues/87
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/95
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/110
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/161
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/198
            if (touchpad_detected == 0 and ("Name=\"ASUE" in line or "Name=\"ELAN" in line or "Name=\"ASUP" or "Name=\"ASUF" or "Name=\"ASCE" or "Name=\"ASCF" or "Name=\"ASCP" in line) and "Touchpad" in line and not "9009" in line):
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

            # Look for the keyboard
            if keyboard_detected == 0 and ("Name=\"AT Translated Set 2 keyboard" in line or (("Name=\"ASUE" in line or "Name=\"Asus" in line or "Name=\"ASUP" in line or "Name=\"ASUF" in line) and "Keyboard" in line)):
                keyboard_detected = 1
                log.info(
                    'Detecting keyboard from string: \"%s\"', line.strip())

            # We look for keyboard
            if keyboard_detected == 1 and "H: " in line:
                keyboard = line.split("event")[1]
                keyboard = keyboard.split(" ")[0]
                keyboard_detected = 2
                log.info('Set keyboard id %s from %s', keyboard, line.strip())

              # Do not stop looking if touchpad and keyboard have been found
            # because more drivers can be installed
            # https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver/issues/87
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/95
            #if touchpad_detected == 2 and keyboard_detected == 2:
            #    break

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

# App-specific configuration (add more mappings as needed)


# Initialize the virtual device globally
uinput_device = None

# Config
CONFIG_FILE_NAME = "dialpad_dev"
CONFIG_SECTION = "main"
CONFIG_ENABLED = "enabled"
CONFIG_SOCKET_ENABLED = "socket_enabled"
CONFIG_SOCKET_ENABLED_DEFAULT = True
CONFIG_ENABLED_DEFAULT = False
CONFIG_SLICES_COUNT = "slices_count"
CONFIG_SLICES_COUNT_DEFAULT = 4
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

config_file_path = config_file_dir + CONFIG_FILE_NAME
config = configparser.ConfigParser()
config_lock = threading.Lock()

# Start monitoring the touchpad
fd_t = open('/dev/input/event' + str(touchpad), 'rb')
d_t = Device(fd_t)

# Get touchpad dimensions
abs_x = d_t.absinfo[EV_ABS.ABS_X]
abs_y = d_t.absinfo[EV_ABS.ABS_Y]
min_x, max_x = abs_x.minimum, abs_x.maximum
min_y, max_y = abs_y.minimum, abs_y.maximum
log.info('Touchpad min-max: x %d-%d, y %d-%d', min_x, max_x, min_y, max_y)

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

def parse_value_from_config(value):
    if value == '0':
        return False
    elif value == '1':
        return True
    else:
        return value

def parse_value_to_config(value):
    if value == True:
        return '1'
    elif value == False:
        return '0'
    else:
        return str(value)

def config_save():
    global config_file_dir, config_file_path

    try:
        with open(config_file_path, 'w') as configFile:
            config.write(configFile)
            log.debug('Writting to config file: \"%s\"', configFile)
    except:
        log.error('Error during writting to config file: \"%s\"', config_file_path)
        pass

def config_set(key, value, no_save=False, already_has_lock=False):
    global config, config_file_dir, config_lock

    if not already_has_lock:
        #log.debug("config_set: config_lock.acquire will be called")
        config_lock.acquire()
        #log.debug("config_set: config_lock.acquire called succesfully")

    config.set(CONFIG_SECTION, key, parse_value_to_config(value))
    log.info('Setting up for config file key: \"%s\" with value: \"%s\"', key, value)

    if not no_save:
        config_save()

    if not already_has_lock:
        # because inotify (deadlock)
        sleep(0.1)
        config_lock.release()

    return value

# methods for read & write from config file
def config_get(key, key_default):
    try:
        value = config.get(CONFIG_SECTION, key)
        parsed_value = parse_value_from_config(value)
        return parsed_value
    except:
        config.set(CONFIG_SECTION, key, parse_value_to_config(key_default))
        return key_default

def read_config_file():
    global config, config_file_path

    try:
        if not config.has_section(CONFIG_SECTION):
            config.add_section(CONFIG_SECTION)

        config.read(config_file_path)
    except:
        pass

def is_multifunction(app_config):

    standard_keys = {"center", "clockwise", "counterclockwise"}

    non_standard_keys = []
    non_standard_keys_icons = []

    for key, value in app_config.items():
        if key in standard_keys:
            continue

        non_standard_keys.append(key)

        if isinstance(value, dict):
            icon = value.get("icon")
            if icon:
                non_standard_keys_icons.append(icon)

    return bool(len(non_standard_keys)), non_standard_keys, non_standard_keys_icons

def load_all_config_values():
    global config
    global disable_due_inactivity_time
    global touchpad_disables_dialpad
    global activation_time
    global config_lock
    global slices_count
    global suppress_app_specifics_shortcuts
    global coactivator_keys
    global socket_enabled

    #log.debug("load_all_config_values: config_lock.acquire will be called")
    config_lock.acquire()
    #log.debug("load_all_config_values: config_lock.acquire called succesfully")

    read_config_file()

    disable_due_inactivity_time = float(config_get(CONFIG_DISABLE_DUE_INACTIVITY_TIME, CONFIG_DISABLE_DUE_INACTIVITY_TIME_DEFAULT))
    touchpad_disables_dialpad = config_get(CONFIG_TOUCHPAD_DISABLES_DIALPAD, CONFIG_TOUCHPAD_DISABLES_DIALPAD_DEFAULT)
    activation_time = float(config_get(CONFIG_ACTIVATION_TIME, CONFIG_ACTIVATION_TIME_DEFAULT))
    enabled = config_get(CONFIG_ENABLED, CONFIG_ENABLED_DEFAULT)
    slices_count = int(config_get(CONFIG_SLICES_COUNT, CONFIG_SLICES_COUNT_DEFAULT))
    suppress_app_specifics_shortcuts = int(config_get(CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS, CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS_DEFAULT))
    coactivator_keys = config_get(CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY, CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY_DEFAULT).strip().split()
    socket_enabled = config_get(CONFIG_SOCKET_ENABLED, CONFIG_SOCKET_ENABLED_DEFAULT)

    config_lock.release()

    if enabled is not dialpad:
        toggle_top_right_icon(dialpad)

def send_value_to_touchpad_via_i2c(value):
    global device_id, device_addr

    # Payload is the same as before, only the transport changes
    data = [
        0x05, 0x00, 0x3d, 0x03, 0x06, 0x00, 0x07, 0x00,
        0x0d, 0x14, 0x03, int(value, 16), 0xad,
    ]

    path = f"/dev/i2c-{device_id}"

    # 1) Try python-periphery first
    try:
        with I2C(path) as i2c:
            msg = I2C.Message(data)
            i2c.transfer(device_addr, [msg])
            log.debug("Sent I2C data via python-periphery on %s", path)
            return True
    except Exception as e:
        log.debug("periphery.I2C transfer failed on %s: %s; falling back to i2ctransfer", path, e)

    # 2) Fallback: use i2ctransfer (from i2c-tools)
    try:
        hex_data = [f"0x{b:02x}" for b in data]
        cmd = [
            "i2ctransfer",
            "-f", "-y",
            str(device_id),
            f"w{len(data)}@0x{device_addr:x}",
        ] + hex_data

        log.debug("Trying I2C via i2ctransfer: %s", " ".join(cmd))
        result = subprocess.run(cmd, check=True, capture_output=True)
        log.debug("I2C transfer successful via i2ctransfer")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode().strip() if e.stderr else str(e)
        log.error("i2ctransfer failed: %s", stderr)
    except Exception as e:
        log.error("Error during fallback I2C transfer: %s", e)

    return False

def initialize_virtual_device():
    global uinput_device, dev, modifiers

    try:
        # create the virtual device
        dev = Device()
        dev.name = touchpad_name.split(" ")[0] + " " + touchpad_name.split(" ")[1] + " DialPad"

        # aggregate all shortcuts to enable
        shortcuts_to_enable = {}

        shortcuts_to_enable = app_shortcuts

        def process_config(config):
            if not isinstance(config, dict):
                return

            field = config.get("key")
            if field is None:
                return

            # single key
            if isEvent(field):
                enable_key(field)
            # list of keys (EV_KEY or EV_REL)
            elif isEventList(field):
                for k in field:
                    enable_key(k)
            # otherwise treat as character
            else:
                set_evdev_key_for_char(field, '')

            # add modifier if present
            mod = config.get("modifier")
            if mod is not None:
                modifiers.add(mod)

        # enable all keys from the aggregated configuration
        for shortcuts in shortcuts_to_enable.values():
            for action, configs in shortcuts.items():
                if isinstance(configs, dict):
                    for sublist in configs.values():
                        if not isinstance(sublist, list):
                            sublist = [sublist]
                        for config in sublist:
                            process_config(config)
                else:
                    if not isinstance(configs, list):
                        configs = [configs]
                    for config in configs:
                        process_config(config)

        # create the uinput device
        uinput_device = dev.create_uinput_device()
        log.info("Virtual device initialized successfully.")

        sleep(0.5)

    except Exception as e:
        log.error(f"Error initializing virtual device: {e}")
        sys.exit(1)

def get_window_kde_wayland_title(window_id):
    try:
        cmd = [QDBUS, 'org.kde.KWin', f'/org/kde/KWin/Window/{window_id}', 'org.kde.KWin.Window.caption']
        output = subprocess.check_output(cmd).decode().strip()
        return output
    except Exception as e:
        log.error("Error getting KDE window title: %s", e)
        return None

def get_active_window_kde_wayland_title_using_qdbus():
    global qdbus_failure_count, qdbus_max_failure_count

    if qdbus_failure_count >= qdbus_max_failure_count:
        return None

    try:
        cmd = [QDBUS, 'org.kde.KWin', '/KWin', 'org.kde.KWin.activeWindow']
        output = subprocess.check_output(cmd).decode().strip()
        match = re.search(r"(\d+)", output)
        if match:
            window_id = match.group(1)
            return get_window_kde_wayland_title(window_id)
    except Exception as e:
        qdbus_failure_count += 1
        log.error("QDbus KDE title fetch failed (%d/%d): %s", qdbus_failure_count, qdbus_max_failure_count, e)
        return None

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
        log.error("GNOME window title fetch failed (%d/%d): %s", gnome_failure_count, gnome_max_failure_count, e)
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
        log.error("Error retrieving active window info (X11): %s", e)
        return None, None

def get_active_window_info_kde_wayland():
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
        log.error("Error retrieving active window info (KDE Wayland): %s", e)
        return None, None

def get_active_window_info_gnome_wayland():
    title = get_active_window_gnome_wayland_title()
    return None, title

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

    log.error("Unsupported session type or display not connected.")

    return None, None

def get_appropriate_app_name_and_shortcuts(window_binary, window_title):

    app_name = None

    # 1. window_binary (exact match)
    if window_binary:
        binary_lower = window_binary.lower()
        app_name = next(
            (app for app in app_shortcuts if app in binary_lower),
            None
        )
    # 2. window_title (may be only part of)
    if not app_name:
        app_name = next((app for app in app_shortcuts if app in window_title.lower()), None) if window_title else None

    # 3. not a specific app -> 'none' shortcuts block
    if not app_name:
        app_name = "none"

    shortcuts = app_shortcuts.get(app_name, app_shortcuts["none"])

    return app_name, shortcuts

def emulate_shortcuts(app_shortcuts, touch_input, event_code, active_modifiers, duration_held=0):
    global suppress_app_specifics_shortcuts

    matched_shortcuts = app_shortcuts.get(touch_input, [])
    if not isinstance(matched_shortcuts, list):
        matched_shortcuts = [matched_shortcuts]

    prioritized_shortcuts = sorted(matched_shortcuts, key=lambda s: "modifier" not in s)

    for shortcut in prioritized_shortcuts:
        key_code = None
        try:
            key_code = shortcut["key"]
        except:
            pass
        key_value = None
        try:
            key_value = shortcut["value"]
        except:
            pass
        trigger_mode = shortcut.get("trigger", "release")
        modifier = shortcut.get("modifier")
        required_duration = shortcut.get("duration", 0)  # Default to 0 (immediate)

        # Ensure correct modifiers
        if (modifier and modifier in active_modifiers) or (not modifier and not active_modifiers):
            if duration_held is None or duration_held >= required_duration:
                if (trigger_mode == "immediate" and event_code) or\
                    (trigger_mode == "release" and not event_code):
                    if key_code:
                        send_key_event(key_code, key_value)

                log.info(f"Executed shortcut: {key_code} with value {key_value} with modifier {modifier} (Held for {duration_held:.2f}s)")
                return shortcut # Stop after first valid shortcut
            else:
                #log.info(trigger_mode)
                #log.info(event_code)
                if (trigger_mode == "immediate" and not event_code) or (trigger_mode == "release" and not event_code):
                    log.info(f"Shortcut {key_code} with value {key_value} requires {required_duration}s, but was held for {duration_held:.2f}s")

    return None
    #log.info(f"No valid shortcut mapped for touch input: {touch_input} with modifiers {active_modifiers}")


def send_key_event(key_code, key_value):
    global uinput_device

    if not uinput_device:
        log.error("Virtual device is not initialized. Cannot send key events.")
        return

    try:
        if isinstance(key_code, list):
            for c, v in zip(key_code, key_value):
                if c.type == EV_REL:
                    uinput_device.send_events([
                        InputEvent(c, v),
                        InputEvent(EV_SYN.SYN_REPORT, 0)  # Sync event
                    ])
        else:
            uinput_device.send_events([
                InputEvent(key_code, 1),
                InputEvent(EV_SYN.SYN_REPORT, 0)  # Sync event
            ])
            uinput_device.send_events([
                InputEvent(key_code, 0),
                InputEvent(EV_SYN.SYN_REPORT, 0)  # Sync event
         ])

        log.info(f"Sent key {key_code, key_value}")

    except Exception as e:
        log.error(f"Error sending key event: {e}")

def activate_dialpad():
    global dialpad, multi_app_mode_titles, multi_app_mode_icons

    # unlock
    send_value_to_touchpad_via_i2c("0x60")
    # activate
    send_value_to_touchpad_via_i2c("0x01")

    config_set(CONFIG_ENABLED, True)

    dialpad = True

    if socket_enabled:
        send_to_socket({CONFIG_ENABLED: dialpad})

        if multi_app_mode:
            send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": None})

def deactivate_dialpad():
    global dialpad

    # lock
    send_value_to_touchpad_via_i2c("0x61")
    # deactivate
    send_value_to_touchpad_via_i2c("0x00")

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

def handle_rotation(direction):
    """
    Handle rotation events based on the detected direction.
    :param direction: "clockwise" or "counterclockwise"
    """
    log.info(f"Handling rotation: {direction}")
    if direction == "clockwise":
        # Example: Increase volume
        log.info("Volume up triggered")
        send_key_event(EV_KEY.KEY_VOLUMEUP)  # Replace with your specific action
    elif direction == "counterclockwise":
        # Example: Decrease volume
        log.info("Volume down triggered")
        send_key_event(EV_KEY.KEY_VOLUMEDOWN)  # Replace with your specific action

def is_pressed_touchpad_top_right_icon():
    global top_right_icon_width, top_right_icon_height, abs_mt_slot_x_values, abs_mt_slot_y_values, abs_mt_slot_value, maxx

    if abs_mt_slot_x_values[abs_mt_slot_value] >= maxx - top_right_icon_width and\
        abs_mt_slot_y_values[abs_mt_slot_value] >= 0 and abs_mt_slot_y_values[abs_mt_slot_value] <= top_right_icon_height:
            return True

    return False

def check_dialpad_automatical_disable_or_idle_due_inactivity():
    global disable_due_inactivity_time, last_event_time, dialpad, stop_threads

    while not stop_threads:

        if\
            disable_due_inactivity_time and\
            dialpad and\
            last_event_time != 0 and\
            time() > disable_due_inactivity_time + last_event_time:

            deactivate_dialpad()
            log.info("DialPad deactivated")

        sleep(1)


gsettings_failure_count = 0
gsettings_max_failure_count = 1

qdbus_failure_count = 0
qdbus_max_failure_count = 1

gnome_failure_count = 0
gnome_max_failure_count = 1

xinput_failure_count = 0
xinput_max_failure_count = 1

synclient_status_failure_count = 0
synclient_status_max_failure_count = 1

def qdbusSet(cmd):
    global qdbus_failure_count, qdbus_max_failure_count

    if qdbus_failure_count < qdbus_max_failure_count:
        try:
            ret = subprocess.call(cmd)

            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)

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
            log.error('Setting libinput Send Events via xinput failed')

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

    global display, keyboard_state, xkb_conn
    
    if not modifier_names:
        return True

    # wayland
    if keyboard_state:
        try:
            for modifier_name in modifier_names:
                #modifier_keysym = mod_name_to_specific_keysym_name(modifier_name)  
                idx = keyboard_state.keymap.mod_get_index(modifier_name)
                if idx >= 0 and not keyboard_state.mod_index_is_active(idx, xkb.StateComponent.XKB_STATE_MODS_DEPRESSED):
                    log.debug("Modifier %s not pressed (wayland xkb)", modifier_name)
                    return False
        except Exception:
            pass

    # x11
    if xkb_conn:

        X11_MODIFIER_INDEX = {
            "Shift": 0,
            "Control": 2,
            "Alt": 3
        }

        try:
            reply = xkb_conn(xcffib.xkb.key).GetState(
                xcffib.xkb.ID.UseCoreKbd
            ).reply()

            active_mods = reply.mods

            for modifier_name in modifier_names:
                idx = X11_MODIFIER_INDEX.get(modifier_name)
                if idx is not None and not (active_mods & (1 << idx)):
                    log.debug("Modifier %s not pressed (xcffib)", modifier_name)
                    return False

        except Exception as e:
            log.exception(f"Failed to get X11 modifier state via XKB: {e}")

    log.debug("All modifier keys pressed: %s", modifier_names)

    return True

# Store key press start times
key_press_times = {}

import subprocess

def get_current_value(shortcut_entry):
    value = shortcut_entry.get("value")
    if value:
        try:
            result = subprocess.check_output(value, shell=True).decode().strip()
            return result
        except Exception as e:
            log.debug(f"Failed to get value for shortcut: {e}")
            return None
    return None

def get_current_value_unit(shortcut_entry):
    value = shortcut_entry.get("unit")
    return value

def listen_touchpad_events():
    global slices_count, activation_time, last_event_time, dialpad, active_modifiers, coactivator_keys, title, multi_app_mode_titles, app_specific_shortcuts, window_binary

    try:

            # Define circle and slices
            circle_radius = circle_diameter / 2
            center_button_radius = center_button_diameter / 2


            # Define the bounds for the top-right icon
            top_right_icon_bounds = {
                "x_min": max_x - top_right_icon_width,
                "x_max": max_x,
                "y_min": 0,
                "y_max": top_right_icon_height
            }

            log.info("Listening to touchpad events...")

            touch_x, touch_y = None, None
            finger_detected = False  # Track finger presence
            touch_start_time = None  # Track the time when the touch started
            within_top_right_icon = False  # Track if the touch is within the top-right icon bounds
            icon_activated = False  # Track if the icon has already been activated during this touch
            last_slice = None  # Track the last active slice in the circle
            center_button_triggered = False
            tap_disabled = False  # Track tap-to-click status
            coactivator_blocked = False
            center_enter_time = 0
            title = None
            center_activated = False
            window_binary = None

            for event in d_t.events():

                last_event_time = time()

                # Handle finger detection
                if event.matches(EV_KEY.BTN_TOOL_FINGER):
                    if event.value == 1:  # Finger down
                        key_press_times[event.code] = time()
                        finger_detected = True
                        coactivator_blocked = False
                        touch_start_time = time()  # Record the touch start time
                        within_top_right_icon = False  # Reset the flag
                        icon_activated = False  # Reset activation status
                        last_slice = None  # Reset the last slice
                        log.debug("Finger detected.")

                    elif event.value == 0:  # Finger up
                        finger_detected = False
                        touch_start_time = None  # Reset the touch start time
                        within_top_right_icon = False  # Reset the flag
                        icon_activated = False  # Reset activation status
                        last_slice = None  # Reset the last slice
                        log.debug("Finger lifted.")
                        touch_x, touch_y = None, None

                        duration_held = time() - center_enter_time
                        if center_button_triggered:

                            if socket_enabled:
                                if not multi_app_mode:
                                    shortcut = emulate_shortcuts(app_specific_shortcuts, "center", event.value, active_modifiers, duration_held)
                                    if shortcut:
                                        center_button_triggered = False
                                elif title:
                                    if app_specific_shortcuts['center']:
                                        center_shortcut = emulate_shortcuts(app_specific_shortcuts, 'center', event.value, active_modifiers, duration_held)
                                        if center_shortcut:
                                            center_button_triggered = False

                                            if not center_activated:
                                                center_activated = True
                                                value = get_current_value(app_specific_shortcuts[title])
                                                value_unit = get_current_value_unit(app_specific_shortcuts[title])
                                                send_to_socket({"value": value, "unit": value_unit, "title": title})
                                            else:
                                                center_activated = False
                                    else:
                                        value = get_current_value(shortcut)
                                        value_unit = get_current_value_unit(shortcut)
                                        send_to_socket({"value": value, "unit": value_unit, "title": title})
                                        center_button_triggered = False

                                        if not center_activated:
                                            center_activated = True
                                        else:
                                            center_activated = False
                                            title = None

                        if multi_app_mode and not center_activated and socket_enabled:
                            send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": None})

                        # Re-enable tap-to-click
                        if tap_disabled:
                            set_touchpad_prop_send_events(1)
                            tap_disabled = False

                # Detect touch positions
                if event.matches(EV_ABS.ABS_MT_POSITION_X):
                    touch_x = event.value
                elif event.matches(EV_ABS.ABS_MT_POSITION_Y):
                    touch_y = event.value

                # Check if the touch is in the top-right icon bounds
                if touch_x is not None and touch_y is not None and finger_detected:
                    if (top_right_icon_bounds["x_min"] <= touch_x <= top_right_icon_bounds["x_max"] and
                            top_right_icon_bounds["y_min"] <= touch_y <= top_right_icon_bounds["y_max"]):
                        if not within_top_right_icon:
                            log.debug("Touch entered top-right icon bounds.")
                        within_top_right_icon = True  # Mark that the touch is inside the bounds

                        # Check if the touch duration exceeds the threshold and hasn't been activated yet
                        if touch_start_time and not icon_activated and not coactivator_blocked:
                            if (time() - touch_start_time) >= activation_time:

                                if dialpad or (not dialpad and are_modifier_keys_pressed(coactivator_keys)):
                                    toggle_top_right_icon(dialpad)
                                    icon_activated = True
                                else:
                                    log.debug("DialPad activation blocked: co-activator key(s) not pressed: %s", " ".join(coactivator_keys))
                                    coactivator_blocked = True
                    else:
                        if within_top_right_icon:
                            log.debug("Touch left top-right icon bounds. Canceling the action.")
                        within_top_right_icon = False  # Mark that the touch has left the bounds
                        touch_start_time = None  # Cancel the action by resetting the start time
                        icon_activated = False  # Reset the activation status to allow future activation

                # Calculate distance and angle for circle-based detection
                if touch_x is not None and touch_y is not None and finger_detected:
                    dx = touch_x - circle_center_x
                    dy = touch_y - circle_center_y
                    distance = math.sqrt(dx**2 + dy**2)
                    angle = (math.atan2(dy, dx) * 180 / math.pi + 90) % 360

                    if distance <= circle_radius and dialpad:

                        # Disable tap-to-click
                        if not tap_disabled:
                            set_touchpad_prop_send_events(0)
                            tap_disabled = True

                        #log.info("Distance: %f, Center button radius: %f", distance, center_button_radius)
                        if distance < center_button_radius and dialpad:  # Center button area
                            # Only trigger if it has not been triggered already in this touch cycle
                            if not center_button_triggered:
                                log.debug("Touch detected in center button area.")
                                send_to_socket({"input": "center", "value": 1, "title": title})
                                center_enter_time = time()
                                # Trigger the center button shortcut
                                duration_held = time() - center_enter_time
        
                                #if not multi_app_mode:
                                    #shortcut = emulate_shortcuts(app_specific_shortcuts, "center", event.value, active_modifiers, duration_held)
                                    #if shortcut and socket_enabled:
                                        #title = shortcut.get("title", None)
                                        #send_to_socket({"input": "center", "value": 1, "title": title})
                                        #send_to_socket({"input": "center", "value": 0, "title": title})
                                center_button_triggered = True  # Set flag to indicate the button has been pressed
                                icon_activated = True  # Ensure it only triggers once per touch
                        else:

                            if multi_app_mode and not center_activated:
                                slices_count_local = multi_app_mode_grid
                            else:
                                # from the config
                                slices_count_local = slices_count
                                pass

                            # Determine the current slice based on the angle
                            current_slice = int(angle // (360 / slices_count_local))

                            log.debug(
                                "TOUCH x=%d y=%d | center x=%d y=%d | dx=%d dy=%d | "
                                "angle=%.1f° | slice_size=%.1f° | slice=%d/%d",
                                touch_x, touch_y,
                                circle_center_x, circle_center_y,
                                touch_x - circle_center_x,
                                touch_y - circle_center_y,
                                angle,
                                360 / slices_count_local,
                                current_slice,
                                slices_count_local
                            )

                            if current_slice != last_slice or center_button_triggered:

                                # Reset the center button triggered flag if the finger leaves the circle (but not the button area)
                                if center_button_triggered:
                                    center_button_triggered = False
                                    icon_activated = False  # Allow the center button action to be triggered again

                                if last_slice is None:
                                    last_slice = current_slice

                                # Trigger rotation logic based on the direction
                                if not multi_app_mode or center_activated:
                                    # Determine the direction of rotation
                                    direction = "clockwise" if (current_slice - last_slice) % slices_count_local == 1 else "counterclockwise"
                                    log.debug(f"Detected circular motion: {direction}")

                                    general_value = None
                                    if center_activated and title in app_specific_shortcuts:
                                        sht = app_specific_shortcuts[title]
                                        general_value = get_current_value(sht)
                                        general_unit = get_current_value_unit(sht)
                                    else:
                                        sht = app_specific_shortcuts

                                    shortcut = emulate_shortcuts(sht, direction, event.value, active_modifiers)
                                    if shortcut and socket_enabled:

                                        value = get_current_value(shortcut)
                                        if value is None and general_value:
                                            value = general_value

                                        unit = get_current_value_unit(shortcut)
                                        if unit is None and general_unit:
                                            unit = general_unit

                                        try:
                                            title = shortcut["title"]
                                        except:
                                            pass                                          

                                        send_to_socket({"input": direction, "value": value, "unit": unit, "title": title})
                                else:
                                    try:
                                        title = multi_app_mode_titles[current_slice]
                                    except:
                                        # may be empty spot
                                        title = None

                                    if socket_enabled:
                                        send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": title})
                                        
                                last_slice = current_slice
                    else:
                        log.debug("Touch outside the circle. Ignoring.")

    except device.EventsDroppedException:
        for e in dev.sync(True):
            pass

        listen_touchpad_events()
    except Exception:
        log.exception("Error in listen_touchpad_events")

def check_config_values_changes():
    global config_lock, stop_threads, event_notifier

    while not stop_threads:
        try:
            event_notifier.process_events()
            if event_notifier.check_events():
                event_notifier.read_events()

                if not config_lock.locked():
                    log.info("check_config_values_changes: detected external change of config file -> loading changes")
                    # because file might be read so fast that changes will not be there yet
                    sleep(0.1)
                    load_all_config_values()
                else:
                    log.info("check_config_values_changes: detected internal change of config file -> do nothing -> would be deadlock")

        except KeyboardInterrupt:
            break

    log.info("check_config_values_changes: inotify watching config file ended")


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

udev = None
threads = []
stop_threads = False
enabled_evdev_keys = []

# only to avoid first - x11 only
gnome_current_layout = None
# only to avoid first - x11 even wayland (e.g. Ubuntu 22.04)
gnome_current_layout_index = None
keysym_name_associated_to_evdev_key_reflecting_current_layout = None


def mod_name_to_specific_keysym_name(mod_name):
    global display_wayland

    mod_to_specific_keysym_name = {
        'Control': 'Control_L',
        'Shift': 'Shift_L',
        'Lock': 'Caps_Lock',
        'Mod1': 'Alt_L',
        'Mod2': 'Num_Lock',
        'Mod3': 'Caps_Lock',
        'Mod4': 'Meta_L',
        'Mod5': 'Scroll_Lock',
        'NumLock': 'Num_Lock',
        'Alt': 'Alt_L',
        'LevelThree': 'ISO_Level3_Shift',
        'LAlt': 'Alt_L',
        'RAlt': 'Alt_R',
        'RControl': 'Control_R',
        'LControl': 'Control_L',
        'ScrollLock': 'Scroll_Lock',
        'LevelFive': 'ISO_Level5_Shift',
        'AltGr': 'Alt_R',
        'Meta': 'Meta_L',
        'Super': 'Meta_L',
        'Hyper': 'Hyper_L'
    }

    mods_to_indexes_x11 = {
        "Shift": Xlib.X.ShiftMapIndex,
        "Lock": Xlib.X.LockMapIndex,
        "Control": Xlib.X.ControlMapIndex,
        "Mod1": Xlib.X.Mod1MapIndex,
        "Mod2": Xlib.X.Mod2MapIndex,
        "Mod3": Xlib.X.Mod3MapIndex,
        "Mod4": Xlib.X.Mod4MapIndex,
        "Mod5": Xlib.X.Mod5MapIndex
    }

    if display and mod_name in mods_to_indexes_x11:

      mods = display.get_modifier_mapping()
      first_keycode = mods[mods_to_indexes_x11[mod_name]][0]
      if first_keycode:
        key = EV_KEY.codes[int(first_keycode) - 8]
        keysym = display.keycode_to_keysym(first_keycode, 0)
        for key in Xlib.XK.__dict__:
          if key.startswith("XK") and Xlib.XK.__dict__[key] == keysym:
            return key[3:]
      else:
        return mod_to_specific_keysym_name[mod_name]
    elif display_wayland:

        keymap = keyboard_state.get_keymap()
        num_mods = keymap.num_mods()

        for keycode in keymap:
            keyboard_state_clean = keymap.state_new()
            key_state = keyboard_state_clean.update_key(keycode, xkb.KeyDirection.XKB_KEY_DOWN)

            num_layouts = keymap.num_layouts_for_key(keycode)
            for layout in range(0, num_layouts):

                if gnome_current_layout_index is not None and gnome_current_layout_index == layout:
                    layout_is_active = True
                else:
                    layout_is_active = keyboard_state_clean.layout_index_is_active(layout, xkb.StateComponent.XKB_STATE_LAYOUT_EFFECTIVE)

                if layout_is_active:
                    for mod_index in range(0, num_mods):

                        is_key_mod = key_state & xkb.StateComponent.XKB_STATE_MODS_DEPRESSED
                        if is_key_mod:

                            is_mod_active = keyboard_state_clean.mod_index_is_active(mod_index, xkb.StateComponent.XKB_STATE_MODS_DEPRESSED)
                            if is_mod_active:
                                if keymap.mod_get_name(mod_index) == mod_name:

                                    keysyms = keymap.key_get_syms_by_level(keycode, layout, 0)

                                    if len(keysyms) != 1:
                                        continue

                                    keysym_name = xkb.keysym_get_name(keysyms[0])
                                    #log.info(mod_name)
                                    #log.info(keycode)
                                    #log.info(keysym_name)
                                    return keysym_name

    else:
      return mod_to_specific_keysym_name[mod_name]

def listen_keyboard_events():
    """
    Listen for keyboard events to track active modifier keys.
    """
    global active_modifiers, modifiers, keyboard

    if keyboard is None:
        log.warning("No keyboard detected; skipping keyboard listener.")
        return

    log.info("Listening to keyboard events...")

    try:
        fd_k = open('/dev/input/event' + str(keyboard), 'rb')
        d_k = Device(fd_k)

        for event in d_k.events():
            if event.code in modifiers:

                if event.value == 1:  # Key Pressed
                    active_modifiers.add(event.code)
                elif event.value == 0:  # Key Released
                    active_modifiers.discard(event.code)

                log.debug(f"Active modifiers: {active_modifiers}")

    except device.EventsDroppedException:
        for e in dev.sync(True):
            pass
    except Exception as e:
        log.error(f"Error in listen_touchpad_events: {e}")

# default are for unicode shortcuts + is loaded layout during start (BackSpace, Return - enter, asterisk, minus etc. can be found using xev)
def set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout():
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    keysym_name_associated_to_evdev_key_reflecting_current_layout = {
         # unicode shortcut - for hex value
        '0': '',
        '1': '',
        '2': '',
        '3': '',
        '4': '',
        '5': '',
        '6': '',
        '7': '',
        '8': '',
        '9': '',
        'a': '',
        'b': '',
        'c': '',
        'd': '',
        'e': '',
        'f': '',
        # unicode shortcut - start sequence
        mod_name_to_specific_keysym_name('Shift'): '',
        mod_name_to_specific_keysym_name('Control'): '',
        'u': '',
        # unicode shortcut - end sequence
        'space': ''
    }

def get_keysym_name_associated_to_evdev_key_reflecting_current_layout():
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    # lazy initialization because of loading modifiers inside Shift & Control
    if not keysym_name_associated_to_evdev_key_reflecting_current_layout:
        set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout()

    return keysym_name_associated_to_evdev_key_reflecting_current_layout

def load_evdev_key_for_wayland(char, keyboard_state):
    global gnome_current_layout_index

    keysym = xkb.keysym_from_name(char)

    keymap = keyboard_state.get_keymap()
    num_mods = keymap.num_mods()

    for keycode in keymap:

        num_layouts = keymap.num_layouts_for_key(keycode)
        for layout in range(0, num_layouts):

            num_levels = keymap.num_levels_for_key(keycode, layout)

            for level in range(0, num_levels):
                mod_masks_for_level = keymap.key_get_mods_for_level(keycode, layout, level)

                if len(mod_masks_for_level) < 1:
                    continue

                keysyms = keymap.key_get_syms_by_level(keycode, layout, level)

                if len(keysyms) != 1 or keysyms[0] != keysym:
                    continue

                for mod_mask_index in range(0, len(mod_masks_for_level)):

                    mod_evdev_keys = []
                    for mod_index in range(0, num_mods):

                        if (mod_masks_for_level[mod_mask_index] & (1 << mod_index) == 0):
                            continue

                        mod_name = keymap.mod_get_name(mod_index)

                        mod_as_evdev_key = load_evdev_key_for_wayland(mod_name_to_specific_keysym_name(mod_name), keyboard_state)
                        mod_evdev_keys.append(mod_as_evdev_key)

                        if not mod_as_evdev_key:
                            continue

                    if len(mod_evdev_keys) > 0:
                        key = mod_evdev_keys + [EV_KEY.codes[int(keycode - 8)]]
                    else:
                        key = EV_KEY.codes[int(keycode - 8)]


                    if gnome_current_layout_index is not None and gnome_current_layout_index == layout:
                        layout_is_active = True
                    else:
                        layout_is_active = keyboard_state.layout_index_is_active(layout, xkb.StateComponent.XKB_STATE_LAYOUT_EFFECTIVE)

                    enable_key(key)

                    if layout_is_active:
                        set_evdev_key_for_char(char, key)
                        return key

def wl_load_keymap_state():
    global keyboard_state, keymap_loaded, udev

    log.debug("Wayland will try to load keymap")

    enabled_keys = len(enabled_evdev_keys)

    for char in get_keysym_name_associated_to_evdev_key_reflecting_current_layout().copy():
        load_evdev_key_for_wayland(char, keyboard_state)

    # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
    #
    # BUT only reset if event is not first one - driver is starting and keymap is not loaded yet
    if len(enabled_evdev_keys) > enabled_keys and keymap_loaded and udev:
        reset_udev_device()

    keymap_loaded = True

    log.debug("Wayland loaded keymap succesfully")
    log.debug(get_keysym_name_associated_to_evdev_key_reflecting_current_layout())

def extract_icons(app_specific_shortcuts):
    icons = {}

    if not isinstance(app_specific_shortcuts, dict):
        return icons

    for name, definition in app_specific_shortcuts.items():
        if not isinstance(definition, dict):
            continue

        icon = definition.get("icon")
        if icon:
            icons[name] = icon

    return icons


def check_window():
    global stop_threads, window_binary, window_title, app_name, app_specific_shortcuts, multi_app_mode, multi_app_mode_titles, multi_app_mode_icons,\
        center_activated, title

    while not stop_threads:
        window_binary_local, window_title = get_active_window_title()
        app_name_local, app_specific_shortcuts = get_appropriate_app_name_and_shortcuts(window_binary, window_title)
        multi_app_mode_local, multi_app_mode_titles_local, multi_app_mode_icons_local = is_multifunction(app_specific_shortcuts)

        update = False

        if window_binary_local != window_binary:
            window_binary = window_binary_local
            update = True

        if app_name_local != app_name:
            app_name = app_name_local
            update = True

        if multi_app_mode_local != multi_app_mode:
            multi_app_mode = multi_app_mode_local
            update = True

        if multi_app_mode_titles_local != multi_app_mode_titles:
            multi_app_mode_titles = multi_app_mode_titles_local
            update = True

        if multi_app_mode_icons_local != multi_app_mode_icons:
            multi_app_mode_icons = multi_app_mode_icons_local
            update = True

        if update:
            send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": None})
            center_activated = False

        sleep(0.5)


def check_gnome_layout():
    global stop_threads, gnome_current_layout, gnome_current_layout_index, keyboard_state, display_wayland_var, display_var

    while not stop_threads:

        mru_sources = gsettingsGet('org.gnome.desktop.input-sources', 'mru-sources')
        try:
          mru_sources_evaluated = ast.literal_eval(mru_sources.decode())
        except:
          mru_sources_evaluated = []

        sources = gsettingsGet('org.gnome.desktop.input-sources', 'sources')
        try:
          sources_evaluated = ast.literal_eval(sources.decode())
        except:
          sources_evaluated = []

        if len(mru_sources_evaluated) > 0:

            mru_layout_index = sources_evaluated.index(mru_sources_evaluated[0])
            mru_layout = mru_sources_evaluated[0][1].split("+")[0]

            if display_wayland_var:
                if keyboard_state and gnome_current_layout_index is not mru_layout_index:

                    gnome_current_layout_index =  mru_layout_index
                    gnome_current_layout = mru_layout
                    wl_load_keymap_state()

            elif gnome_current_layout != mru_layout:

                    try:
                        cmd = ['setxkbmap', mru_layout, '-display', display_var]

                        log.debug(cmd)
                        subprocess.call(cmd)

                        gnome_current_layout = mru_layout
                        gnome_current_layout_index =  mru_layout_index
                    except:
                        log.exception('setxkbmap set failed')

        else:

            current = gsettingsGet('org.gnome.desktop.input-sources', 'current')

            current_evaluated = None
            try:
              current_evaluated = ast.literal_eval(current.decode().split(" ")[1])
            except:
              pass

            if current_evaluated is not None and current_evaluated < len(sources_evaluated):
                layout = sources_evaluated[current_evaluated][1].split("+")[0]

                # first run, would be unnecessary duplicated loading x11 keymap because X.org server notify all clients at start about Mapping and setxkbmap would trigger new second notify
                if gnome_current_layout == None:
                    gnome_current_layout = layout

                elif gnome_current_layout != layout:

                    try:
                        cmd = ['setxkbmap', layout, '-display', display_var]

                        log.debug(cmd)
                        subprocess.call(cmd)

                        gnome_current_layout = layout
                    except:
                        log.exception('setxkbmap set failed')

        sleep(0.5)


def cleanup():
    global dialpad, display, display_wayland, stop_threads, event_notifier, watch_manager, xkb_conn, sock

    log.info("Clean up started")

    # try deactivate first
    try:
        if dialpad:

            dialpad = False
            deactivate_dialpad()
            log.info("DialPad deactivated")

        # then clean up
        stop_threads=True

        fd_t.close()

        if display_wayland:
            display_wayland.disconnect()

        if display:
            try:
                display.close()
            # because may be already closed (e.g. closed connection by server in load_keymap_listener_x11)
            except:
                pass

        try:
            if xkb_conn is not None:
                xkb_conn.disconnect()
        except Exception:
            pass

        if watch_manager:
            watch_manager.close()

        if sock:
            sock.close()

        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        log.info("Clean up finished")
    except:
        log.exception("Clean up error")
        pass

threads = []
stop_threads = False
watch_manager = None
event_notifier = None

def isEvent(event):
    if hasattr(event, "name") and (hasattr(EV_KEY, event.name) or hasattr(EV_REL, event.name)):
        return True
    else:
        return False

def isEventList(events):
    if type(events) is list:
        for event in events:
            if not isEvent(event):
                return False
        return True
    else:
        return False

def enable_key(key_or_key_combination, reset_udev = False):
    global enabled_evdev_keys, dev, udev

    enabled_keys_count = len(enabled_evdev_keys)

    if isEvent(key_or_key_combination):
      if key_or_key_combination not in enabled_evdev_keys:
          enabled_evdev_keys.append(key_or_key_combination)
          dev.enable(key_or_key_combination)
    elif isEventList(key_or_key_combination):
      for key in key_or_key_combination:
        if key not in enabled_evdev_keys:
          enabled_evdev_keys.append(key)
          dev.enable(key)

    # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
    if len(enabled_evdev_keys) > enabled_keys_count and reset_udev:
      reset_udev_device()

def set_evdev_key_for_char(char, evdev_key):
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    # lazy initialization because of loading modifiers inside Shift & Control
    if not keysym_name_associated_to_evdev_key_reflecting_current_layout:
        set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout()

    keysym_name_associated_to_evdev_key_reflecting_current_layout[char] = evdev_key

def load_evdev_key_for_x11(char):
    global display, keysym_name_associated_to_evdev_key_reflecting_current_layout

    keysym = Xlib.XK.string_to_keysym(char)

    if keysym == 0:
      return

    keycode = display.keysym_to_keycode(keysym)
    key = EV_KEY.codes[int(keycode) - 8]

    # bare
    if display.keycode_to_keysym(keycode, 0) == keysym:
      pass
    # shift
    elif display.keycode_to_keysym(keycode, 1) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('Shift')), key]
    # altgr
    elif display.keycode_to_keysym(keycode, 2) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('AltGr')), key]
    # shift altgr
    elif display.keycode_to_keysym(keycode, 3) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('Shift')), load_evdev_key_for_x11(mod_name_to_specific_keysym_name('AltGr')), key]

    set_evdev_key_for_char(char, key)

    enable_key(key)

    return key

# necessary when are new keys enabled
def reset_udev_device():
    global dev, udev

    log.info("Old device at {} ({})".format(udev.devnode, udev.syspath))
    udev = dev.create_uinput_device()
    log.info("New device at {} ({})".format(udev.devnode, udev.syspath))

    # Sleep for a little bit so udev, libinput, Xorg, Wayland, ... all have had
    # a chance to see the device and initialize it. Otherwise the event
    # will be sent by the kernel but nothing is ready to listen to the
    # device yet
    sleep(0.5)

def load_evdev_keys_for_x11():
  global enabled_evdev_keys, keymap_loaded, udev

  log.debug("X11 will try to load keymap")

  enabled_keys_count = len(enabled_evdev_keys)

  for char in get_keysym_name_associated_to_evdev_key_reflecting_current_layout().copy():
    load_evdev_key_for_x11(char)

  # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
  #
  # BUT only reset if event is not first one - driver is starting and keymap is not loaded yet
  if len(enabled_evdev_keys) > enabled_keys_count and keymap_loaded and udev:
    reset_udev_device()

  keymap_loaded = True

  log.debug("X11 loaded keymap succesfully")
  log.debug(get_keysym_name_associated_to_evdev_key_reflecting_current_layout())

def wl_keyboard_keymap_handler(keyboard, format_, fd, size):
    global keyboard_state

    keymap_data = mmap.mmap(
       fd, size, prot=mmap.PROT_READ, flags=mmap.MAP_PRIVATE
    )
    xkb_context = xkb.Context()
    keymap = xkb_context.keymap_new_from_buffer(keymap_data, length=size - 1)
    keymap_data.close()

    keyboard_state = keymap.state_new()

    wl_load_keymap_state()

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
        display_wayland = Display(display_wayland_var)
        display_wayland.connect()
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
    global stop_threads, display, listening_touchpad_events_started

    try:

      while not stop_threads:

        event = display.next_event()
        if event.type == Xlib.X.MappingNotify and event.count > 0 and event.request == Xlib.X.MappingKeyboard:

          if listening_touchpad_events_started or not keymap_loaded:
            display.refresh_keyboard_mapping(event)
            load_evdev_keys_for_x11()
            #raise Xlib.error.ConnectionClosedError("fd") # testing purpose only
    except:
      log.exception("X11 load keymap listener error. Exiting")
      os.kill(os.getpid(), signal.SIGUSR1)

try:
    # init the socket
    init_socket()

    # init the device
    initialize_virtual_device()

    if xdg_session_type == "wayland":
        t = threading.Thread(target=load_keymap_listener_wayland)
        t.daemon = True
        threads.append(t)
        t.start()

    if xdg_session_type == "x11" and display:

        # when is the driver starting event is not received
        load_evdev_keys_for_x11()

        t = threading.Thread(target=load_keymap_listener_x11)
        t.daemon = True
        threads.append(t)
        t.start()

    # wait until is keymap loaded
    while not keymap_loaded:
        sleep(0.5)

    # Load config values
    load_all_config_values()
    config_lock.acquire()
    config_save()
    config_lock.release()
    # because inotify (deadlock)
    sleep(0.5)

    watch_manager = WatchManager()

    path = os.path.abspath(config_file_dir)
    mask = IN_CLOSE_WRITE | IN_IGNORED | IN_MOVED_TO
    watch_manager.add_watch(path, mask)

    event_notifier = AsyncNotifier(watch_manager)

    t = threading.Thread(target=check_dialpad_automatical_disable_or_idle_due_inactivity)
    t.daemon = True
    threads.append(t)
    t.start()

    if keyboard:
        t = threading.Thread(target=listen_keyboard_events)
        t.daemon = True
        threads.append(t)
        t.start()

    t = threading.Thread(target=check_gnome_layout)
    t.daemon = True
    threads.append(t)
    t.start()

    t = threading.Thread(target=check_window)
    t.daemon = True
    threads.append(t)
    t.start()
    
    # Start the touchpad listener in a separate thread
    listen_touchpad_events()
except Exception:
    logging.exception("Listening touchpad events unexpectedly failed")
finally:
    cleanup()
    log.info("Exiting")
    sys.exit(1)
