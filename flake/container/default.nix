# flake/container/default.nix
{ pkgs, infra-watcher, nix2container }:

# nix2container is the _package set_ that exposes `buildImage`
nix2container.buildImage {
  name = "infra-watcher";
  tag  = "0.1";

  fromImage   = pkgs.dockerTools.baseImage;        # scratch-like layer
  copyToRoot  = [ infra-watcher ./../assets ];     # agent + config
  config = {
    Cmd          = [ "/bin/sh" "-c"
                     "infra-tailer & fluent-bit -c /assets/fluent-bit.conf" ];
    ExposedPorts = { "8080/tcp" = {}; };
  };
}
