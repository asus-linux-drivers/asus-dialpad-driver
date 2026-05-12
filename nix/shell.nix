{
  mkShell,
  python3Packages,
  asus-dialpad-driver,
}:

mkShell {
  name = "asus-dialpad-driver";

  inputsFrom = [
    asus-dialpad-driver
  ];
  
  packages = [
    python3Packages.pip
  ];
}
