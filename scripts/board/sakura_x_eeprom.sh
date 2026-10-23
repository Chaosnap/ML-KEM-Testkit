#!/usr/bin/env bash
# One-click SAKURA-X FT2232H EEPROM tool (Linux). Close the GUI first.
#
#   bash scripts/board/sakura_x_eeprom.sh            # fix: Channel A = 245 FIFO, Channel B = UART
#   bash scripts/board/sakura_x_eeprom.sh restore    # write the original backup back
#   bash scripts/board/sakura_x_eeprom.sh check      # read only
#
# The first run installs pyftdi into ~/ftdi-venv (SAKURA_EEPROM_VENV to change).
# The script needs root for the USB device and asks for the sudo password.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
venv=${SAKURA_EEPROM_VENV:-$HOME/ftdi-venv}

if ! "$venv/bin/python" -c "import pyftdi" 2>/dev/null; then
    echo "Installing pyftdi into $venv (one time) ..."
    if ! python3 -m venv "$venv"; then
        echo "STOP: python3 cannot make a venv. Run: sudo apt install python3-venv" >&2
        exit 1
    fi
    "$venv/bin/pip" install --quiet pyftdi
fi

if [ "$(id -u)" -eq 0 ]; then
    exec "$venv/bin/python" "$here/sakura_x_eeprom.py" "$@"
fi
exec sudo "$venv/bin/python" "$here/sakura_x_eeprom.py" "$@"
