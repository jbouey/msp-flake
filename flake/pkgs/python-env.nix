{ pkgs }:
pkgs.python311.withPackages (ps: with ps; [ requests fastapi uvicorn ])
