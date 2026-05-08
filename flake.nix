{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = {nixpkgs, self, ...} @ inputs: let
    forAllSystems = nixpkgs.lib.genAttrs ["x86_64-linux" "i686-linux" "aarch64-linux"];

    pkgsForEach = forAllSystems (system: nixpkgs.legacyPackages.${system}.appendOverlays [
      self.overlays.default
    ]);
  in {
    packages = forAllSystems (system:
      let pkgs = pkgsForEach.${system}; in
      {
        inherit (pkgs) asus-dialpad-driver;
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.asus-dialpad-driver;
      });

    devShells = forAllSystems (system: {
      default = pkgsForEach.${system}.callPackage ./nix/shell.nix {
        inherit self;
        python3Packages = pkgsForEach.${system}.python313Packages;
      };
    });

    overlays = {
      default = import ./nix/overlay/default.nix;
    };

    nixosModules.default = import ./nix/module.nix inputs;
  };
}
