#!/bin/bash
# Builds the Tredot firmware and optionally installs it on the module.
#   ./firmware.sh          compile into build/ (mac/build_app.py then bundles it into Tredot.app,
#                          for "Update firmware" in the menu)
#   ./firmware.sh update   compile and update through the bridge, over Wi-Fi or USB (needs pairing)
#   ./firmware.sh flash    compile and write over USB with esptool: for the first install, or when
#                          the firmware on the module no longer starts
# Needs arduino-cli with ESP32 support (esp32:esp32 3.3.x) and the libraries
# "GFX Library for Arduino", "U8g2" and "JPEGDEC" (viewfinder).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi"  # the camera needs PSRAM

# Arduino's ctags only ships for Intel Macs: tools/ctags is an empty stub, so the firmware declares
# every function before using it.
# The app slots come from firmware/tredot/partitions.csv (3.94 MB); the stock limit is 3.3 MB.
arduino-cli compile --fqbn "$FQBN" --output-dir "$DIR/build" \
  --build-property "tools.ctags.path=$DIR/tools" \
  --build-property "upload.maximum_size=4128768" "$DIR/firmware/tredot"

if [ "${1:-}" = "update" ]; then
  exec "$DIR/mac/.venv/bin/python" "$DIR/mac/update_firmware.py" "$DIR/build/tredot.ino.bin"
fi
[ "${1:-}" = "flash" ] || exit 0

PORT="${TREDOT_PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -n "$PORT" ] || { echo "Module not found: connect it with the USB cable." >&2; exit 1; }
ESPTOOL="$(ls -d "$HOME"/Library/Arduino15/packages/esp32/tools/esptool_py/*/esptool | tail -1)"
# While this file exists the bridge leaves the USB port alone: Claude's hooks restart the bridge
# on every event, and it would grab the port in the middle of flashing. The running one is stopped
# below.
CACHE="$("$DIR/mac/.venv/bin/python" "$DIR/mac/config.py" get cache_dir)"
mkdir -p "$CACHE" && touch "$CACHE/flashing"
trap 'rm -f "$CACHE/flashing"' EXIT
pkill -f "mac/bridge.py" && sleep 1 || true
# The first attempt sometimes fails ("Invalid head of packet") if the module is writing to serial
# while esptool resets it: retry once.
# Not the full 8 MB image: it would erase the NVS, i.e. the pairing with the Mac. Only bootloader,
# partition table, slot selection (otadata) and app.
BOOT_APP0="$(ls "$HOME"/Library/Arduino15/packages/esp32/hardware/esp32/*/tools/partitions/boot_app0.bin | tail -1)"
for try in 1 2; do
  "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 write-flash \
    0x0 "$DIR/build/tredot.ino.bootloader.bin" \
    0x8000 "$DIR/build/tredot.ino.partitions.bin" \
    0xe000 "$BOOT_APP0" \
    0x10000 "$DIR/build/tredot.ino.bin" && exit 0
  [ "$try" = 1 ] && echo "Retrying…" >&2 && sleep 2
done
exit 1
