from libevdev import EV_KEY, EV_REL

top_right_icon_width = 250
top_right_icon_height = 250

circle_diameter = 919
center_button_diameter = 364
circle_center_x = 586
circle_center_y = 573

app_shortcuts = {
    "code": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "duration": 1},
        ],
        "clockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [1, 120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    },
    "firefox": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "duration": 1},
        ],
        "clockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [1, 120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    },
    "none": {
        "center": [
          {"key": EV_KEY.KEY_MUTE, "trigger": "release", "duration": 1},
        ],
        "clockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [1, 120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEUP, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ],
        "counterclockwise": [
          {"key": [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES], "value": [-1, -120], "trigger": "immediate"},
          {"key": EV_KEY.KEY_VOLUMEDOWN, "trigger": "immediate", "modifier": EV_KEY.KEY_LEFTSHIFT}
        ]
    }
}