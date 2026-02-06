{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = {nixpkgs, self, ...} @ inputs: let
    forAllSystems = nixpkgs.lib.genAttrs ["x86_64-linux" "i686-linux" "aarch64-linux"];
    pkgsForEach = nixpkgs.legacyPackages;
  in {
    packages = forAllSystems (system: {
      default = pkgsForEach.${system}.callPackage ./nix { python3Packages = pkgsForEach.${system}.python313Packages; };
    });

    devShells = forAllSystems (system: {
      default = pkgsForEach.${system}.callPackage ./nix/shell.nix { 
        inherit self;
        python3Packages = pkgsForEach.${system}.python313Packages; 
      };
    });

    overlays.default = final: _: {
      asus-dialpad-driver = self.packages.${final.stdenv.hostPlatform.system}.default;
    };

    nixosModules.default = import ./nix/module.nix inputs;
  };
}
