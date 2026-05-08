final: prev: {
  asus-dialpad-driver = final.callPackage ../default.nix {
    xinput = prev.xinput or prev.xorg.xinput;
  };
}
