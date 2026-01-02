#!/usr/bin/env bash

source non_sudo_check.sh

START_TIME=${EPOCHREALTIME::-7}

# ENV VARS
if [ -z "$LOGS_DIR_PATH" ]; then
    LOGS_DIR_PATH="/var/log/asus-dialpad-driver"
fi

source install_logs.sh

echo

# log output from every installing attempt aswell
LOGS_INSTALL_LOG_FILE_NAME=install-"$(date +"%d-%m-%Y-%H-%M-%S")".log
LOGS_INSTALL_LOG_FILE_PATH="$LOGS_DIR_PATH/$LOGS_INSTALL_LOG_FILE_NAME"


{
    # determine plasma version
    # https://github.com/asus-linux-drivers/asus-numberpad-driver/pull/255
    if command -v kinfo >/dev/null 2>&1; then
        PLASMA_VER=$(kinfo 2>/dev/null | awk -F': ' '/KDE Plasma Version/ {print $2}' | cut -d. -f1)
    else
        PLASMA_VER=6  # default to plasma 6 for modern systems
    fi

    # pip pywayland requires gcc
    if command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt"
        sudo apt-get -y install ibus libevdev2 curl xinput i2c-tools python3-dev python3-virtualenv libxml2-utils libxkbcommon-dev gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo apt-get -y install libwayland-dev
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo apt-get -y install qdbus-qt$PLASMA_VER
        fi

    elif command -v pacman >/dev/null 2>&1; then
        PACKAGE_MANAGER="pacman"
        sudo pacman --noconfirm --needed -S ibus libevdev curl xorg-xinput i2c-tools python python-virtualenv libxml2 libxkbcommon gcc pkgconf
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo pacman --noconfirm --needed -S wayland
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo pacman --noconfirm --needed -S qt$PLASMA_VER-tools
        fi

    elif command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
        sudo dnf -y install ibus libevdev curl xinput i2c-tools python3-devel python3-virtualenv libxml2 libxkbcommon-devel gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo dnf -y install wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo dnf -y install qt$PLASMA_VER-qttools
        fi

    elif command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER="yum"
        sudo yum -y install ibus libevdev curl xinput i2c-tools python3-devel python3-virtualenv libxml2 libxkbcommon-devel gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo yum -y install wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo yum -y install qt$PLASMA_VER-qttools
        fi

    elif command -v zypper >/dev/null 2>&1; then
        PACKAGE_MANAGER="zypper"
        sudo zypper --non-interactive install ibus libevdev2 curl xinput i2c-tools python3-devel python3-virtualenv libxml2 libxkbcommon-devel gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo zypper --non-interactive install wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo zypper --non-interactive install qt$PLASMA_VER-tools-qdbus
        fi

    elif command -v xbps-install >/dev/null 2>&1; then
        PACKAGE_MANAGER="xbps-install"
        sudo xbps-install -Suy ibus-devel libevdev-devel curl xinput i2c-tools python3-devel python3-virtualenv libxml2 libxkbcommon-devel gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo xbps-install -Suy wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo xbps-install -Suy qt$PLASMA_VER-tools
        fi

    elif command -v emerge >/dev/null 2>&1; then
        PACKAGE_MANAGER="portage"
        sudo emerge app-i18n/ibus dev-libs/libevdev net-misc/curl x11-apps/xinput sys-apps/i2c-tools dev-lang/python dev-python/virtualenv dev-libs/libxml2 x11-libs/libxkbcommon sys-devel/gcc virtual/pkgconfig
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo emerge dev-libs/wayland
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo emerge dev-qt/qdbus
        fi
        
    elif command -v rpm-ostree >/dev/null 2>&1; then
        PACKAGE_MANAGER="rpm-ostree"
        sudo rpm-ostree install xinput virtualenv python3-devel wayland-protocols-devel pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo rpm-ostree install wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo rpm-ostree install qt$PLASMA_VER-tools
        fi

    elif command -v eopkg >/dev/null 2>&1; then
        PACKAGE_MANAGER="eopkg"
        sudo eopkg install -y ibus libevdev curl xinput i2c-tools python3-devel python3-virtualenv libxml2-devel libxkbcommon-devel gcc pkg-config
        if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
            sudo eopkg install -y wayland-devel
        fi
        if [[ "$DESKTOP_SESSION" == plasma* ]]; then
            sudo eopkg install -y qt$PLASMA_VER-tools
        fi

    else
        echo "Warning: Not detected package manager. Driver may not work properly because required packages have not been installed. Please create an issue (https://github.com/asus-linux-drivers/asus-dialpad-driver/issues)."
    fi

    if [[ $? != 0 ]]; then
        echo "Error: Something went wrong during installing packages"
        source install_begin_send_anonymous_report.sh
        exit 1
    else
        source install_begin_send_anonymous_report.sh
    fi

    echo

    source install_user_groups.sh

    echo

    source install_device_check.sh

    echo

    # do not install __pycache__
    if [[ -d layouts/__pycache__ ]]; then
        rm -rf layouts/__pycache__
    fi

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
    CONFIG_FILE_PATH="$CONFIG_FILE_DIR_PATH/$CONFIG_FILE_NAME"

    sudo mkdir -p "$INSTALL_DIR_PATH/layouts"
    sudo chown -R $USER "$INSTALL_DIR_PATH"
    sudo install dialpad.py "$INSTALL_DIR_PATH"
    sudo install -t "$INSTALL_DIR_PATH/layouts" layouts/*.py

    if [[ -f "$CONFIG_FILE_PATH" ]]; then
        read -r -p "In system remains config file from previous installation. Do you want replace that config with default config? [y/N]" RESPONSE
        case "$RESPONSE" in [yY][eE][sS]|[yY])

            # default will be autocreated, that is why is removed
            sudo rm -f $CONFIG_FILE_PATH
            if [[ $? != 0 ]]; then
                echo "$CONFIG_FILE_PATH cannot be removed correctly..."
                exit 1
            fi
            ;;
        *)
            source install_config_send_anonymous_report.sh
            ;;
        esac
    else
        echo "Default config will be autocreated during the first run and available for futher modifications here:"
        echo "$CONFIG_FILE_PATH"
    fi

    echo

    # create Python3 virtual environment
    virtualenv --python=$(command -v python3) $INSTALL_DIR_PATH/.env
    source $INSTALL_DIR_PATH/.env/bin/activate
    pip3 install --upgrade pip
    pip3 install --upgrade setuptools
    pip3 install -r requirements.txt
    if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
        pip3 install -r requirements.wayland.txt
    fi

    echo

    if [ -z "$LAYOUT_NAME" ]; then

      source install_layout_auto_suggestion.sh

      echo

      if [ -z "$LAYOUT_NAME" ]; then

        source install_layout_select.sh

        echo
      fi
    fi

    source install_service.sh

    echo

    echo "Installation finished successfully"

    echo

    END_TIME=${EPOCHREALTIME::-7}
    source install_finished_send_anonymous_report.sh

    echo "Installation finished successfully"

    echo

    read -r -p "Reboot is required. Do you want reboot now? [y/N]" response
    case "$response" in [yY][eE][sS]|[yY])
        sudo /sbin/reboot
        ;;
    *)
        ;;
    esac

    echo

    exit 0
} 2>&1 | sudo tee "$LOGS_INSTALL_LOG_FILE_PATH"
