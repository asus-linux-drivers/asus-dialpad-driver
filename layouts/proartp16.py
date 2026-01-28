from libevdev import EV_KEY, EV_REL

top_right_icon_width = 250
top_right_icon_height = 250

circle_diameter = 919
center_button_diameter = 364
circle_center_x = 586
circle_center_y = 573


#
# below is just example configuration for some applications
#
app_shortcuts = {
    "/usr/share/code/code": {
        "center": [
            {"trigger": "release", "duration": 0.5}
        ],
        "Notifications": {
          "value": "dconf read /io/elementary/notifications/do-not-disturb",
          "icons": {
              "true": "/usr/share/icons/elementary/status/symbolic/notification-disabled-symbolic.svg",
              "false": "/usr/share/icons/elementary/status/symbolic/notification-symbolic.svg"
          },
          # toggles do-not-disturb
          "command": 'dconf write /io/elementary/notifications/do-not-disturb "$( [ "$(dconf read /io/elementary/notifications/do-not-disturb)" = "true" ] && echo false || echo true )"',
        },
        "Edit": {
            "icon": "/usr/share/icons/elementary/status/symbolic/media-playlist-repeat-symbolic-rtl.svg",
            "treshold": 180,
            "clockwise": [
                {
                    "key": [EV_KEY.KEY_LEFTCTRL, EV_KEY.KEY_Y],
                    "trigger": "immediate"
                }
            ],
            "counterclockwise": [
                {
                    "key": [EV_KEY.KEY_LEFTCTRL, EV_KEY.KEY_Z],
                    "trigger": "immediate"
                }
            ]
        },
        "Volume": {
            "icon": "/usr/share/icons/elementary/status/symbolic/audio-volume-medium-symbolic.svg",
            "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\\d+%' | head -n 1 | tr -d '%'",
            "unit": "%",
            "clockwise": [
              {"command": "pactl set-sink-volume @DEFAULT_SINK@ +8%", "trigger": "immediate"}
            ],
            "counterclockwise": [
              {"command": "pactl set-sink-volume @DEFAULT_SINK@ -8%", "trigger": "immediate"}
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
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT, "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\\d+%' | head -n 1 | tr -d '%'", "unit": "%", "title": "Volume"}
        ],
        "counterclockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate", "title": "Scroll"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT, "value": "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\\d+%' | head -n 1 | tr -d '%'", "unit": "%", "title": "Volume"}
        ]
    }
}