{
  description = "Проект сайта первичного отделения района Печатники";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
  };

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";

      pkgs = import nixpkgs {
        inherit system;
      };
    in
    {
      devShells."${system}".default = pkgs.mkShell {
        packages = with pkgs; [
          uv
          python313
        ];

        shellHook = ''
          echo "Python: $(${pkgs.python313}/bin/python3 --version)"
          echo "UV: $(${pkgs.uv}/bin/uv --version)"

          if [[ -z "''${FISH_VERSION:-}" && -t 1 ]]; then
            exec ${pkgs.fish}/bin/fish
          fi
        '';
      };
    };
}
