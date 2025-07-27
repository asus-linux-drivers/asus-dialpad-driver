from libevdev import EV_KEY

top_right_icon_width = 250
top_right_icon_height = 250

circle_diameter = 1400
center_button_diameter = 250
circle_center_x = 770
circle_center_y = 750

app_shortcuts = {
    "code": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "duration": 1},
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "modifier": EV_KEY.KEY_LEFTSHIFT},
        ],
        "clockwise": [
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    },
    "firefox": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release"},
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "clockwise": [
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    },
    "none": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release"},
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "clockwise": [
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    }
}