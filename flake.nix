{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = {nixpkgs, self, ...}: let
    forAllSystems = nixpkgs.lib.genAttrs ["x86_64-linux" "i686-linux" "aarch64-linux"];

    pkgsForEach = forAllSystems (system: nixpkgs.legacyPackages.${system}.appendOverlays [
      self.overlays.default
      self.overlays.development
      self.overlays.check
    ]);
  in {
    packages = forAllSystems (system:
      let pkgs = pkgsForEach.${system}; in
      {
        inherit (pkgs) asus-dialpad-driver;
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.asus-dialpad-driver;
      });

    checks = forAllSystems (system: pkgsForEach.${system}.asus-dialpad-driver-check);

    devShells = forAllSystems (system: {
      default = pkgsForEach.${system}.asus-dialpad-driver-shell;
    });

    overlays = {
      default = import ./nix/overlay/default.nix;
      development = import ./nix/overlay/development.nix;
      check = import ./nix/overlay/check.nix;
    };

    nixosModules.default = import ./nix/module.nix;
  };
}
