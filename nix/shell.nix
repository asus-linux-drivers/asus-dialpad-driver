{ stdenv,
  python3Packages,
  mkShell,
  self,
}:
mkShell {
  inputsFrom = [ self.packages.${stdenv.hostPlatform.system}.default ];
  
  packages = [
        python3Packages.pip
  ];
}
