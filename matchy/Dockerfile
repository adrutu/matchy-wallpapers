# Matchy Wallpapers — Home Assistant app image.
#
# BUILD_FROM is no longer injected by the Supervisor (removed in 2026.04.0) unless a
# deprecated build.yaml is present, so the base is pinned here as an ARG default and can
# still be overridden by a build arg. Verified multi-arch (amd64 + arm64) on 2026-10-13; built natively
# on both on 2026-10-14 with raqm on and byte-identical label renders (tests/label_diff.py).
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie-2026.10.0@sha256:a7f262f331e2a4f4e3e02173da932a8d8c26f3d5758a5f59903d46de1f728e5b

# Pull four Noto faces out of Debian's fonts-noto-core instead of shipping the whole pack.
FROM ${BUILD_FROM} AS fonts
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core \
 && mkdir /out \
 && cp /usr/share/fonts/truetype/noto/NotoSerifDisplay-Italic.ttf \
       /usr/share/fonts/truetype/noto/NotoSerifDisplay-Regular.ttf \
       /usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf \
       /usr/share/fonts/truetype/noto/NotoSans-Regular.ttf /out/ \
 && cp /usr/share/doc/fonts-noto-core/copyright /out/COPYRIGHT-fonts-noto-core

FROM ${BUILD_FROM}

# python3-pil from the Debian archive; libraqm0 is what gives the gallery label true small caps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pil libraqm0 \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

COPY --from=fonts /out/ /fonts/
COPY app/ /app/
COPY run.sh /
RUN chmod a+x /run.sh

# PYTHONDONTWRITEBYTECODE: the engine imports its own modules from /app, which would
# otherwise leave __pycache__ in the image layer at runtime.
ENV WE_FONT_DIR=/fonts \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL \
    io.hass.type="app" \
    io.hass.name="Matchy Wallpapers" \
    io.hass.description="One wallpaper, matched across every screen." \
    io.hass.url="https://github.com/adrutu/matchy" \
    org.opencontainers.image.title="Home Assistant App: Matchy Wallpapers" \
    org.opencontainers.image.description="One wallpaper, matched across every screen." \
    org.opencontainers.image.source="https://github.com/adrutu/matchy" \
    org.opencontainers.image.licenses="MIT"

# Health for the Supervisor (in place of config.yaml's obsolete `watchdog:` URL): with the app's
# Watchdog toggle on, a container Docker marks unhealthy is restarted. /healthz answers during a
# cold scan, because the engine binds before it scans.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS --max-time 8 -o /dev/null http://127.0.0.1:8160/healthz || exit 1

CMD [ "/run.sh" ]
