# Keep flake.lock committed before any reproducible deployment.
ARG NIX_IMAGE=nixos/nix:2.35.2
FROM ${NIX_IMAGE} AS build
WORKDIR /src
COPY . .
RUN test -s flake.lock || (echo "Generate and commit flake.lock using nix flake lock" >&2; exit 1)
RUN nix --extra-experimental-features 'nix-command flakes' build --option sandbox false --no-write-lock-file .#runtime --out-link /tmp/result \
 && mkdir -p /stage/nix/store /stage/bin /stage/etc \
 && for p in $(nix-store --query --requisites "$(readlink -f /tmp/result)"); do cp -a "$p" /stage/nix/store/; done \
 && ln -s "$(readlink -f /tmp/result)/bin/livingd" /stage/bin/livingd \
 && printf 'hosts: files dns\n' > /stage/etc/nsswitch.conf \
 && printf 'living:x:10001:10001:living:/nonexistent:/sbin/nologin\n' > /stage/etc/passwd \
 && printf 'living:x:10001:\n' > /stage/etc/group
FROM scratch
COPY --from=build /stage/ /
USER 10001:10001
ENV LIVING_LISTEN=0.0.0.0:8080 LIVING_DEV_MODE=1 LIVING_MIGRATE_ON_BOOT=1
EXPOSE 8080
ENTRYPOINT ["/bin/livingd"]
