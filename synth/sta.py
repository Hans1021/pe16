"""Pre-layout static timing (OpenSTA via the openroad Python package). No wire parasitics,
so results are optimistic; place-and-route timing in the TT flow is the real check."""
import sys
from openroad import Tech, Design
corner = sys.argv[1] if len(sys.argv) > 1 else "slow"
period = float(sys.argv[2]) if len(sys.argv) > 2 else 20.833
L = "/home/claude/ihp5/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib"
S = "/home/claude/ihp5/ihp-sg13g2/libs.ref/sg13g2_sram/lib"
libs = {"slow": ("sg13cmos5l_stdcell_slow_1p08V_125C.lib", "RM_IHPSG13_1P_256x16_c2_bm_bist_slow_1p08V_125C.lib"),
        "typ": ("sg13cmos5l_stdcell_typ_1p20V_25C.lib", "RM_IHPSG13_1P_256x16_c2_bm_bist_typ_1p20V_25C.lib")}[corner]
tech = Tech()
LEF = "/home/claude/ihp5/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lef"
tech.readLef(f"{LEF}/sg13cmos5l_tech.lef")
tech.readLef(f"{LEF}/sg13cmos5l_stdcell.lef")
tech.readLef("/home/claude/ihp5/ihp-sg13g2/libs.ref/sg13g2_sram/lef/RM_IHPSG13_1P_256x16_c2_bm_bist.lef")
tech.readLiberty(f"{L}/{libs[0]}")
tech.readLiberty(f"{S}/{libs[1]}")
d = Design(tech)
d.readVerilog(f"synth/pe16_n4_{corner}.v")
d.link("tt_um_pe16")
tcl = f"""
create_clock -name clk -period {period} [get_ports clk]
set_input_delay 0 -clock clk [delete_from_list [all_inputs] [get_ports clk]]
set_output_delay 0 -clock clk [all_outputs]
set_load 0.02 [all_outputs]
set_input_transition 0.2 [all_inputs]
report_checks -path_delay max -to [get_pins -hierarchical *u_sram/A_ADDR*] -group_path_count 1 -digits 2
report_checks -path_delay min -group_path_count 1 -digits 2
report_check_types -max_slew -max_capacitance -max_fanout -violators
report_worst_slack -max -digits 2
report_worst_slack -min -digits 2
report_tns -digits 2
"""
print(d.evalTclString(tcl))
