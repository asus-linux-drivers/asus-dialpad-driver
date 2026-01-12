# Asus touchpad DialPad driver

[![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)
![Maintainer](https://img.shields.io/badge/maintainer-ldrahnik-blue)
[![GitHub Release](https://img.shields.io/github/release/asus-linux-drivers/asus-dialpad-driver.svg?style=flat)](https://github.com/asus-linux-drivers/asus-dialpad-driver/releases)
[![GitHub commits](https://img.shields.io/github/commits-since/asus-linux-drivers/asus-dialpad-driver/v2.0.0.svg)](https://GitHub.com/asus-linux-drivers/asus-dialpad-driver/commit/)
[![GitHub issues-closed](https://img.shields.io/github/issues-closed/asus-linux-drivers/asus-dialpad-driver.svg)](https://GitHub.com/asus-linux-drivers/asus-dialpad-driver/issues?q=is%3Aissue+is%3Aclosed)
[![GitHub pull-requests closed](https://img.shields.io/github/issues-pr-closed/asus-linux-drivers/asus-dialpad-driver.svg)](https://github.com/asus-linux-drivers/asus-dialpad-driver/compare)
[![Ask Me Anything !](https://img.shields.io/badge/Ask%20about-anything-1abc9c.svg)](https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/new/choose)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)
![Badge](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fasus-linux-drivers%2Fasus-dialpad-driver&label=Visitors&icon=suit-heart-fill&color=%23e35d6a)
--
[![Nix Flakes: Compatible](https://img.shields.io/badge/Nix%20Flakes-Compatible-brightgreen)](https://github.com/asus-linux-drivers/asus-dialpad-driver#installation)

![Single function mode](preview/ui_single_function_mode.png) ![Multi function mode](preview/ui_multi_function_mode.png)

The driver is written in python and does not necessarily run as a systemd service ([How to start DialPad without systemd service?](#faq)). It contains the common DialPad layouts, you can pick up the right one during the install process. Default settings aim to be the most convenient for the majority. All possible customizations can be found [here](#configuration).

If you find this project useful, please do not forget to give it a [![GitHub stars](https://img.shields.io/github/stars/asus-linux-drivers/asus-dialpad-driver.svg?style=social&label=Star&maxAge=2592000)](https://github.com/asus-linux-drivers/asus-dialpad-driver/stargazers) People already did!

[![BuyMeACoffee](https://img.shields.io/badge/Buy%20to%20maintainer%20a%20coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://ko-fi.com/ldrahnik)

## Changelog

[CHANGELOG.md](CHANGELOG.md)

## Frequently Asked Questions

[FAQ](#faq)

## Features of User Interface

- Support for own icons in `.svg` format
- Double-clicking by left mouse button unlocks (and locks again) the elements position allowing it to be moved across the screen

## Features

- Single-function mode
- Multi-function mode
- Driver during installation collects anonymous data with goal improve driver (e.g. automatic layout detection; data are publicly available [here](https://lookerstudio.google.com/s/gaK2TftgZqM), you can provide used config using `$ bash install_config_send_anonymous_report.sh`)
- Driver (including backlighting if hardware supported) installed for the current user
- Driver creates own virtual environment of currently installed version of `Python3`
- Multiple pre-created [DialPad layouts](https://github.com/asus-linux-drivers/asus-dialpad-driver#layouts) with the possibility of [creating custom layouts or improving existing ones (circle_diameter, center_button_diameter, circle_center_x..)](https://github.com/asus-linux-drivers/asus-dialpad-driver#keyboard-layout)
- Customization through 2-way sync [configuration file](https://github.com/asus-linux-drivers/asus-dialpad-driver#configuration-file) (when `$ bash ./install.sh` is run, changes previously made in the config file will not be overwritten without user permission, similarly when `$ bash ./uninstall.sh` is run the config file will be kept. In either case, when the config file or parts of it do not exist they will be automatically created or completed with default values)
- Automatic DialPad layout detection
- Activation/deactivation of DialPad by pressing and holding the top-right icon (activation time by default is 1s)
- Optional co-activator key requirement (`Shift`, `Control`, `Alt`) to prevent accidental DialPad activation
- Recognize of currently focused app by binary path (e.g. `/usr/share/code/code`) or part of the title (during finding the first matched shortcut wins so `visual studio code` defined after `code` will be never be matched)
- For configured apps have a single-function mode (where only possible distinction of shortcuts is by key modifier like Shift) or multi-function mode (with confirmation/going back to menu by middle button; 4 apps in menu by default changable by config value `slices_count`)
- Adding events for `clockwise`, `counterclockwise` or `center` button, the circle is delimeted to slices according to config value `slices_count` (by default 4)
- Adding event key `EV_KEY` with press and release events (e.g. key volume up, down and mute: `EV_KEY.KEY_VOLUMEUP, EV_KEY.KEY_VOLUMEDOWN, EV_KEY.KEY_MUTE`)
- Adding arrays of single-event `EV_REL` with values (e.g. scrolling: `EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES` with values: `-1, -120`)
- Possibility to trigger both types `EV_REL` and `EV_KEY` on release or immediately
- Possibility to require co-activator keys (`EV_KEY.KEY_LEFTSHIFT`) for both types `EV_KEY` and `EV_REL` which makes possible to distinguish between multiple functions for each app in single function mode
- Possibility to temporary force using not app specific shortcut only without removing app specific shortcuts from layout (`config_supress_app_specifics_shortcuts`)
- Disabling the Touchpad (e.g. Fn+special key) disables by default the DialPad as well (can be disabled)

## Data collecting

- Driver during installation collects anonymously data with goal improve the driver (e.g. automatic layout detection; data are publicly available [here](https://lookerstudio.google.com/reporting/a9ed8ed9-a0d7-42bd-96e9-57daed8697b1), you can provide used config using `$ bash install_config_send_anonymous_report.sh`)

## Installation

Get the latest dev version using `git`:

```bash
$ git clone https://github.com/asus-linux-drivers/asus-dialpad-driver
$ cd asus-dialpad-driver
# now you are using master branch with the latest changes which may be not stable
# jump to the latest release of stable version:
$ git checkout v2.0.0
```

or customized install:

```
# ENV VARS (with the defaults)
INSTALL_DIR_PATH="/usr/share/asus-dialpad-driver"
LOGS_DIR_PATH="/var/log/asus-dialpad-driver" # only for install and uninstall logs
INSTALL_UDEV_DIR_PATH="/usr/lib/udev"

# e.g. for BazziteOS (https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/198)
$ INSTALL_DIR_PATH="/home/$USER/.local/share/asus-dialpad-driver"\
INSTALL_UDEV_DIR_PATH="/etc/udev"\
bash install.sh
```

or run separately parts of the install script.

Try found Touchpad with DialPad:

```bash
$ bash install_device_check.sh
```

Add a user to the groups `i2c,input,uinput`:

```bash
$ bash install_user_groups.sh
```

Run driver now and every time that user logs in (do NOT run as `$ sudo`, works via `systemctl --user`):

```bash
$ bash install_service.sh
```

or for NixOS you can use flakes for the installation of this driver.

> [!IMPORTANT]
> In case the layout isn't provided, the "proartp16" DialPad layout is used.

> The default value for runtimeDir is `/run/usr/1000/`, for waylandDisplay is `wayland-0` and wayland is `true`.

> Enabling `ignoreWaylandDisplayEnv` removes the explicit declaration of `WAYLAND_DISPLAY` in the service, allowing it to function correctly when switching between desktop environments or window managers that may have different `WAYLAND_DISPLAY` environment variables.

<details>
<summary>The driver installation (NixOS)</summary>
<br>

This repo contains a Flake that exposes a NixOS Module that manages and offers options for asus-dialpad-driver. To use it, add the flake as an input to your `flake.nix` file and enable the module:

```nix
# flake.nix

{

    inputs = {
        # ---Snip---
        asus-dialpad-driver = {
          url = "github:asus-linux-drivers/asus-dialpad-driver";
          inputs.nixpkgs.follows = "nixpkgs";
        };
        # ---Snip---
    }

    outputs = {nixpkgs, asus-dialpad-driver, ...} @ inputs: {
        nixosConfigurations.HOSTNAME = nixpkgs.lib.nixosSystem {
            specialArgs = { inherit inputs; };
            modules = [
                ./configuration.nix
                asus-dialpad-driver.nixosModules.default
            ];
        };
    }
}
```
Then you can enable the program in your `configuration.nix` file:
```nix
# configuration.nix

{inputs, pkgs, ...}: {
  # ---Snip---
  # Enable Asus DialPad Service
  services.asus-dialpad-driver = {
    enable = true;
    layout = "default";
    wayland = true;
    ignoreWaylandDisplayEnv = false;
    runtimeDir = "/run/user/1000/";
    waylandDisplay = "wayland-0";
  };
  # ---Snip---
}

```

> The key for the caclulator toggling script should be associated with XF86Calculator, allowing it to toggle any calculator application, not just the one specified in the configuration. This means that the key binding can be used to manage various calculator applications across different key binding configurations. For e.g.:

```
"XF86Calculator".action = sh -c "if pidof gnome-calculator > /dev/null; then kill $(pidof gnome-calculator); else gnome-calculator; fi";
```

</details>

## Uninstallation

To uninstall run

```bash
$ bash uninstall.sh

# ENV VARS (with the defaults)
INSTALL_DIR_PATH="/usr/share/asus-dialpad-driver"
CONFIG_FILE_DIR_PATH="$INSTALL_DIR_PATH"
CONFIG_FILE_NAME="dialpad_dev"
LOGS_DIR_PATH="/var/log/asus-dialpad-driver" # only for install and uninstall logs
SERVICE_INSTALL_DIR_PATH="$HOME/.config/systemd/user"
INSTALL_UDEV_DIR_PATH="/usr/lib/udev"

# e.g. for BazziteOS (https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/198)
$ INSTALL_DIR_PATH="/home/$USER/.local/share/asus-dialpad-driver"\
INSTALL_UDEV_DIR_PATH="/etc/udev/"\
bash uninstall.sh
```

or run separately parts of the uninstall script

```bash
$ bash uninstall_service.sh
$ bash uninstall_user_groups.sh
```

## Layouts

Layouts below are named by laptop models, but the name is not important. What is important is their visual appearance because they are repeated on multiple laptop models across series. The install script should recognize the correct one automatically for your laptop. If yours was not recognized, please create issue.

| Name | Description                                                                                                  | Image                                                                                               |
| ------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| <a id="asusvivobook16x"></a><br><br><br><br><br>asusvivobook16x<br><br><br><br><br><br><br> | not nested                                                                | ![](images/Asus-Vivobook-16-x.png)                                             |
| <a id="proartp16"></a><br><br><br><br><br>proartp16<br><br><br><br><br><br><br> | nested                                                               | ![](images/Asus-ProArt-P16.png)   |
| <a id="zenbookpro14"></a><br><br><br><br><br>zenbookpro14<br><br><br><br><br><br><br> | nested                                                               | ![](images/Asus-Zenbook-Pro-14.jpg)   |


### FAQ ###

**How to start DialPad without systemd service?**

- install in standard way using `bash install.sh` and answer no to the question about using `systemd`
- layout name is required as first argument and as second argument can be optionally passed path to directory where will be autocreated config `dialpad_dev` (default is current working directory):

```
/usr/share/asus-dialpad-driver/.env/bin/python3 /usr/share/asus-dialpad-driver/dialpad.py <asusvivobook16x|proartp16|..>
```

**How to install the driver when is used pyenv for managing multiple Python versions?**

```
$ git clone https://github.com/asus-linux-drivers/asus-dialpad-driver
$ cd asus-dialpad-driver

$ # pyenv install Ubuntu 22.04
$ apt install -y make build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev libncursesw5-dev xz-utils tk-dev libffi-dev liblzma-dev python3-openssl git
$ curl https://pyenv.run | bash

# install & change to the Python version for which one do you want to install the driver
$ CC=clang pyenv install 3.9.4
$ pyenv global 3.9.4 # change as global
$ # pyenv local 3.9.4 # will create file .python-version inside source dir so next (re)install will be used automatically saved Python version in this file

# install the driver
$ bash install.sh

# change to the standardly (previously) used Python version
$ pyenv global system
```

**How can DialPad be activated via CLI?**

- directly just change `enabled` in the appropriate lines of the config file:

```
# enabling DialPad via command line
sed -i "s/enabled = 0/enabled = 1/g" dialpad_dev
sed -i "s/enabled = 0/enabled = 1/g" /usr/share/asus-dialpad-driver/dialpad_dev
# disabling
sed -i "s/enabled = 1/enabled = 0/g" dialpad_dev
sed -i "s/enabled = 1/enabled = 0/g" /usr/share/asus-dialpad-driver/dialpad_dev
```

## Configuration

### Keyboard layout

During the install process `bash ./install.sh`, you're required to select your keyboard layout:

```
...
1) asusvivobook16x.py
2) proartp16.py
3) zenbookpro14.py
4) Quit
Please enter your choice
...
```

| Option                                        | Required | Default           | Description |
| --------------------------------------------- | -------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Position of DialPad**                                |          |
| `circle_diameter`                                        | Required |                   | in px
| `center_button_diameter`                                        | Required |                   | in px
| `circle_center_x`                                        | Required |                   | in px
| `circle_center_y`                                        | Required |                   | in px
| **Associated apps**                                |          |          |
| `app_shortcuts`                                        | Optional | Yes                  | | required format like in `default` layout

### Co-activator keys

To prevent accidental DialPad activation while using the touchpad, you can configure a co-activator key. This requires holding a modifier key like `Alt` while touching the top right icon to activate the DialPad.

During installation, you will be prompted to select a co-activator key:

```
Select co-activator key:
1) None
2) Shift
3) Control
4) Alt
6) Quit
```

The co-activator is configured by modifying the `top_right_icon_coactivator_key` in your config file. When a co-activator is set, the `top_right_icon_coactivator_key` value becomes:

```
...
top_right_icon_coactivator_key = Alt
```

### Configuration file

Attributes which do not depend on a specific DialPad keyboard can be changed according to the table below in the config `dialpad_dev` in the installed driver location `/usr/share/asus-dialpad-driver`. See the example below showing the default attibutes:

```
[main]
disable_due_inactivity_time = 0
touchpad_disables_dialpad = 1
activation_time = 1
enabled = 0
socket_enabled = 1
top_right_icon_coactivator_key = Alt
```

| Option                                        | Required | Default           | Description |
| --------------------------------------------- | -------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **System**                                    |          |                   |
| `enabled`                                     |          | `0`               | DialPad running status (enabled/disabled)
| `socket_enabled`                                     |          | `0`               | DialPad is sending to the socket what is the user doing (enabled/disabled)
| `disable_due_inactivity_time`                 |          | `0` [s]            | DialPad is automatically disabled when no event received for this interval<br><br>decimal numbers allowed (e.g. `60.0` [s] is one minute, `0` set up by default disables this functionality)
| `touchpad_disables_dialpad`                    |          | `1`            | when Touchpad is disabled DialPad is disabled aswell
| **Layout**                                |          |
| `slices_count`              |          | `1.0` [seconds]             | number of slices in the circle considered as steps when moving with finger around
| `suppress_app_specifics_shortcuts`              |          | `0`             | app specific shortcuts are ignored when is specific window with app opened
| **Top right icon**                            |          |                   |
| `activation_time`              |          | `1.0` [seconds]             | amount of time you have to hold `top_right_icon`
| `top_right_icon_coactivator_key`                     |          | ``            | empty default means no co-activator keys are required (valid values are `Shift`, `Control` or `Alt` delimeted by space)<br><br>this works only for activation by touching the top right icon

## Debugging

Listening on socket using `socat` (e.g. `$ sudo apt install socat`):

**Is necessary to have enabled in config `socket_enabled`.**

```
$ socat - UNIX-RECV:/tmp/dialpad.sock
{"ts": 1767791846.2734825, "enabled": true}
{"ts": 1767791846.3734825, "input": "center", "value": 1}
{"ts": 1767791846.3737416, "input": "center", "value": 0}
{"ts": 1767791846.461517, "input": "counterclockwise", "value": "62", "title": "Volume"}
{"ts": 1767791846.6073422, "input": "counterclockwise", "value": "58", "title": "Volume"}
{"ts": 1767791846.698195, "input": "counterclockwise", "value": "56", "title": "Volume"}
{"ts": 1767791846.814567, "input": "center", "value": 1}
{"ts": 1767791846.8146207, "input": "center", "value": 0}
{"ts": 1767791846.8976963, "input": "counterclockwise", "value": "54", "title": "Volume"}
{"ts": 1767791846.9604836, "input": "counterclockwise", "value": "52", "title": "Volume"}
{"ts": 1767791847.161212, "input": "counterclockwise", "value": "50", "title": "Volume"}
{"ts": 1767791847.3800304, "input": "clockwise", "value": "52", "title": "Volume"}
{"ts": 1767791847.5570402, "input": "clockwise", "value": "54", "title": "Volume"}
{"ts": 1767791847.6351395, "input": "center", "value": 1}
{"ts": 1767791847.6357095, "input": "center", "value": 0}
{"ts": 1767791847.7126362, "input": "clockwise", "value": "56", "title": "Volume"}
{"ts": 1767791847.789433, "input": "clockwise", "value": "58", "title": "Volume"}
{"ts": 1767791846.2734825, "enabled": false}
```

```
$ source /usr/share/asus-dialpad-driver/.env/bin/activate
(.env) $ python3 dialpad_ui.py
Listening on /tmp/dialpad.sock
```

Systemd service logs:

```
$ journalctl --user -u asus_dialpad_driver@$USER.service
```

## Similar existing

- I do not know any

## Existing related projects

- [c] Set of tools for handling ASUS Dial and simmilar designware hardware under Linux (https://github.com/fredaime/openwheel)

**Why was this project created?** Because linux does not support integration of DialPad into a Touchpad

**Stargazer evolution for the project**

[![Stargazers over time](https://starchart.cc/asus-linux-drivers/asus-dialpad-driver.svg)](https://starchart.cc/asus-linux-drivers/asus-dialpad-driver)

**Buy me a coffee**

Do you think my effort put into open source is useful for you / others? Please put a star in the GitHub repository. Every star makes me proud. Any contribution is also welcome. Would you like to reward me more? There now exists a way : you can invite me for a coffee! I would really appreciate that!

For this [ko-fi.com/ldrahnik](https://ko-fi.com/ldrahnik) is preferred instead of [buymeacoffee.com/ldrahnik](https://buymeacoffee.com/ldrahnik) because of zero commissions.

[![Ko-fi supporters](images/kofi.png)](https://ko-fi.com/ldrahnik)

[![Buy me a coffee supporters](images/buymeacoffee.png)](https://buymeacoffee.com/ldrahnik)