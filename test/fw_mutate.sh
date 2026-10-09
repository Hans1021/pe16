#!/bin/bash
# Firmware fault injection: each mutant must make its protocol test fail.
cd "$(dirname "$0")"
# Mutants are applied to a private copy of fw/, never to the source tree.
WORK=$(mktemp -d); cp -r ../fw/. $WORK/; export PE_FW_DIR=$WORK
trap 'rm -rf $WORK' EXIT
M=(
 "uart_tx|        set pin 0 1          ; stop bit|        set pin 0 0          ; stop bit|uart_tx$"
 "uart_tx|bit:    dly 16|bit:    dly 17|uart_tx_period"
 "uart_rx|        in raw 1|        in raw 0|uart_rx$"
 "uart_rx|        dly {T // 2 - L}      ; half bit|        dly 1      ; half bit|uart_rx$"
 "uart_rx|        dly {T // 2 - L}      ; half bit|        dly {T // 2}      ; half bit|uart_rx$"
 "spi_ctl|        set pin 2 1          ; SCK rise\n        in raw 4|        in raw 4\n        set pin 2 1          ; SCK rise|spi_controller"
 "spi_ctl|        out raw 3            ; MOSI|        out raw 2            ; MOSI|spi_controller"
 "can_tx|        jmp s bit            ; stuff bit|        nop                  ; stuff bit|can_tx"
 "can_tx|crcf:   mov osr crc^|crcf:   mov osr crc|can_tx"
 "i2c_ctl|set oe 1 1           ; ACK|set oe 1 0           ; ACK|i2c_controller"
 "i2c_ctl|rb:     dly {3 * Q}|rb:     dly {2 * Q}|i2c_controller"
 "i2c_tgt|set y 0x42|set y 0x43|i2c_target"
 "spi_tgt|        in raw 3|        in raw 4|spi_target"
 "jtag|bit:    out raw 1\n        out raw 2|bit:    out raw 2\n        out raw 1|jtag_idcode"
 "swd|        set x 7\nrq:|        set x 6\nrq:|swd_dpidr"
 "ps2_rx|        set x 10|        set x 9|ps2_receive"
 "onewire|        dly {us(7)}           ; sample|        dly {us(40)}          ; sample|onewire"
 "onewire|        set ctl sync         ; time the low from the falling edge\n||onewire"
 "ws2812|        dly {H0}|        dly {H0 + 2}|ws2812"
 "dmx|        dly {us(45)}\n        dly {us(45)}|        dly {us(40)}\n        dly {us(45)}|dmx512"
 "lin|        set x 11|        set x 10|lin"
 "mdio|        set x 31|        set x 29|mdio"
 "can_rx|        set pin 6 0          ; ACK slot dominant|        set pin 6 1|can_rx"
 "usb_tx|        mov osr ~crc^@|        mov osr crc^@|usb_tx"
 "usb_rx|        jmp x!=y bad|        jmp x0 bad|usb_rx"
 "eth_tx|        set pins 0x02        ; 1: low then high|        set pins 0x01        ; 1: low then high|eth_manchester"
 "modbus_tx|        set pin 0 0          ; even number of ones so far: parity 0|        set pin 0 1          ; even number of ones so far: parity 0|modbus_tx"
 "modbus_tx|        mov osr crc@|        mov osr crc|modbus_tx"
 "modbus_tx|        set x 23\ngap:|        set x 19\ngap:|modbus_tx"
 "modbus_rx|        set x 1\n        jmp fin|        set x 0\n        jmp fin|modbus_rx"
 "modbus_rx|pO:     dly {T}\n        in peek 1\n        jmp b sb\n        jmp ferr|pO:     dly {T}\n        in peek 1\n        jmp b sb\n        jmp sb|modbus_rx"
 "modbus_rx|pE:     dly {T}\n        in peek 1            ; even so far: parity bit must be 0\n        jmp b ferr|pE:     dly {T}\n        in peek 1            ; even so far: parity bit must be 0\n        jmp b sb|modbus_rx"
 "mil1553_tx|parE:   dly {H}\n        set pins 0x01|parE:   dly {H}\n        set pins 0x02|mil1553_tx"
 "mil1553_tx|dsync:  pull\n        dly {H}\n        set pins 0x02|dsync:  pull\n        dly {H}\n        set pins 0x01|mil1553_tx"
 "mil1553_rx|oE:     dly {H}\n        in peek 3            ; 1 needs a negative second half\n        jmp b qE\n        jmp merr|oE:     dly {H}\n        in peek 3            ; 1 needs a negative second half\n        jmp b qE\n        jmp qE|mil1553_rx"
 "mil1553_rx|pO:     in peek 2            ; parity must be 0\n        jmp b perr|pO:     in peek 2            ; parity must be 0\n        jmp b good|mil1553_rx"
 "mil1553_rx|pE:     in peek 2            ; even ones so far: parity must be 1\n        jmp b pE1\n        jmp perr|pE:     in peek 2            ; even ones so far: parity must be 1\n        jmp b pE1\n        jmp pE1|mil1553_rx"
)
k=0; killed=0
for m in "${M[@]}"; do
  IFS='|' read -r f old new t <<< "$m"
  if [ -n "$1" ] && [[ "${t%$}" != $1* ]]; then continue; fi
  k=$((k+1)); cp $WORK/$f.pe $WORK/$f.orig
  python3 - "$f" "$old" "$new" <<'PY'
import sys
f, old, new = sys.argv[1], sys.argv[2].replace("\\n", "\n"), sys.argv[3].replace("\\n", "\n")
import os; p = os.path.join(os.environ["PE_FW_DIR"], f + ".pe"); s = open(p).read()
assert old in s, f"pattern not found in {f}: {old!r}"
open(p, "w").write(s.replace(old, new, 1))
PY
  if [ $? -ne 0 ]; then echo "mutant $k ($f): PATTERN MISSING"; cp $WORK/$f.orig $WORK/$f.pe; continue; fi
  COCOTB_TEST_FILTER=$t timeout 900 make -s > /tmp/fwm_$k.log 2>&1
  if grep -q "FAIL=0" /tmp/fwm_$k.log && grep -q "PASS=1" /tmp/fwm_$k.log; then echo "mutant $k ($f -> $t): SURVIVED"
  elif grep -q "PASS=0 FAIL=1" /tmp/fwm_$k.log; then echo "mutant $k ($f -> $t): killed"; killed=$((killed+1))
  else echo "mutant $k ($f -> $t): NO RESULT"; fi
  cp $WORK/$f.orig $WORK/$f.pe
done
echo "firmware mutants killed $killed / $k"
