#!/bin/bash
# Full regression; writes logs/verify.log. Run from the repository root.
cd "$(dirname "$0")"
md5sum src/*.v fw/*.pe > logs/src.md5
export PATH=/home/claude/lean-4.15.0-linux/bin:$PATH
PDK=${PDK_ROOT:-/home/claude/ihp5}
LIB=$PDK/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib
{
for cfg in "" "SRAM_MODEL=yes PDK_ROOT=$PDK" "IMEM=flop"; do
  rm -rf test/sim_build; make -s -C test $cfg > /tmp/s.log 2>&1
  echo "== protocol suite [${cfg:-behavioural SRAM}] $(grep -o 'NENG = [0-9]' /tmp/s.log)"
  grep -E "\*\* test|TESTS=" /tmp/s.log | sed 's/  */ /g'
  grep -E "INFO.*(SCL|MDC|1-Wire low|WS2812|DMX|LIN|Manchester|engines ran)" /tmp/s.log | sed 's/  */ /g'
done
echo "== firmware mutation"; test/fw_mutate.sh
echo "== core differential"; rm -rf test/core/sim_build; make -s -C test/core 2>&1 | grep -E "coverage|TESTS"
echo "== RTL mutation"; test/core/mutate.sh
echo "== lean"; (cd lean && rm -rf .lake/build && lake build 2>&1 | tail -1 && lake env lean Axioms.lean && python3 gen_traces.py 500 600 | .lake/build/bin/crosscheck)
echo "== synthesis (sg13cmos5l typ)"
for v in "SRAM 4" "FLOP 2"; do set -- $v
  D=""; [ $1 = FLOP ] && D="-DPE_FLOP_IMEM"; BB=""; [ $1 = SRAM ] && BB="synth/sram_bb.v"
  yowasp-yosys -q -p "read_verilog -sv $D $BB src/pe_imem.v src/pe_core.v src/project.v; chparam -set NENG $2 tt_um_pe16; synth -flatten -top tt_um_pe16; dfflibmap -liberty $LIB; abc -liberty $LIB -D 20000; opt_clean; tee -o synth/$1_n$2.stat stat -liberty $LIB" >/dev/null 2>&1
  echo "$1 NENG=$2 $(grep 'Chip area' synth/$1_n$2.stat)"
done
echo "== source tree unchanged by the run:"; md5sum -c --quiet logs/src.md5 && echo yes
echo "== done"
} > logs/verify.log 2>&1
