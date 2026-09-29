{ lib
, python3Packages
, ibus
, libevdev
, curl
, xinput # xorg.xinput can passed in with override for old version of Nixpkgs
, i2c-tools
, libxml2
, libxkbcommon
, qt6
, waylandSupport ? false
, x11Support ? true
, layoutManagerSupport ? false
}:

python3Packages.buildPythonPackage {
  pname = "asus-dialpad-driver";
  version = "2.5.2";

  src =
    let fs = lib.fileset; in
    fs.toSource {
      root = ../.;
      fileset = fs.unions [
        (fs.fileFilter (f: f.hasExt "py" || f.hasExt "json") ../.)
        ../layouts
        ../laptop_dialpad_layouts
      ];
    };

  pyproject = false;

  dependencies = with python3Packages; [
    numpy
    python3Packages.libevdev
    pyinotify
    pyasyncore
    xkbcommon
    systemd-python
    python-periphery
  ] ++ lib.optionals x11Support [
    xlib
    xcffib
  ] ++ lib.optional waylandSupport pywayland
    ++ lib.optional layoutManagerSupport pyside6;

  nativeBuildInputs = lib.optional layoutManagerSupport qt6.wrapQtAppsHook;
  dontWrapQtApps = true;

  buildInputs = [
    ibus
    libevdev
    curl
    xinput
    i2c-tools
    libxml2
    libxkbcommon
  ];

  installPhase = ''
    runHook preInstall
    data="$out/share/asus-dialpad-driver"
    mkdir -p "$data/layouts" "$out/bin"
    install -m755 dialpad.py "$data/dialpad.py"
    install -m644 dialpad_layout.py dialpad_layout_linux.py dialpad_runtime.py \
      dialpad_events.json bundled-layouts.json "$data/"
    for layout in layouts/*.py layouts/*.json; do
      [ ! -f "$layout" ] || install -m644 "$layout" "$data/layouts/"
    done

    # Explicit, deterministic XDG configuration; no search among existing instances.
    cat > "$out/bin/asus-dialpad-layout" <<EOF
#!${python3Packages.python.interpreter}
import os
from pathlib import Path
import runpy
import sys
root = "$data"
sys.path.insert(0, root)
config = os.environ.get("DIALPAD_CONFIG_DIR") or str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "asus-dialpad-driver")
sys.argv[1:1] = ["--config-dir", config, "--install-dir", root]
runpy.run_path(root + "/dialpad_layout.py", run_name="__main__")
EOF
    chmod +x "$out/bin/asus-dialpad-layout"

    ${lib.optionalString layoutManagerSupport ''
      install -m644 dialpad_layout_manager.py dialpad_layout_editor.py dialpad_overlay.py "$data/"
      cat > "$out/bin/asus-dialpad-layout-manager" <<EOF
#!${python3Packages.python.interpreter}
import os
from pathlib import Path
import runpy
import sys
root = "$data"
sys.path.insert(0, root)
config = os.environ.get("DIALPAD_CONFIG_DIR") or str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "asus-dialpad-driver")
sys.argv[1:1] = ["--config-dir", config, "--install-dir", root]
runpy.run_path(root + "/dialpad_layout_manager.py", run_name="__main__")
EOF
      chmod +x "$out/bin/asus-dialpad-layout-manager"
      mkdir -p "$out/share/applications"
      cat > "$out/share/applications/asus-dialpad-layout-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DialPad Layout Manager
Comment=Edit and activate Asus DialPad layouts
Exec=$out/bin/asus-dialpad-layout-manager
Icon=input-gaming
Terminal=false
Categories=Settings;HardwareSettings;
EOF
    ''}
    runHook postInstall
  '';

  preFixup = ''
    sed -i 's/\r$//' "$out/share/asus-dialpad-driver/dialpad.py"
  '';

  postFixup = ''
    wrapPythonProgramsIn "$out/share/asus-dialpad-driver" "$out $pythonPath"
    wrapPythonProgramsIn "$out/bin" "$out $pythonPath"
    ${lib.optionalString layoutManagerSupport ''
      wrapProgram "$out/bin/asus-dialpad-layout-manager" "''${qtWrapperArgs[@]}"
    ''}
  '';

  meta = {
    homepage = "https://github.com/asus-linux-drivers/asus-dialpad-driver";
    description = "Linux driver for DialPad on Asus laptops.";
    license = lib.licenses.gpl2;
    platforms = lib.platforms.linux;
    maintainers = with lib.maintainers; [asus-linux-drivers];
    mainProgram = "dialpad.py";
  };
}
