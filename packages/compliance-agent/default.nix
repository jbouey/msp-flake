{ lib
, python311Packages
, nix
, systemd
, nftables
, dnsutils
, cryptography
}:

python311Packages.buildPythonApplication rec {
  pname = "compliance-agent";
  version = "0.1.0";

  src = ./.;

  propagatedBuildInputs = with python311Packages; [
    aiohttp
    cryptography
    pydantic
    pydantic-settings
    fastapi
    uvicorn
    jinja2
  ];

  buildInputs = [
    nix
    systemd
    nftables
    dnsutils
  ];

  # Don't run tests during build (run via checks)
  doCheck = false;

  meta = with lib; {
    description = "MSP Compliance Agent - Self-Healing NixOS Agent";
    homepage = "https://github.com/yourorg/msp-platform";
    license = licenses.mit;
    platforms = platforms.linux;
  };
}
