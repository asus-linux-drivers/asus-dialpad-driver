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
            "$DIALPAD_SOURCE_DIR/dialpad_overlay.py" \
            "$DIALPAD_SOURCE_DIR/dialpad_help.py" \
            "$DIALPAD_SOURCE_DIR/dialpad_i18n.py" "$INSTALL_DIR_PATH/" || exit 1
        install -d -- "$INSTALL_DIR_PATH/locales" || exit 1
        install -m 644 -- "$DIALPAD_SOURCE_DIR"/locales/{en_US,zh_CN,zh_TW}.json "$INSTALL_DIR_PATH/locales/" || exit 1
        # Retire only the manager-owned source-text catalogs from older installs.
        rm -f -- "$INSTALL_DIR_PATH/locales/en.json" || exit 1
        for section in common help editor manager; do
            rm -f -- "$INSTALL_DIR_PATH/locales/$section.zh_CN.json" \
                "$INSTALL_DIR_PATH/locales/$section.zh_TW.json" || exit 1
        done
        dialpad_install_launchers 1 || exit 1
        echo "Installed: $DIALPAD_BIN_DIR_PATH/asus-dialpad-layout-manager"
        echo "The manager can edit layouts offline; it does not start or enable a service."
        ;;
esac
