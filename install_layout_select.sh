#!/usr/bin/env bash

source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/install_common.sh"
dialpad_init_paths || exit 1

dialpad_select_layout() {
    local configured available option response
    configured=$(dialpad_config_cli config-get layout) || return
    if [[ -z "$LAYOUT_NAME" && -n "$configured" ]]; then
        echo "Configured layout: $configured"
        read -r -p "Keep this explicit selection (including manager changes)? [Y/n] " response
        case "$response" in
            [nN]|[nN][oO]) ;;
            *)
                LAYOUT_NAME=$configured
                echo "Preserving the configured layout; automatic suggestions will not replace it."
                return 0
                ;;
        esac
    fi

    available=$(dialpad_config_cli list --identifiers) || return
    if [[ -z "$available" ]]; then
        echo "No supported JSON or Python layout files were found." >&2
        return 1
    fi
    local -a layouts options
    mapfile -t layouts <<< "$available"
    if [[ -z "$LAYOUT_NAME" && -z "$configured" ]]; then
        source "$DIALPAD_SOURCE_DIR/install_layout_auto_suggestion.sh"
    fi
    if [[ -z "$LAYOUT_NAME" ]]; then
        echo
        echo "DialPad layout (user layouts take precedence over bundled layouts)"
        echo "Photos: https://github.com/asus-linux-drivers/asus-dialpad-driver#layouts"
        options=("${layouts[@]}" "Quit")
        local PS3="Please enter your choice: "
        select option in "${options[@]}"; do
            case "$option" in
                Quit) exit 0 ;;
                '') echo "Invalid option $REPLY" ;;
                *) LAYOUT_NAME=$option; break ;;
            esac
        done
    fi

    local found=0
    for option in "${layouts[@]}"; do
        if [[ "$LAYOUT_NAME" == "$option" ]]; then
            found=1
            break
        fi
    done
    if [[ "$found" != 1 ]]; then
        echo "Layout '$LAYOUT_NAME' is not an installed JSON or Python layout." >&2
        return 1
    fi
    echo "Requesting layout '$LAYOUT_NAME' in $CONFIG_FILE_PATH."
    echo "This explicit selection overrides the service's positional layout default."
    # activate validates without executing Python or commands, then patches only layout.
    dialpad_config_cli activate "$LAYOUT_NAME" || return
    if [[ -n "$DETECTED_LAYOUT_VIA_OFFLINE_TABLE" && "$DETECTED_LAYOUT_VIA_OFFLINE_TABLE" != "$LAYOUT_NAME" ]]; then
        LAYOUT_AUTO_SUGGESTED_DIFFER_FROM_USED=1
    fi
    echo "Selected layout: $LAYOUT_NAME (request saved; use asus-dialpad-layout status to confirm application)."
}

dialpad_select_layout || exit 1
