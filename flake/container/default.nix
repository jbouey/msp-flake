{ pkgs, infra-watcher, nix2container }:
nix2container.buildImage {
  name = "registry.example.com/infra-watcher";
  tag  = "0.1";
  fromImage = pkgs.dockerTools.baseImage;
  copyToRoot = [ infra-watcher ];
  config = {
    Cmd = [ "/bin/sh" "-c" "infra-tailer & fluent-bit -c /assets/fluent-bit.conf" ];
    ExposedPorts = { "8080/tcp" = {}; }; # health port
    Healthcheck = {
      Test = [ "CMD" "curl" "-f" "http://localhost:8080/status" ];
      Interval = "30s"; Timeout = "3s"; Retries = 3;
    };
  };
}
