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
echo "DialPad User Interface Installation"
echo
echo "You can choose whether to install the DialPad User Interface."
echo "This interface allows you to see what you do on DialPad visually."
echo

USER_INTERFACE=0

read -r -p "Do you want to install the DialPad User Interface? [y/N]" RESPONSE
case "$RESPONSE" in [yY][eE][sS]|[yY])

    pip3 install -r requirements.ui.txt

    USER_INTERFACE=1

    sudo install dialpad_ui.py "$INSTALL_DIR_PATH"
    sudo chown -R $USER "$INSTALL_DIR_PATH"

    echo

    echo "Enabling DialPad User Interface in configuration..."

    if [ ! -f "$CONFIG_FILE_PATH" ]; then
        echo "[main]" | sudo tee "$CONFIG_FILE_PATH" > /dev/null
    fi

    # check if the setting already exists
    if grep -q "socket_enabled" "$CONFIG_FILE_PATH"; then
        sudo sed -i "s/socket_enabled.*/socket_enabled = 1/" "$CONFIG_FILE_PATH"
    else
        # add new setting under [main] section
        sudo sed -i "/\[main\]/a socket_enabled = 1" "$CONFIG_FILE_PATH"
    fi
esac