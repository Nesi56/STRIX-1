#!/usr/bin/env bash
# Report power / throttling state (Pi 5). Run under load too (e.g. while llama-server answers).
set -uo pipefail
if ! command -v vcgencmd >/dev/null; then echo "vcgencmd not found"; exit 1; fi
echo "== get_throttled (0x0 = OK; bit0 under-voltage now, bit16 under-voltage occurred)"
vcgencmd get_throttled
echo "== 5V input and PMIC rails"
vcgencmd pmic_read_adc EXT5V_V 2>/dev/null || vcgencmd pmic_read_adc | grep -i ext5v || echo "pmic_read_adc unsupported"
echo "== USB current limit configuration (Pi 5 needs a 5 A PD source to lift the 600 mA USB limit)"
vcgencmd get_config usb_max_current_enable 2>/dev/null || true
echo "== SoC temperature"
vcgencmd measure_temp
