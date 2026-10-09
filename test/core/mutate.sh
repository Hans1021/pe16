#!/bin/bash
# Mutation check: each mutant of pe_core.v must be killed by the differential test.
cd "$(dirname "$0")"
# Mutants are applied to a private copy of src/, never to the source tree, so an interrupted
# run cannot leave a mutant behind.
WORK=$(mktemp -d); cp -r ../../src/. $WORK/; export SRC_DIR=$WORK
SRC=$WORK/pe_core.v; cp $SRC $WORK/pe_core.orig
trap 'rm -rf $WORK' EXIT
declare -a M=(
 's/v10\[5\]) since <= 16.d0/v10[4]) since <= 16'"'"'d0/'
 's/| ~(16.hFFFF << aln_sh)//'
 's/(runl == 3.d7) ? 3.d7/(runl == 3'"'"'d6) ? 3'"'"'d6/'
 's/(runl >= cfg_stuff_n)/(runl > cfg_stuff_n)/'
 's/since1 < {1.b0, dly_k}/since1 <= {1'"'"'b0, dly_k}/'
 's/fb = c\[w\] ^ d;/fb = c[w];/'
 's/3.d2: jtake = (x != 0);/3'"'"'d2: jtake = (x != 16'"'"'d1);/'
 's/(rxc == 3.d4)/(rxc == 3'"'"'d3)/'
 's/(cfg_stuff_ones ? 1.b0 : ~last) : osr_bit/(~last) : osr_bit/'
 's/wire        pin_bit = cfg_nrzi ? ~(pin_raw ^ rxl) : pin_raw;/wire        pin_bit = cfg_nrzi ? (pin_raw ^ rxl) : pin_raw;/'
 's/if (cfg_diff) pout\[pi1\] <= ~out_line;/if (cfg_diff) pout[pi1] <= out_line;/'
 's/mov_val\[ri\] = ins\[4\] ? mov_aln\[15-ri\]/mov_val[ri] = ins[4] ? mov_aln[ri]/'
 's/? (x != y) :/? (x == y) :/'
 's/(f3a == 3.d1) ? stuff_due :/(f3a == 3'"'"'d1) ? 1'"'"'b0 :/'
 's/(f3a == 3.d2) ? (txc == 3.d0)/(f3a == 3'"'"'d2) ? (txc == 3'"'"'d1)/'
 's/if (mm == 2.d3) b <= pin_raw; /if (mm == 2'"'"'d3) b <= pin_bit; /'
 's/if (since1 > {1.b0, dly_k}) ov <= 1.b1;/if (since1 > {1'"'"'b0, dly_k} + 17'"'"'d1) ov <= 1'"'"'b1;/'
 's/3.d4: crc <= mov_val \& cmask;/3'"'"'d4: crc <= mov_val;/'
 's/if (v10\[1\]) crc <= cmask; else if (v10\[0\]) crc <= 16.d0;/if (v10[0]) crc <= 16'"'"'d0; else if (v10[1]) crc <= cmask;/'
 's/isr_sh  = cfg_isr_lsb ? {pin_bit, isr\[15:1\]}/isr_sh  = cfg_isr_lsb ? {isr[0], isr[15:1]}/'
)
# baseline: the unmutated RTL must pass with full coverage at this seed budget,
# otherwise a surviving mutant could just be an unexercised feature
rm -rf sim_build results.xml
NSEEDS=60 make -s >/tmp/mut_base.log 2>&1
grep "coverage:" /tmp/mut_base.log | sed 's/.*coverage/baseline coverage/'
grep -q "FAIL=0" /tmp/mut_base.log || { echo "baseline failed at NSEEDS=60"; exit 1; }
k=0; killed=0
for m in "${M[@]}"; do
  k=$((k+1)); cp $WORK/pe_core.orig $SRC; sed -i "$m" $SRC
  if cmp -s $SRC $WORK/pe_core.orig; then echo "mutant $k: SED DID NOT APPLY"; continue; fi
  rm -rf sim_build results.xml
  NSEEDS=60 make -s >/tmp/mut_$k.log 2>&1
  # killed only by a state mismatch, never by the coverage gate
  if grep -q "mismatch (rtl" /tmp/mut_$k.log; then echo "mutant $k: killed"; killed=$((killed+1)); else echo "mutant $k: SURVIVED"; fi
done
cp $WORK/pe_core.orig $SRC; rm -rf sim_build results.xml
echo "killed $killed / $k"
