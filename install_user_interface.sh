#!/usr/bin/env bash

source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/install_common.sh"
dialpad_init_paths || exit 1

echo
echo "DialPad Floating User Interface Installation"
echo "The feedback overlay is independent of the layout manager."
echo

USER_INTERFACE=0
CURRENT_USER_INTERFACE=$(dialpad_config_cli config-get socket_enabled) || exit 1
if [[ "$CURRENT_USER_INTERFACE" == 1 ]]; then
    read -r -p "Keep the floating interface installed and enabled? [Y/n] " RESPONSE
    case "$RESPONSE" in
        [nN]|[nN][oO]) ;;
        *) USER_INTERFACE=1 ;;
    esac
else
    read -r -p "Do you want to install and enable the floating interface? [y/N] " RESPONSE
    case "$RESPONSE" in
        [yY][eE][sS]|[yY]) USER_INTERFACE=1 ;;
    esac
fi

if [[ "$USER_INTERFACE" == 1 ]]; then
    dialpad_install_qt_dependencies || exit 1
    install -m 644 -- "$DIALPAD_SOURCE_DIR/dialpad_ui.py" \
        "$DIALPAD_SOURCE_DIR/dialpad_overlay.py" "$INSTALL_DIR_PATH/" || exit 1
    dialpad_config_cli config-set socket_enabled 1 || exit 1
elif [[ -n "$CURRENT_USER_INTERFACE" ]]; then
    # This is an explicit overlay choice, never a side effect of manager installation.
    dialpad_config_cli config-set socket_enabled 0 || exit 1
fi
