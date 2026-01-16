# Changelog

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