{
  description = "Living Intelligence: deterministic PostgreSQL graph-engine vertical slice";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      py = pkgs.python3.withPackages (ps: [ ps.psycopg ]);
      livingd = pkgs.stdenvNoCC.mkDerivation {
        pname = "livingd";
        version = "0.28.0";
        src = self;
        dontBuild = true;
        installPhase = ''
          mkdir -p "$out/lib/livingd" "$out/share/living/migrations" "$out/bin"
          cp -r core/livingd/livingd/. "$out/lib/livingd/"
          cp db/migrations/*.sql "$out/share/living/migrations/"
          cat > "$out/bin/livingd" <<EOF
          #!${pkgs.runtimeShell}
          export PYTHONPATH="$out/lib"
          export LIVING_MIGRATIONS_DIR="$out/share/living/migrations"
          exec ${py}/bin/python -m livingd "\$@"
          EOF
          chmod +x "$out/bin/livingd"
        '';
      };
      runtime = pkgs.symlinkJoin {
        name = "living-runtime";
        paths = [ livingd py pkgs.cacert ];
      };
    in {
      packages.${system} = {
        inherit livingd runtime;
        default = livingd;
        coreImage = pkgs.dockerTools.buildLayeredImage {
          name = "living-core";
          tag = "v0.28-dev";
          contents = [ runtime ];
          config = {
            User = "10001:10001";
            Entrypoint = [ "${livingd}/bin/livingd" ];
            ExposedPorts = { "8080/tcp" = {}; };
            Env = [ "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt" ];
          };
        };
      };
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [ py postgresql_16 docker docker-compose ];
      };
    };
}
