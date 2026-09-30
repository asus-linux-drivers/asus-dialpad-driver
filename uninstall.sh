#!/usr/bin/env bash

set -o pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/install_common.sh"
dialpad_init_paths || exit 1
INSTALL_UDEV_DIR_PATH=${INSTALL_UDEV_DIR_PATH:-/usr/lib/udev}
MODULES_LOAD_DIR_PATH=${MODULES_LOAD_DIR_PATH:-/etc/modules-load.d}
SERVICE_INSTALL_DIR_PATH=${SERVICE_INSTALL_DIR_PATH:-$HOME/.config/systemd/user}

# Uninstallation must not provision groups or packages, especially for editor-only installs.
if [[ -z "$LOGS_DIR_PATH" ]]; then
    if [[ -d /var/log/asus-dialpad-driver && -w /var/log/asus-dialpad-driver ]]; then
        LOGS_DIR_PATH=/var/log/asus-dialpad-driver
    else
        LOGS_DIR_PATH="${XDG_STATE_HOME:-$HOME/.local/state}/asus-dialpad-driver"
    fi
fi
mkdir -p -- "$LOGS_DIR_PATH" || exit 1
LOGS_UNINSTALL_LOG_FILE_PATH="$LOGS_DIR_PATH/uninstall-$(date +%d-%m-%Y-%H-%M-%S).log"

{
    # A damaged or partial installation can lose dialpad.py while still owning
    # service templates and privileged rules, so cleanup is driven by the exact
    # artifact names instead. Editor-only installations and unrelated service
    # installs own none of them, so their files stay untouched.
    DRIVER_WAS_INSTALLED=0
    if [[ -f "$INSTALL_DIR_PATH/dialpad.py" ]]; then
        DRIVER_WAS_INSTALLED=1
    fi

    DIALPAD_SERVICE_WAS_INSTALLED=0
    for SERVICE_NAME in asus_dialpad_driver_ui asus_dialpad_driver; do
        if [[ -f "$SERVICE_INSTALL_DIR_PATH/$SERVICE_NAME@.service" ]]; then
            DIALPAD_SERVICE_WAS_INSTALLED=1
            break
        fi
    done

    DIALPAD_RULES_WERE_INSTALLED=0
    for RULES_FILE_PATH in \
        "$INSTALL_UDEV_DIR_PATH/rules.d/99-asus-dialpad-driver-uinput.rules" \
        "$INSTALL_UDEV_DIR_PATH/rules.d/99-asus-dialpad-driver-i2c-dev.rules" \
        "$MODULES_LOAD_DIR_PATH/uinput-asus-dialpad-driver.conf" \
        "$MODULES_LOAD_DIR_PATH/i2c-dev-asus-dialpad-driver.conf"; do
        if [[ -f "$RULES_FILE_PATH" ]]; then
            DIALPAD_RULES_WERE_INSTALLED=1
            break
        fi
    done

    if [[ "$DIALPAD_SERVICE_WAS_INSTALLED" == 1 ]]; then
        source "$DIALPAD_SOURCE_DIR/uninstall_service.sh"
    fi
    if [[ "$DIALPAD_RULES_WERE_INSTALLED" == 1 ]]; then
        source "$DIALPAD_SOURCE_DIR/uninstall_user_groups.sh"
    fi

    echo "Removing application files and launchers from: $INSTALL_DIR_PATH"
    echo "Configuration directory: $CONFIG_FILE_DIR_PATH"
    echo "Configuration, layouts (including modified bundled layouts) and recovery are preserved by default."
    echo
    echo "Purge permanently deletes dialpad_dev, layouts/ and .layout-state/ in the configuration directory"
    echo "and any retained layouts/recovery in the installation directory. Other files are not removed."
    read -r -p "Type PURGE to delete those user data, or press Enter to preserve them: " RESPONSE
    PURGE_USER_DATA=0
    [[ "$RESPONSE" == PURGE ]] && PURGE_USER_DATA=1

    dialpad_remove_launchers || exit 1
    dialpad_remove_program_files "$PURGE_USER_DATA" || exit 1
    echo "Uninstallation finished successfully."

    if [[ "$DRIVER_WAS_INSTALLED" == 1 ]]; then
        echo
        read -r -p "A reboot may be required for kernel/group changes. Reboot now? [y/N] " RESPONSE
        case "$RESPONSE" in
            [yY][eE][sS]|[yY]) sudo /sbin/reboot ;;
        esac
    fi
} 2>&1 | tee "$LOGS_UNINSTALL_LOG_FILE_PATH"
