#!/usr/bin/env bash

# Also runnable by itself: no hardware setup, overlay or systemd service required.
source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/install_common.sh"
dialpad_init_paths || exit 1

echo
echo "DialPad Layout Manager Installation"
echo "The editor is independent of the floating interface and driver service."
echo "Configuration directory: $CONFIG_FILE_DIR_PATH"
echo

read -r -p "Do you want to install the DialPad Layout Manager? [y/N] " RESPONSE
case "$RESPONSE" in
    [yY][eE][sS]|[yY])
        dialpad_install_backend || exit 1
        dialpad_install_qt_dependencies || exit 1
        install -m 644 -- "$DIALPAD_SOURCE_DIR/dialpad_layout_manager.py" \
            "$DIALPAD_SOURCE_DIR/dialpad_layout_editor.py" \
            "$DIALPAD_SOURCE_DIR/dialpad_overlay.py" "$INSTALL_DIR_PATH/" || exit 1
        dialpad_install_launchers 1 || exit 1
        echo "Installed: $DIALPAD_BIN_DIR_PATH/asus-dialpad-layout-manager"
        echo "The manager can edit layouts offline; it does not start or enable a service."
        ;;
esac
