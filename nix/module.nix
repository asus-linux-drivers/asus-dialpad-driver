{ config, lib, pkgs, ... }:

let
  cfg = config.hardware.asus-dialpad-driver;

  defaultConfigFile =
    pkgs.writeText "dialpad_dev" ''
      ; vim: filetype=dosini
      ; Asus DialPad configuration
      ${lib.generators.toINI { } cfg.defaultConfig}
    '';

  package = cfg.package.override {
    waylandSupport = lib.elem "wayland" cfg.sessionTypes;
    x11Support = lib.elem "x11" cfg.sessionTypes;
    layoutManagerSupport = cfg.layoutManager.enable;
  };
in {
  imports = [
    (lib.mkRenamedOptionModule
      [ "services" "asus-dialpad-driver" ]
      [ "hardware" "asus-dialpad-driver" ])
  ];

  options.hardware.asus-dialpad-driver = {
    enable = lib.mkOption {
      default = false;
      type = lib.types.bool;
      description = "Enable the Asus DialPad Driver module (udev rules, i2c, groups).";
    };

    daemon.enable = lib.mkOption {
      default = true;
      type = lib.types.bool;
      description = ''
        Whether to start the Asus DialPad Driver daemon as a systemd user service.
        Note that the user *must* be enrolled in these groups: i2c, input, uinput.
      '';
    };

    layoutManager.enable = lib.mkOption {
      default = false;
      type = lib.types.bool;
      description = ''
        Install the standalone PySide6 layout manager and desktop entry.
        This does not start a manager or floating-overlay service and can be enabled
        without the hardware module or daemon for offline layout editing.
      '';
    };

    package = lib.mkPackageOption pkgs "asus-dialpad-driver" { };

    sessionTypes = lib.mkOption {
      type = lib.types.uniq (lib.types.nonEmptyListOf (lib.types.enum [ "wayland" "x11" ]));
      default = [ "wayland" "x11" ];
      description = ''
        The display server session types to support.
        All listed types will be built into the package.
      '';
    };

    layout = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9][A-Za-z0-9_-]*";
      default = "proartp16";
      description = ''
        Positional default layout identifier (e.g. proartp16). A nonempty layout in
        the user's dialpad_dev takes precedence. This fallback is not persisted.
      '';
    };

    defaultConfig = lib.mkOption {
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
      default = { };
      description = ''
        Defaults merged into still-missing configuration keys before daemon startup.
        Existing settings, including an explicit layout selection, are preserved.
        Configuration lives in the user's XDG config directory under asus-dialpad-driver.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {
        LOG = "INFO";
        XDG_RUNTIME_DIR = "/run/user/1000";
        DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
        DISPLAY = ":0";
        WAYLAND_DISPLAY = "wayland-0";
        XDG_SESSION_TYPE = "wayland";
      };
      description = ''
        Environment variables passed to the Asus DialPad Driver daemon.
      '';
    };
  };

  config = lib.mkMerge [
    (lib.mkIf (cfg.enable || cfg.layoutManager.enable) {
      environment.systemPackages = [ package ];
    })
    (lib.mkIf cfg.enable {
    # Enable i2c
    hardware.i2c.enable = true;

    # Enable uinput
    hardware.uinput.enable = true;

    # Add rest of the groups for dialpad
    users.groups = {
      input = { };
    };

    systemd.user.services.asus-dialpad-driver = lib.mkIf cfg.daemon.enable {
      description = "Asus DialPad Driver";
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      serviceConfig = {
        Type = "simple";
        ConfigurationDirectory = "asus-dialpad-driver";
        # Merge missing defaults under the same lock/transaction as runtime and GUI edits.
        ExecStartPre = "${package}/bin/asus-dialpad-layout --config-dir \"%E/asus-dialpad-driver\" config-defaults ${defaultConfigFile}";
        ExecStart = "${package}/share/asus-dialpad-driver/dialpad.py ${cfg.layout} \"%E/asus-dialpad-driver/\"";
        # The script logs to the journal directly
        StandardOutput = "null";
        StandardError = "null";
        Restart = "on-failure";
        RestartSec = 1;
        TimeoutSec = 5;
        WorkingDirectory = "${package}/share/asus-dialpad-driver";
        Environment = lib.mapAttrsToList
          (name: value: "${name}=${value}")
          cfg.environment;
      };
      path = [ pkgs.i2c-tools pkgs.qt6.qttools ];
    };

    })
  ];
}
