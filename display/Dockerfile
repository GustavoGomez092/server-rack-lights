# Rack screen for Command Center: a Chromium kiosk drawn straight to the server's DisplayPort.
FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends cage chromium seatd wlr-randr curl ca-certificates \
      fonts-inter fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY run.sh /usr/local/bin/run.sh
ENV WLR_BACKENDS=drm WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 LIBSEAT_BACKEND=seatd \
    XDG_RUNTIME_DIR=/tmp/xdg
CMD ["/usr/local/bin/run.sh"]
