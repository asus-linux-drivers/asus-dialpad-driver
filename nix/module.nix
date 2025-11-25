
inputs: { config, lib, pkgs, ... }:

let
  cfg = config.services.asus-dialpad-driver;

  configFileDir = pkgs.writeTextFile {
    name = "asus-dialpad-driver-config";
    text = lib.generators.toINI {} cfg.config;
    destination = "/dialpad_dev";
  };
  
in {
  options.services.asus-dialpad-driver = {
    enable = lib.mkEnableOption "Enable the Asus DialPad Driver service.";

    package = lib.mkOption {
      type = lib.types.package;
      default = inputs.self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The package to use for the Asus DialPad Driver.";
    };

    layout = lib.mkOption {
      type = lib.types.str;
      default = "proartp16";
      description = "The layout identifier for the DialPad driver (e.g. proart16). This value is required.";
    };

    display = lib.mkOption {
      type = lib.types.str;
      default = ":0";
      description = "The DISPLAY environment variable. Default is :0.";
    };

    wayland = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable this option to run under Wayland. Disable it for X11.";
    };

    waylandDisplay = lib.mkOption {
      type = lib.types.str;
      default = "wayland-0";
      description = "The WAYLAND_DISPLAY environment variable. Default is wayland-0.";
    };

    ignoreWaylandDisplayEnv = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description =
        "If true, WAYLAND_DISPLAY will not be set in the service environment.";
    };

    runtimeDir = lib.mkOption {
      type = lib.types.str;
      default = "/run/user/1000/";
      description = "The XDG_RUNTIME_DIR environment variable, specifying the runtime directory.";
    };

    config = lib.mkOption {
      type = with lib.types;
        let
          valueType = nullOr (oneOf [
            bool
            int
            float
            str
            path
            (attrsOf valueType)
            (listOf valueType)
          ]) // {
            description = "Asus DialPad Driver configuration value";
          };
        in valueType;
      example = {
        main = {
          enabled = false;
          slices_count = 4;
          disable_due_inactivity_time = 0;
          touchpad_disables_dialpad = true;
          activation_time = 1;
          config_supress_app_specifics_shortcuts = 0;
        };
      };
      default = {};
      description = "Configuration options for the Asus DialPad Driver.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];

    # Ensure the writable directories exists
    systemd.tmpfiles.rules = [
      "d /var/log/asus-dialpad-driver 0755 root root -"
    ];

    # Enable i2c
    hardware.i2c.enable = true;

    # Add groups for dialpad
    users.groups = {
      uinput = { };
      input = { };
      i2c = { };
    };

    # Add root to the necessary groups
    users.users.root.extraGroups = [ "i2c" "input" "uinput" ];

    # Add the udev rule to set permissions for uinput and i2c-dev
    services.udev.extraRules = ''
      # Set uinput device permissions
      KERNEL=="uinput", GROUP="uinput", MODE="0660"
      # Set i2c-dev permissions
      SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"
    '';

    # Load specific kernel modules
    boot.kernelModules = [ "uinput" "i2c-dev" ];

    systemd.services.asus-dialpad-driver = {
      description = "Asus DialPad Driver";
      wantedBy = [ "default.target" ];
      startLimitBurst=20;
      startLimitIntervalSec=300;
      serviceConfig = {
        Type = "simple";
        ExecStart = "${cfg.package}/share/asus-dialpad-driver/dialpad.py ${cfg.layout} ${configFileDir}/";
        StandardOutput = null;
        StandardError = null;
        Restart = "on-failure";
        RestartSec = 1;
        TimeoutSec = 5;
        WorkingDirectory = "${cfg.package}/share/asus-dialpad-driver";
        Environment = [
          "XDG_SESSION_TYPE=${if cfg.wayland then "wayland" else "x11"}"
          "XDG_RUNTIME_DIR=${cfg.runtimeDir}"
          "DISPLAY=${cfg.display}"
          "LOG=WARNING"
        ] ++ lib.optional (!cfg.ignoreWaylandDisplayEnv)
          "WAYLAND_DISPLAY=${cfg.waylandDisplay}";
      };
    };

  };
}
