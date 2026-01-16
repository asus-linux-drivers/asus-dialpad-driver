from libevdev import EV_KEY, EV_REL

top_right_icon_width = 250
top_right_icon_height = 250

circle_diameter = 1400
center_button_diameter = 250
circle_center_x = 770
circle_center_y = 750

# current_value and title is optional because it is used for the user interface
app_shortcuts = {
    "/usr/share/code/code": {
        "center": [
          {"trigger": "immediate", "duration": 0.5}
        ],
        "Volume": {
            "icon": "/usr/share/icons/elementary/status/symbolic/audio-volume-medium-symbolic.svg",
            "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\d+%' | head -n 1 | tr -d '%'",
            "unit": "%",
            "clockwise": [
              # works even better with `dconf write /org/gnome/desktop/sound/allow-volume-above-100-percent true`
              {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate"}
            ],
            "counterclockwise": [
              # works even better with `dconf write /org/gnome/desktop/sound/allow-volume-above-100-percent true`
              {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate"}
            ]
        },
        "Scroll": {
            "icon": "/usr/share/icons/elementary/status/symbolic/rotation-allowed-symbolic.svg",
            "clockwise": [
               {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [1, 120], "trigger": "immediate"},
            ],
            "counterclockwise": [
              {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate"},
            ]
        },
        "Brightness": {
            "icon": "/usr/share/icons/elementary/status/symbolic/display-brightness-symbolic.svg",
            # requires to install `$ sudo apt install brightnessctl`
            #
            # $ brightnessctl -m
            #       intel_backlight,backlight,343,86%,400
            #
            "value": "brightnessctl -m | cut -d, -f4 | tr -d '%'",
            "unit": "%",
            "clockwise": [
              {"key": EV_KEY.KEY_BRIGHTNESSUP, "trigger": "immediate"},
            ],
            "counterclockwise": [
              {"key": EV_KEY.KEY_BRIGHTNESSDOWN, "trigger": "immediate"},
            ]
        }
    },
    "firefox": {
        # ... e.g. same as below (single function mode) or above (multi-function mode combined with single function mode)...
    },
    "none": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "duration": 1, "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "clockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [1, 120], "trigger": "immediate", "title": "Scroll"},
          # works even better with `dconf write /org/gnome/desktop/sound/allow-volume-above-100-percent true`
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT, "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\d+%' | head -n 1 | tr -d '%'", "unit": "%", "title": "Volume"}
        ],
        "counterclockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate", "title": "Scroll"},
          # works even better with `dconf write /org/gnome/desktop/sound/allow-volume-above-100-percent true`
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT, "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\d+%' | head -n 1 | tr -d '%'", "unit": "%", "title": "Volume"}
        ]
    }
}