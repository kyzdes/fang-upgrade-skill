# Образ для ofgate: всё, что нужно, чтобы собрать ВСЮ рабочую область форка,
# включая openfang-desktop (Tauri 2).
#
# Состав выяснен прогоном, а не списком из документации:
#   1. rust:1.91-slim-bookworm + то, что ставил ofcheck-rs (pkg-config libssl-dev
#      perl make) → `cargo check -p openfang-desktop` падает на glib-sys:
#      "The system library `glib-2.0` required by crate `glib-sys` was not found."
#   2. + libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev
#      librsvg2-dev patchelf → тот же cargo check проходит, exit=0 (5m43s с нуля).
# Поэтому исключать openfang-desktop из --workspace не нужно.
#
# Версия rust взята из rust-toolchain.toml форка (channel = "1.91"). Тулчейн с
# ИМЕНЕМ "1.91" ставится отдельной строкой: в базовом образе установлен
# "1.91.1-x86_64-unknown-linux-gnu", а rust-toolchain.toml просит "1.91" — это для
# rustup другое имя, и он скачивал весь тулчейн заново на КАЖДОМ запуске
# (проверено: 19-секундный fmt-прогон, лог которого целиком состоит из
# "info: downloading component ...").
FROM rust:1.91-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      pkg-config libssl-dev perl make \
      libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf \
 && rm -rf /var/lib/apt/lists/*

RUN rustup toolchain install 1.91 --profile minimal --component rustfmt --component clippy \
 && rustup default 1.91
