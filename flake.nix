{
  description = "Dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    cubiomes = {
      url = "github:Cubitect/cubiomes";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, cubiomes }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs:
        let
          # package cubiomes
          cubiomesLib = pkgs.stdenv.mkDerivation {
            pname = "cubiomes";
            version = "unstable-${builtins.substring 0 7 cubiomes.rev}";
            src = cubiomes;

            makeFlags = [ "release" ];

            installPhase = ''
              runHook preInstall
              mkdir -p $out/lib $out/include/cubiomes
              cp libcubiomes.a $out/lib/
              cp *.h $out/include/cubiomes/
              runHook postInstall
            '';

            meta.description = "Minecraft biome and terrain generation library in C";
          };

          python = pkgs.python3.withPackages (ps: with ps; [
            flask
            pandas
            plotly
            numpy
            scikit-learn
          ]);

          # convenience
          build-collector = pkgs.writeShellScriptBin "build-collector" ''
            set -euo pipefail

            src="Minecraft Data Collector.cpp"
            if [ ! -f "$src" ]; then
              echo "error: '$src' not found" >&2
              exit 1
            fi

            mkdir -p build
            echo "Building build/spawn_biomes ..."
            g++ -std=c++17 -O3 -fwrapv -fopenmp \
              -I${cubiomesLib}/include/cubiomes \
              "$src" \
              -L${cubiomesLib}/lib -lcubiomes -lm \
              -o build/spawn_biomes
            echo "Done: build/spawn_biomes"
          '';
        in
        {
          default = (pkgs.mkShell.override { stdenv = pkgs.gccStdenv; }) {
            packages = [
              python
              pkgs.sqlite
              build-collector
            ];

            CUBIOMES_INCLUDE_DIR = "${cubiomesLib}/include/cubiomes";
            CUBIOMES_LIB_DIR = "${cubiomesLib}/lib";
          };
        });
    };
}
