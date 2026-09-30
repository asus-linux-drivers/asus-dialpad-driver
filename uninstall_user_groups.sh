#!/usr/bin/env bash

source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"

echo "$INSTALL_UDEV_DIR_PATH"

sudo rm -f -- "$INSTALL_UDEV_DIR_PATH/rules.d/99-asus-dialpad-driver-uinput.rules"

if [[ $? != 0 ]]; then
    echo "Something went wrong when removing the uinput udev rule"
fi

sudo rm -f -- "$INSTALL_UDEV_DIR_PATH/rules.d/99-asus-dialpad-driver-i2c-dev.rules"
if [[ $? != 0 ]]; then
    echo "Something went wrong when removing the i2c-dev udev rule"
fi

# Module autoload files live in the fixed systemd directory by default; the path is
# overridable like INSTALL_UDEV_DIR_PATH, and uninstall.sh detects artifacts there.
MODULES_LOAD_DIR_PATH=${MODULES_LOAD_DIR_PATH:-/etc/modules-load.d}

sudo rm -f "$MODULES_LOAD_DIR_PATH/uinput-asus-dialpad-driver.conf" \
    "$MODULES_LOAD_DIR_PATH/i2c-dev-asus-dialpad-driver.conf"
if [[ $? != 0 ]]; then
    echo "Something went wrong when removing the uinput conf"
fi

sudo rm -f $INSTALL_UDEV_DIR_PATH/rules.d/70-asus-numberpad-driver-hidraw.rules
if [[ $? != 0 ]]; then
    echo "Something went wrong when removing the hidraw udev rule"
fi

sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput && sudo udevadm trigger --subsystem-match=hidraw
if [[ $? != 0 ]]; then
    echo "Something went wrong when reloading or triggering uinput udev rules"
else
    echo "Udev rules reloaded and triggered"
fi