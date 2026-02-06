{ lib
, python3Packages
, ibus
, libevdev
, curl
, xorg
, i2c-tools
, libxml2
, libxkbcommon
, waylandSupport ? false
}:
python3Packages.buildPythonApplication {
  pname = "asus-dialpad-driver";
  version = "2.1.0";
  src = ../.;
  
  pyproject = false;
  
  dependencies = with python3Packages; [
    numpy
    python3Packages.libevdev
    xlib
    pyinotify
    pyasyncore
    pywayland
    xkbcommon
    systemd-python
    xcffib
    python-periphery
  ] ++ lib.optional waylandSupport [ python3Packages.pywayland ];

  buildInputs = [
    ibus
    libevdev
    curl
    xorg.xinput
    i2c-tools
    libxml2
    libxkbcommon
  ];

  # Install files for driver and layouts
  installPhase = ''
    mkdir -p $out/share/asus-dialpad-driver

    # Copy the driver script
    install -Dm755 dialpad.py $out/share/asus-dialpad-driver/dialpad.py

    # Copy layouts directory if it exists, and remove __pycache__ if present
    if [ -d layouts ]; then
      cp -r layouts $out/share/asus-dialpad-driver/
      rm -rf $out/share/asus-dialpad-driver/layouts/__pycache__
    fi
  '';

  preFixup = ''
    # Change line endings to Unix format
    sed -i 's/\r$//' $out/share/asus-dialpad-driver/dialpad.py
  '';
  
  # Patch shebangs (defaults to files in $out/bin)
  postFixup = ''
    wrapPythonProgramsIn "$out/share/asus-dialpad-driver" "$out $pythonPath"
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
