#!/usr/bin/env bash

# Sourced helpers: defining these functions never installs packages or starts services.
DIALPAD_SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

dialpad_init_paths() {
    PYTHON=${PYTHON:-$(command -v python3)}
    if [[ -z "$PYTHON" ]]; then
        echo "Python 3 is required." >&2
        return 1
    fi
    INSTALL_DIR_PATH=${INSTALL_DIR_PATH:-/usr/share/asus-dialpad-driver}
    INSTALL_DIR_PATH=$("$PYTHON" -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$INSTALL_DIR_PATH") || return
    local metadata="$INSTALL_DIR_PATH/.installation.json"
    if [[ -z "$CONFIG_FILE_DIR_PATH" && -f "$metadata" ]]; then
        CONFIG_FILE_DIR_PATH=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["config_dir"])' "$metadata") || return
    fi
    CONFIG_FILE_DIR_PATH=${CONFIG_FILE_DIR_PATH:-$INSTALL_DIR_PATH}
    CONFIG_FILE_DIR_PATH=$("$PYTHON" -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$CONFIG_FILE_DIR_PATH") || return
    CONFIG_FILE_NAME=${CONFIG_FILE_NAME:-dialpad_dev}
    if [[ "$CONFIG_FILE_NAME" != dialpad_dev ]]; then
        echo "CONFIG_FILE_NAME must be dialpad_dev; use CONFIG_FILE_DIR_PATH to select another instance." >&2
        return 1
    fi
    CONFIG_FILE_PATH="$CONFIG_FILE_DIR_PATH/dialpad_dev"
    if [[ -f "$metadata" ]]; then
        if [[ -z "$DIALPAD_BIN_DIR_PATH" ]]; then
            DIALPAD_BIN_DIR_PATH=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("bin_dir", ""))' "$metadata") || return
        fi
        if [[ -z "$DIALPAD_APPLICATIONS_DIR_PATH" ]]; then
            DIALPAD_APPLICATIONS_DIR_PATH=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("applications_dir", ""))' "$metadata") || return
        fi
    fi
    DIALPAD_BIN_DIR_PATH=${DIALPAD_BIN_DIR_PATH:-$HOME/.local/bin}
    DIALPAD_APPLICATIONS_DIR_PATH=${DIALPAD_APPLICATIONS_DIR_PATH:-${XDG_DATA_HOME:-$HOME/.local/share}/applications}
    DIALPAD_BIN_DIR_PATH=$("$PYTHON" -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$DIALPAD_BIN_DIR_PATH") || return
    DIALPAD_APPLICATIONS_DIR_PATH=$("$PYTHON" -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$DIALPAD_APPLICATIONS_DIR_PATH") || return
}

dialpad_owned_directory() {
    local directory=$1
    if [[ -d "$directory" && -w "$directory" ]]; then
        return 0
    fi
    if ! mkdir -p -- "$directory" 2>/dev/null || [[ ! -w "$directory" ]]; then
        sudo mkdir -p -- "$directory" || return
        # Do not recursively change ownership of preserved user data.
        sudo chown "$(id -u):$(id -g)" -- "$directory" || return
    fi
}

dialpad_install_backend() {
    local file
    for file in dialpad_layout.py dialpad_layout_linux.py dialpad_events.json bundled-layouts.json; do
        if [[ ! -f "$DIALPAD_SOURCE_DIR/$file" ]]; then
            echo "Missing required installation file: $file" >&2
            return 1
        fi
    done
    dialpad_owned_directory "$INSTALL_DIR_PATH" || return
    dialpad_owned_directory "$INSTALL_DIR_PATH/layouts" || return
    dialpad_owned_directory "$CONFIG_FILE_DIR_PATH" || return
    dialpad_owned_directory "$CONFIG_FILE_DIR_PATH/layouts" || return
    for file in dialpad_layout.py dialpad_layout_linux.py dialpad_events.json bundled-layouts.json; do
        install -m 644 -- "$DIALPAD_SOURCE_DIR/$file" "$INSTALL_DIR_PATH/$file" || return
    done
    "$PYTHON" - "$DIALPAD_SOURCE_DIR/layouts" "$INSTALL_DIR_PATH/layouts" <<'PY'
import os
from pathlib import Path
import shutil
import sys
import tempfile

source_directory, destination_directory = map(Path, sys.argv[1:])
for source in sorted(source_directory.iterdir()):
    if source.suffix not in ('.py', '.json') or not source.is_file():
        continue
    destination = destination_directory / source.name
    if os.path.lexists(destination):
        print(f'Preserving installed layout: {destination}')
        continue
    fd, temporary = tempfile.mkstemp(prefix='.install-layout-', dir=destination_directory)
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
            os.fchmod(output.fileno(), 0o644)
        try:
            # Atomic create-only publication: even a concurrently created user copy wins.
            os.link(temporary, destination)
        except FileExistsError:
            print(f'Preserving installed layout: {destination}')
    finally:
        os.unlink(temporary)
PY
}

dialpad_python() {
    if [[ -x "$INSTALL_DIR_PATH/.env/bin/python3" ]]; then
        "$INSTALL_DIR_PATH/.env/bin/python3" "$@"
    else
        "$PYTHON" "$@"
    fi
}

dialpad_config_cli() {
    dialpad_python "$INSTALL_DIR_PATH/dialpad_layout.py" \
        --config-dir "$CONFIG_FILE_DIR_PATH" --install-dir "$INSTALL_DIR_PATH" "$@"
}

dialpad_install_qt_dependencies() {
    if [[ "$DIALPAD_QT_DEPENDENCIES_READY" == 1 ]]; then
        return 0
    fi
    if [[ ! -x "$INSTALL_DIR_PATH/.env/bin/python3" ]]; then
        "$PYTHON" -m venv --system-site-packages "$INSTALL_DIR_PATH/.env" || {
            echo "Install your distribution's Python venv support, then rerun this installer." >&2
            return 1
        }
    fi
    "$INSTALL_DIR_PATH/.env/bin/python3" -m pip install -r "$DIALPAD_SOURCE_DIR/requirements.ui.txt" || return
    DIALPAD_QT_DEPENDENCIES_READY=1
}

dialpad_install_launchers() {
    local manager=${1:-0}
    if [[ -f "$INSTALL_DIR_PATH/dialpad_layout_manager.py" && -f "$INSTALL_DIR_PATH/dialpad_layout_editor.py" ]]; then
        # A reinstall/config-dir change must also refresh an already installed manager.
        manager=1
    fi
    mkdir -p -- "$DIALPAD_BIN_DIR_PATH" || return
    local cli="$DIALPAD_BIN_DIR_PATH/asus-dialpad-layout"
    local interpreter="$INSTALL_DIR_PATH/.env/bin/python3"
    [[ -x "$interpreter" ]] || interpreter=$PYTHON
    printf '#!/usr/bin/env bash\nexec %q %q --config-dir %q --install-dir %q "$@"\n' \
        "$interpreter" "$INSTALL_DIR_PATH/dialpad_layout.py" "$CONFIG_FILE_DIR_PATH" "$INSTALL_DIR_PATH" > "$cli" || return
    chmod 755 -- "$cli" || return
    if [[ "$manager" == 1 ]]; then
        mkdir -p -- "$DIALPAD_APPLICATIONS_DIR_PATH" || return
        local launcher="$DIALPAD_BIN_DIR_PATH/asus-dialpad-layout-manager"
        printf '#!/usr/bin/env bash\nexec %q %q --config-dir %q --install-dir %q "$@"\n' \
            "$interpreter" "$INSTALL_DIR_PATH/dialpad_layout_manager.py" "$CONFIG_FILE_DIR_PATH" "$INSTALL_DIR_PATH" > "$launcher" || return
        chmod 755 -- "$launcher" || return
    fi
    "$PYTHON" - "$INSTALL_DIR_PATH" "$CONFIG_FILE_DIR_PATH" "$DIALPAD_BIN_DIR_PATH" "$DIALPAD_APPLICATIONS_DIR_PATH" "$manager" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

install, config, binaries, applications = map(Path, sys.argv[1:5])
metadata_path = install / '.installation.json'
metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
entries = metadata.setdefault('launchers', {})
paths = [binaries / 'asus-dialpad-layout']
if sys.argv[5] == '1':
    launcher = binaries / 'asus-dialpad-layout-manager'
    desktop = applications / 'asus-dialpad-layout-manager.desktop'
    # Desktop strings and Exec argument quoting are two separate escaping layers.
    argument = ''.join('\\' + c if c in '\\"`$' else c for c in str(launcher))
    argument = argument.replace('\\', '\\\\').replace('%', '%%')
    argument = argument.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    desktop.write_text('[Desktop Entry]\nType=Application\nName=DialPad Layout Manager\n'
                       'Comment=Edit and activate Asus DialPad layouts\n'
                       f'Exec="{argument}"\nIcon=input-gaming\nTerminal=false\n'
                       'Categories=Settings;HardwareSettings;\n', encoding='utf-8')
    paths += [launcher, desktop]
for path in paths:
    entries[str(path.absolute())] = hashlib.sha256(path.read_bytes()).hexdigest()
metadata.update(config_dir=str(config), bin_dir=str(binaries.absolute()),
                applications_dir=str(applications.absolute()))
fd, temporary = tempfile.mkstemp(prefix='.installation-', dir=install)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(metadata, stream, indent=2)
        stream.write('\n')
    os.replace(temporary, metadata_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

dialpad_remove_launchers() {
    "$PYTHON" - "$INSTALL_DIR_PATH/.installation.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

metadata = Path(sys.argv[1])
if metadata.exists():
    for filename, digest in json.loads(metadata.read_text()).get('launchers', {}).items():
        path = Path(filename)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            path.unlink()
        elif path.exists():
            print(f'Preserving changed launcher or another installation: {path}')
PY
}

dialpad_render_service() {
    DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" XDG_SESSION_TYPE="$XDG_SESSION_TYPE" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" LOG="$LOG" \
    "$PYTHON" - "$DIALPAD_SOURCE_DIR/$1" "$2" "$INSTALL_DIR_PATH" "$CONFIG_FILE_DIR_PATH" "$LAYOUT_NAME" <<'PY'
import os
from pathlib import Path
from string import Template
import sys


def quote(value, command=False):
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    value = value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return value.replace('$', '$$') if command else value


values = {name: quote(os.environ.get(name, '')) for name in (
    'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR',
    'XDG_SESSION_TYPE', 'DBUS_SESSION_BUS_ADDRESS')}
values.update(INSTALL_DIR_PATH=quote(sys.argv[3], True),
              CONFIG_FILE_DIR_PATH=quote(sys.argv[4], True), LAYOUT_NAME=quote(sys.argv[5], True))
values['LOG_ENV_LINE'] = 'Environment="LOG=' + quote(os.environ['LOG']) + '"' if os.environ.get('LOG') else ''
Path(sys.argv[2]).write_text(Template(Path(sys.argv[1]).read_text()).substitute(values))
PY
}

dialpad_remove_program_files() {
    "$PYTHON" - "$INSTALL_DIR_PATH" "$CONFIG_FILE_DIR_PATH" "${1:-0}" <<'PY'
from pathlib import Path
import shutil
import sys

install, config = map(Path, sys.argv[1:3])
purge = sys.argv[3] == '1'
protected = config.resolve()


def remove(path, preserve_config=True):
    resolved = path.resolve()
    if preserve_config and (resolved == protected or resolved in protected.parents):
        print(f'Preserving installation directory containing user configuration: {path}')
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


for name in (
    'dialpad.py', 'dialpad_runtime.py', 'dialpad_layout.py', 'dialpad_layout_linux.py',
    'dialpad_layout_manager.py', 'dialpad_layout_editor.py', 'dialpad_ui.py',
    'dialpad_overlay.py',
    'dialpad_events.json', '.env', '__pycache__',
):
    remove(install / name)
if purge:
    # Never remove the entire config/install directory: it can contain unrelated files.
    for directory in {config, install}:
        remove(directory / 'layouts', preserve_config=False)
        remove(directory / '.layout-state', preserve_config=False)
    for name in ('dialpad_dev', '.dialpad_dev.lock'):
        remove(config / name, preserve_config=False)
    for name in ('bundled-layouts.json', '.installation.json'):
        remove(install / name, preserve_config=False)
    print('Removed the explicitly selected configuration, layouts and recovery data.')
else:
    print(f'Preserved configuration, layouts and recovery data in {config}')
    if config.resolve() != install.resolve():
        print(f'Preserved installed layouts in {install / "layouts"}')
PY
}
