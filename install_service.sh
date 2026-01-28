#!/usr/bin/env bash

source non_sudo_check.sh

# ENV VARS
if [ -z "$CONFIG_FILE_DIR_PATH" ]; then
    CONFIG_FILE_DIR_PATH="/usr/share/asus-dialpad-driver"
fi
if [ -z "$LAYOUT_NAME" ]; then
    LAYOUT_NAME="default"
fi
if [ -z "$LOGS_DIR_PATH" ]; then
    LOGS_DIR_PATH="/var/log/asus-dialpad-driver"
fi
if [ -z "$SERVICE_INSTALL_DIR_PATH" ]; then
    SERVICE_INSTALL_DIR_PATH="$HOME/.config/systemd/user"
fi

echo "Systemctl service(s)"
echo

read -r -p "Do you want install systemctl service(s)? [y/N]" RESPONSE
case "$RESPONSE" in [yY][eE][sS]|[yY])

    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get -y install libsystemd-dev python3-systemd
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman --noconfirm --needed -S systemd python-systemd
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf -y install systemd-devel python3-systemd
    elif command -v yum >/dev/null 2>&1; then
        sudo yum -y install systemd-devel python3-systemd
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper --non-interactive install systemd-devel python3-systemd
    elif command -v xbps-install >/dev/null 2>&1; then
        sudo xbps-install -Suy systemd python3-systemd
    elif command -v emerge >/dev/null 2>&1; then
        sudo emerge sys-apps/systemd dev-python/python-systemd
    elif command -v rpm-ostree >/dev/null 2>&1; then
        sudo rpm-ostree install systemd-devel python3-systemd
    elif command -v eopkg >/dev/null 2>&1; then
        sudo eopkg install -y systemd-devel python3-systemd
    else
        echo "Not detected package manager. Driver may not work properly because required packages have not been installed. Please create an issue (https://github.com/asus-linux-drivers/asus-dialpad-driver/issues)."
    fi

    pip3 install -r requirements.systemd.txt

    SERVICE=1

    SERVICE_WAYLAND_FILE_PATH=asus_dialpad_driver.wayland.service
    SERVICE_X11_FILE_PATH=asus_dialpad_driver.x11.service
    SERVICE_INSTALL_FILE_NAME="asus_dialpad_driver@.service"

    XDG_RUNTIME_DIR=$(echo $XDG_RUNTIME_DIR)
    DBUS_SESSION_BUS_ADDRESS=$(echo $DBUS_SESSION_BUS_ADDRESS)
    XAUTHORITY=$(echo $XAUTHORITY)
    DISPLAY=$(echo $DISPLAY)
    WAYLAND_DISPLAY=$(echo $WAYLAND_DISPLAY)
    XDG_SESSION_TYPE=$(echo $XDG_SESSION_TYPE)
    ERROR_LOG_FILE_PATH="$LOGS_DIR_PATH/error.log"

    echo
    echo "LAYOUT_NAME: $LAYOUT_NAME"
    echo "CONFIG_FILE_DIR_PATH: $CONFIG_FILE_DIR_PATH"
    echo
    echo "env var DISPLAY: $DISPLAY"
    echo "env var WAYLAND_DISPLAY: $WAYLAND_DISPLAY"
    echo "env var AUTHORITY: $XAUTHORITY"
    echo "env var XDG_RUNTIME_DIR: $XDG_RUNTIME_DIR"
    echo "env var DBUS_SESSION_BUS_ADDRESS: $DBUS_SESSION_BUS_ADDRESS"
    echo "env var XDG_SESSION_TYPE: $XDG_SESSION_TYPE"

    # with no gdm is env var XDG_SESSION_TYPE tty - https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/185
    if [ "$XDG_SESSION_TYPE" == "tty" ] || [ "$XDG_SESSION_TYPE" == "" ]; then

        echo
        echo "Env var XDG_SESSION_TYPE is: `$XDG_SESSION_TYPE`"
        echo
        echo "Please, select your display manager:"
        echo
        PS3="Please enter your choice "
        OPTIONS=("x11" "wayland" "Quit")
        select SELECTED_OPT in "${OPTIONS[@]}"; do
            if [ "$SELECTED_OPT" = "Quit" ]; then
                exit 0
            fi

            XDG_SESSION_TYPE=$SELECTED_OPT

            echo
            echo "(SET UP FOR DRIVER ONLY) env var XDG_SESSION_TYPE: $XDG_SESSION_TYPE"
            echo

            if [ -z "$LAYOUT_NAME" ]; then
                echo "invalid option $REPLY"
            else
                break
            fi
        done
    fi

    echo

    if [ "$XDG_SESSION_TYPE" == "x11" ]; then
        cat "$SERVICE_X11_FILE_PATH" | INSTALL_DIR_PATH=$INSTALL_DIR_PATH LAYOUT_NAME=$LAYOUT_NAME CONFIG_FILE_DIR_PATH="$CONFIG_FILE_DIR_PATH/" DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR XDG_SESSION_TYPE=$XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS envsubst '$INSTALL_DIR_PATH $LAYOUT_NAME $CONFIG_FILE_DIR_PATH $DISPLAY $XAUTHORITY $XDG_RUNTIME_DIR $XDG_SESSION_TYPE $DBUS_SESSION_BUS_ADDRESS' | tee "$SERVICE_INSTALL_DIR_PATH/$SERVICE_INSTALL_FILE_NAME" >/dev/null
    else
        echo "Unfortunatelly you will not be able use feature: Disabling Touchpad (e.g. Fn+special key) disables DialPad aswell, at this moment is supported only X11"
        # DISPLAY=$DISPLAY for Xwayland
        cat "$SERVICE_WAYLAND_FILE_PATH" | INSTALL_DIR_PATH=$INSTALL_DIR_PATH LAYOUT_NAME=$LAYOUT_NAME CONFIG_FILE_DIR_PATH="$CONFIG_FILE_DIR_PATH/" DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR XDG_SESSION_TYPE=$XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS envsubst '$INSTALL_DIR_PATH $LAYOUT_NAME $CONFIG_FILE_DIR_PATH $DISPLAY $WAYLAND_DISPLAY $XDG_RUNTIME_DIR $XDG_SESSION_TYPE $DBUS_SESSION_BUS_ADDRESS' | tee "$SERVICE_INSTALL_DIR_PATH/$SERVICE_INSTALL_FILE_NAME" >/dev/null
    fi

    if [[ $? != 0 ]]; then
        echo "Something went wrong when moving the asus_dialpad_driver.service"
        exit 1
    else
        echo "Asus DialPad Driver service placed"
    fi

    systemctl --user daemon-reload

    if [[ $? != 0 ]]; then
        echo "Something went wrong when was called systemctl daemon reload"
        exit 1
    else
        echo "Systemctl daemon reloaded"
    fi

    systemctl enable --user asus_dialpad_driver@$USER.service

    if [[ $? != 0 ]]; then
        echo "Something went wrong when enabling the asus_dialpad_driver.service"
        exit 1
    else
        echo "Asus DialPad driver service enabled"
    fi

    systemctl restart --user asus_dialpad_driver@$USER.service
    if [[ $? != 0 ]]; then
        echo "Something went wrong when starting the asus_dialpad_driver.service"
        exit 1
    else
        echo "Asus DialPad driver service started"
    fi

    if [ "$USER_INTERFACE" -eq 1 ]; then

        USER_INTERFACE_SERVICE_WAYLAND_FILE_PATH=asus_dialpad_driver_ui.wayland.service
        USER_INTERFACE_SERVICE_X11_FILE_PATH=asus_dialpad_driver_ui.x11.service
        USER_INTERFACE_SERVICE_INSTALL_FILE_NAME="asus_dialpad_driver_ui@.service"

        if [ "$XDG_SESSION_TYPE" == "x11" ]; then
            cat "$USER_INTERFACE_SERVICE_X11_FILE_PATH" | INSTALL_DIR_PATH=$INSTALL_DIR_PATH LAYOUT_NAME=$LAYOUT_NAME CONFIG_FILE_DIR_PATH="$CONFIG_FILE_DIR_PATH/" DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR XDG_SESSION_TYPE=$XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS envsubst '$INSTALL_DIR_PATH $LAYOUT_NAME $CONFIG_FILE_DIR_PATH $DISPLAY $XAUTHORITY $XDG_RUNTIME_DIR $XDG_SESSION_TYPE $DBUS_SESSION_BUS_ADDRESS $ERROR_LOG_FILE_PATH' | tee "$SERVICE_INSTALL_DIR_PATH/$USER_INTERFACE_SERVICE_INSTALL_FILE_NAME" >/dev/null
        else
            # DISPLAY=$DISPLAY for Xwayland
            cat "$USER_INTERFACE_SERVICE_WAYLAND_FILE_PATH" | INSTALL_DIR_PATH=$INSTALL_DIR_PATH LAYOUT_NAME=$LAYOUT_NAME CONFIG_FILE_DIR_PATH="$CONFIG_FILE_DIR_PATH/" DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR XDG_SESSION_TYPE=$XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS envsubst '$INSTALL_DIR_PATH $LAYOUT_NAME $CONFIG_FILE_DIR_PATH $DISPLAY $WAYLAND_DISPLAY $XDG_RUNTIME_DIR $XDG_SESSION_TYPE $DBUS_SESSION_BUS_ADDRESS $ERROR_LOG_FILE_PATH' | tee "$SERVICE_INSTALL_DIR_PATH/$USER_INTERFACE_SERVICE_INSTALL_FILE_NAME" >/dev/null
        fi

        echo

        if [[ $? != 0 ]]; then
            echo "Something went wrong when moving the asus_dialpad_driver_ui.service"
            exit 1
        else
            echo "Asus DialPad Driver User Interface service placed"
        fi

        systemctl --user daemon-reload

        if [[ $? != 0 ]]; then
            echo "Something went wrong when was called systemctl daemon reload"
            exit 1
        else
            echo "Systemctl daemon reloaded"
        fi

        systemctl enable --user asus_dialpad_driver_ui@$USER.service

        if [[ $? != 0 ]]; then
            echo "Something went wrong when enabling the asus_dialpad_driver.service"
            exit 1
        else
            echo "Asus DialPad driver User Interface service enabled"
        fi

        systemctl restart --user asus_dialpad_driver_ui@$USER.service
        if [[ $? != 0 ]]; then
            echo "Something went wrong when starting the asus_dialpad_driver.service"
            exit 1
        else
            echo "Asus DialPad driver User Interface service started"
        fi

    fi
esac