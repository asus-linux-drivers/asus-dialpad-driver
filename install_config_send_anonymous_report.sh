#!/usr/bin/env bash

# INHERIT VARS
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

G_ID="G-371CTM2RN8"
API_SECRET="lrAbtN4rTA6Q-5oufTN4Sw"
CLIENT_ID="1547780539.1740322228"
LAPTOP_ID=$(sudo cat /sys/class/dmi/id/product_uuid)
EVENT_NAME="install_config"

DISABLE_DUE_INACTIVITY_TIME=$(cat $CONFIG_FILE_PATH | grep disable_due_inactivity_time | cut -d '=' -f2 | head -n 1 | xargs)
TOUCHPAD_DISABLES_DIALPAD=$(cat $CONFIG_FILE_PATH | grep touchpad_disables_dialpad | cut -d '=' -f2 | head -n 1 | xargs)
ACTIVATION_TIME=$(cat $CONFIG_FILE_PATH | grep activation_time | cut -d '=' -f2 | head -n 1 |  xargs)
DRIVER_VERSION=$(cat CHANGELOG.md | grep -Po '(?<=## )[^ ]*' | head -1)

CURL_PAYLOAD='{
    "client_id": "'${CLIENT_ID}'",
    "user_id": "'${LAPTOP_ID}'",
    "non_personalized_ads": true,
    "events": [
        {
            "name": "'${EVENT_NAME}'",
            "params": {
                "laptop_id": "'${LAPTOP_ID}'",
                "disable_due_inactivity_time": "'${DISABLE_DUE_INACTIVITY_TIME}'",
                "touchpad_disables_dialpad": "'${TOUCHPAD_DISABLES_DIALPAD}'",
                "activation_time": "'${ACTIVATION_TIME}'",
                "version": "'${DRIVER_VERSION}'"
            }
        }
    ]
}'
CURL_URL="https://www.google-analytics.com/mp/collect?&measurement_id=$G_ID&api_secret=$API_SECRET"

#echo $CURL_PAYLOAD
$(curl -d "$CURL_PAYLOAD" -H "Content-Type: application/json" -X POST -s --max-time 2 "$CURL_URL")