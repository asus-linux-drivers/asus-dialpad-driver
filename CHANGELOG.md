# Changelog

## 2.5.2 (30.8.2026)

### Fixed

- Fedora Qt6 `qdbus` detection (credits @pesoiq)
- `AltGr` on X11 (e.g. czech keyboard layout use for asterisk `AltGr + KEY_8`)
- Logging exception for main loop (credits @bub4z0r)
- Do not specify during importing `ImportError` (e.g. `AttributeError` can occurs)
- When is `QDBUS` not available then do not even try to send it
- Freeze of `PyGObject<3.52` only when exists `girepository-2.0`
- Active layout (credits @bub4z0r)

## 2.5.1 (10.8.2026)

### Fixed

- Fix Wayland startup crash before keyboard state initialization (credits @valruin)

## 2.5.0 (8.8.2026)

### Fixed

- More preciously freeze of `PyGObject<3.52` (only when exist girepository-2.0)
- Changed permissions of config file via used `sudo`
- Missing checking config values handler for changes (credits @Robin-Everaars)
- Duplication of logs (output/error) of `systemd` service for `NixOS` (credits @Robin-Everaars)
- Missing filtering out everything except `EV_KEY` events when listening for active modifiers
- Usage of `i2ctransfer`, was not a part of path inside `systemd` service on `NixOS` (credits @Robin-Everaars)
- Using of co-activation modifier Alt on `X11`

### Feature

- Added udev option `static_node` for `uinput` which is applied immediately and not after reboot (and action remove and tag uaccess for i2c)(credits @Robin-Everaars)
- Install and load `x11` dependencies only when are used (credits @Robin-Everaars)

## 2.4.0 (28.7.2026)

### Fixed

- Version of `xkbcommon` when is version of `libxkbcommon` not a lower then 1.5
- Changed the order of `i2ctransfer` and `periphery.I2C` because periphery is raising buffer overflow (credits @noguerol)
- Getting current layout on newer gnome when sources do not contain most recently used layout (credits @noguerol)
- When import of `AsyncNotifier` is no longer possible (replaced by `ThreadedNotifier`) (credits @noguerol)
- Detection of event when is the name not known but code is valid (credits @md2z34)
- Situation on wayland when is not found e.g. `Alt` (because can be found under `Mod1`) (credits @SvenSvenson38)
- Unmet dependencies for `python3-pyatspi` by using `--system-site-packages` inside `virtualenv`

### Feature

- Added to the install process security check with info about [CVE-2026-50292](https://github.com/advisories/GHSA-jcq8-v68h-2c44) (credits @GLLM)

## 2.3.0 (22.6.2026)

### Fixed

- Missing required dependency for `rpm-ostree` + added `allow inactive` + require reboot only when is needed (reported on `Fedora Silverblue`)
- Missing symlink for `wayland` and `xkbcommon` headers to `/usr/include` (reported on `OpenSUSE`)
- Default log level `DEBUG` decreased to `INFO`
- Reworked NixOS support (credits @toastal)
- Detection because missing `line`
- Name of `uinput` device variable
- Added missing `sudo` when removing `__pycache__`
- Re-loading `coactivator` keys on `Wayland` when was config not loaded yet and added re-loading on X11 completely

### Feature

- Added possibility to run `$ LOG=DEBUG bash install.sh`
- Added elimination of Stylus `9008` (fullname `ELAN9008:00 04F3:4631 Stylus`)
- Added DSDT probing script

## 2.2.1 (27.4.2026)

### Fixed

- Device detection during install process

## 2.2.0 (15.4.2026)

### Fixed

- Adding `uinput` group on BazziteOS
- Deprecated `xorg.xinput` to `xinput` for the next stable release of NixOS (credits @jalbstmeijer @MeeSumee)
- Default layout name and missing `INSTALL_DIR_PATH` when using `install_service.sh `independently without overriding from cmd
- Missing `SUDO_USER` on `qdbusSet` function
- Missing anti-repeating failure mechanism for `QDBUS`
- UI window from stealing focus on Wayland (credits @junjzhang)
- Duplication of `pywayland` dependency for NixOS

### Feature

- Added support for Wayland/X11 on Gnome using `pyatspi`
- Added support for wlroots-based Wayland compositors niri, sway and Hyprland (credits @junjzhang)
- Added support for immutable systems BazziteOS, Fedora Silverblue and Kinoite (credits @VictorPrado99)

## 2.1.1 (11.2.2026)

### Fixed

- Support for NixOS (credits @SamueleFacenda)
- Package names for `eopkg` / `yum` (credits @sHAKaJaada)

## 2.1.0 (28.1.2026)

### Fixed

- Previously added `i2c` group as not a system by recreating
- Missing dependency for NixOS `xcffib`
- Removed wayland dependency from the `requirements.txt` because existence of `requirements.wayland.txt`

### Feature

- Init `treshold` and possibility to see (only) progress in UI up to `treshold` using `socket_send_progress_above_treshold`
- Icons may be associated to `value` returned by command as alternation for sending keys

## 2.0.2 (16.1.2026)

### Fixed

- Added collecting of device_addresses (`0x38` or `0x15`) for kernel driver development purpose

## 2.0.1 (15.1.2026)

### Fixed

- Visual of pressing center button
- Shift of circle slices about 90 degree

## 2.0.0 (12.1.2026)

### Fixed

- Changed location of service to `$HOME/.config/systemd/user` (credits @s-badran)
- Fixed co-activator key selection for DialPad activation

### Feature

- Init of user interface including single and multi function mode

## 1.3.0 (3.1.2026)

### Fixed

- Detection of plasma environment (e.g. `plasmawayland` or `plasma-x11`)
- Missing support for `qdbus6`
- Plasma version detection using `kinfo`
- Running not under systemd service (when optional `systemd-python` pip package is not installed)
- When xauthority has in `tmp` folder multiple files
- Missing auto-installation of `qdbus` in supported distributions when using KDE Plasma

### Feature

- Co-activator key selection for DialPad activation
- By default DialPad automatically disable after 2 mins
- Support for `EV_REL` events with single event (opposite to release/press)
- Support for list of keys
- Example of scrolling

## 1.2.0 (16.12.2025)

### Fixed

- The package `smbus2` was replaced by `python-periphery` because has missing support for `python3.14` (yet) and `i2ctransfer` was added as alternative for `i2c` communication
- Nix `system` has been changed to `stdenv.hostPlatform.system` (credits @SamueleFacenda)
- Sending driver's version to GA
- `uinput`, `i2c`, `input` changed to a system groups (credits @vitaminace33)
- Setting up appropriate (not a static) `KERNEL` and `SUBSYSTEM` for `i2c` and `uinput` udev rules

### Feature

- Updated offline table for auto suggestions from gathered data (GA)

## 1.1.0 (8.10.2025)

### Fixed

- Missing python3 dependency when using `systemd`
- Installing `pip` package `pywayland` when is not required
- Layout `Asus Vivobook 16 x` dialpad coordinates (credits @cristianvasquez)

### Feature

- Added layout Zenbook Pro

## 1.0.0 (16.6.2025)

First release

## 0.0.1 (23.02.2025)

Init