# Changelog

## 1.2.0 (16.12.2025)

### Fixed

- The package `smbus2` was replaced by `python-periphery` because has missing support for `python3.14` (yet) and `i2ctransfer` was added as alternative for `i2c` communication
- Nix `system` has been changed to `stdenv.hostPlatform.system` (credits @SamueleFacenda)
- Sending driver's version to GA
- `uinput`, `i2c`, `input` changed to a system groups (@vitaminace33)
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