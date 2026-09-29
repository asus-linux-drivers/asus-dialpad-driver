#!/usr/bin/env bash

source "$(dirname -- "${BASH_SOURCE[0]}")/non_sudo_check.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/install_common.sh"
dialpad_init_paths || exit 1

echo
echo "Co-activator key for DialPad activation"
echo
echo "A co-activator key requires you to hold a modifier key while touching"
echo "the top right icon to activate the DialPad. This helps"
echo "prevent accidental activation during touchpad use."
echo
echo "Select co-activator key:"
echo

if [ -z "$COACTIVATOR_KEY" ]; then
    PS3="Please enter your choice: "
    OPTIONS=("Keep current" "None" "Shift" "Control" "Alt" "Quit")
    select SELECTED_OPT in "${OPTIONS[@]}"; do
        case "$SELECTED_OPT" in
            "Keep current")
                COACTIVATOR_KEY="Keep current"
                break
                ;;
            "Quit")
                exit 0
                ;;
            "None"|"Shift"|"Control"|"Alt")
                COACTIVATOR_KEY="$SELECTED_OPT"
                break
                ;;
            *)
                echo "Invalid option $REPLY"
                ;;
        esac
    done
fi

echo
echo "Selected co-activator key: $COACTIVATOR_KEY"

if [[ "$COACTIVATOR_KEY" != "Keep current" ]]; then
    case "$COACTIVATOR_KEY" in
        None) COACTIVATOR_KEY="" ;;
        Shift|Control|Alt) ;;
        *) echo "Unsupported co-activator key: $COACTIVATOR_KEY" >&2; exit 1 ;;
    esac
    echo "Applying the explicit co-activator choice to configuration..."
    dialpad_config_cli config-set top_right_icon_coactivator_key "$COACTIVATOR_KEY" || exit 1
fi
