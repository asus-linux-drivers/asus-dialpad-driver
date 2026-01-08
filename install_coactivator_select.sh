#!/usr/bin/env bash

source non_sudo_check.sh

# ENV VARS
if [ -z "$INSTALL_DIR_PATH" ]; then
    INSTALL_DIR_PATH="/usr/share/asus-dialpad-driver"
fi
if [ -z "$CONFIG_FILE_DIR_PATH" ]; then
    CONFIG_FILE_DIR_PATH="$INSTALL_DIR_PATH"
fi
if [ -z "$CONFIG_FILE_NAME" ]; then
    CONFIG_FILE_NAME="dialpad_dev"
fi
if [ -z "$CONFIG_FILE_PATH" ]; then
    CONFIG_FILE_PATH="$CONFIG_FILE_DIR_PATH/$CONFIG_FILE_NAME"
fi

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
    OPTIONS=("None" "Shift" "Control" "Alt" "Quit")
    select SELECTED_OPT in "${OPTIONS[@]}"; do
        case "$SELECTED_OPT" in
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

if [ "$COACTIVATOR_KEY" != "None" ]; then

    echo "Applying co-activator key ($COACTIVATOR_KEY) to config file..."

    if [ ! -f "$CONFIG_FILE_PATH" ]; then
        echo "[main]" | tee "$CONFIG_FILE_PATH" > /dev/null
    fi

    # check if the setting already exists
    if grep -q "top_right_icon_coactivator_key" "$CONFIG_FILE_PATH"; then
        sed -i "s/top_right_icon_coactivator_key.*/top_right_icon_coactivator_key = $COACTIVATOR_KEY/" "$CONFIG_FILE_PATH"
    else
        # add new setting under [main] section
        sed -i "/\[main\]/a top_right_icon_coactivator_key = $COACTIVATOR_KEY" "$CONFIG_FILE_PATH"
    fi
fi
