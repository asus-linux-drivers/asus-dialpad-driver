#!/usr/bin/env bash

source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
SERVICE_INSTALL_DIR_PATH=${SERVICE_INSTALL_DIR_PATH:-$HOME/.config/systemd/user}

# Editor-only or non-systemd installations must not touch unrelated running services.
REMOVED_DIALPAD_SERVICE=0
for SERVICE_NAME in asus_dialpad_driver_ui asus_dialpad_driver; do
    SERVICE_INSTALL_FILE_PATH="$SERVICE_INSTALL_DIR_PATH/$SERVICE_NAME@.service"
    [[ -f "$SERVICE_INSTALL_FILE_PATH" ]] || continue
    SERVICE_INSTANCE_FILE_NAME="$SERVICE_NAME@$USER.service"
    systemctl --user disable --now "$SERVICE_INSTANCE_FILE_NAME" || exit 1
    rm -f -- "$SERVICE_INSTALL_FILE_PATH" || exit 1
    REMOVED_DIALPAD_SERVICE=1
    echo "Removed service: $SERVICE_INSTANCE_FILE_NAME"
done
if [[ "$REMOVED_DIALPAD_SERVICE" == 1 ]]; then
    systemctl --user daemon-reload || exit 1
fi
