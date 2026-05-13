{ testers }:

testers.nixosTest ({ pkgs, ... }: {
  name = "asus-dialpad-driver-module-basic-setup";

  nodes.machine = { ... }: {
    imports = [
      "${pkgs.path}/nixos/tests/common/user-account.nix"
      ../module.nix
    ];

    services.getty.autologinUser = "alice";

    environment = {
      variables = {
        WLR_RENDERER = "pixman";
      };
    };

    users.users.alice = {
      uid = 1000;
      name = "alice";
      description = "Alice Foobar";
      password = "foobar";
      isNormalUser = true;
      extraGroups = [ "i2c" "input" "uinput" ];
    };

    # Most folks will be using Wayland at this point (despite X11 testing being simpler).
    # Sway is both popular & lightweight enough to be a good test case.
    programs.sway.enable = true;

    # Automatically configure & start Sway when logging in on tty1:
    programs.bash.loginShellInit = /* bash */ ''
      if [ "$(tty)" = "/dev/tty1" ]; then
        set -e
        sway
      fi
    '';

    virtualisation.qemu.options = [ "-vga none -device virtio-gpu-pci" ];

    hardware.asus-dialpad-driver = {
      enable = true;
      daemon.enable = true;
      layout = "proartp16";
    };
  };

  testScript = { nodes, ... }:
    let
      inherit (nodes) machine;
      inherit (machine.users.users) alice;
    in
    /* python */ ''
      machine.start()
      machine.wait_for_unit("multi-user.target")

      machine.wait_for_file("/run/user/${builtins.toString alice.uid}/wayland-1")
      machine.wait_for_unit("sway-session.target", "${alice.name}")
      machine.wait_for_unit("asus-dialpad-driver.service", "${alice.name}")

      assert "uinput" in machine.succeed("udevadm info -e")
    '';
})
