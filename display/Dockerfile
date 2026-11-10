# Rack screen for Command Center: a native GPUI program (rack-screen) drawn straight to the server's DisplayPort
# under cage. No browser: the old Chromium kiosk used ~275 MiB for one display-only page.
FROM rust:1-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
      pkg-config libwayland-dev libxkbcommon-dev libxkbcommon-x11-dev libfontconfig-dev libfreetype-dev \
      libvulkan-dev libx11-dev libx11-xcb-dev libxcb1-dev libzstd-dev cmake g++ \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY screen/ .
# Two jobs keep the build out of swap on the 16 GB server; the caches keep later builds to a few minutes.
ENV CARGO_BUILD_JOBS=2
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/target \
    cargo test --locked --release -p rack-screen-core \
 && cargo build --locked --release -p rack-screen \
 && cp target/release/rack-screen /rack-screen

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends cage seatd wlr-randr curl ca-certificates grim \
      mesa-vulkan-drivers libvulkan1 libwayland-client0 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 \
      libfreetype6 libx11-xcb1 libxcb1 fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /rack-screen /usr/local/bin/rack-screen
COPY run.sh screen-loop.sh /usr/local/bin/
COPY screen/fixtures /fixtures
# gles2, not pixman: GPUI draws with Vulkan and hands cage GPU buffers.
ENV WLR_BACKENDS=drm WLR_RENDERER=gles2 WLR_LIBINPUT_NO_DEVICES=1 LIBSEAT_BACKEND=seatd \
    XDG_RUNTIME_DIR=/tmp/xdg
CMD ["/usr/local/bin/run.sh"]
