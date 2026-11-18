
# PE16 - Basys 3 full FPGA build
# Vivado 2026.1
# 4 engines, 256x16 RAM each, 48 MHz

set script_dir \
    [file dirname [file normalize [info script]]]

set rtl_dir [file join $script_dir rtl]
set build_dir [file join $script_dir build_board]
set report_dir [file join $script_dir reports]

file mkdir $report_dir

# ---------------------------------------
# Create project
# ---------------------------------------
create_project pe16_basys3_board \
    $build_dir \
    -part xc7a35tcpg236-1 \
    -force

set_property target_language Verilog \
    [current_project]

# ---------------------------------------
# RTL sources
# ---------------------------------------
add_files [list \
    [file join $rtl_dir project.v] \
    [file join $rtl_dir pe_core.v] \
    [file join $rtl_dir pe_imem.v] \
    [file join $script_dir pe16_basys3_top.sv] \
]

set_property file_type SystemVerilog \
    [get_files [file join $script_dir pe16_basys3_top.sv]]

# Use the existing 256x16 behavioral RAM.
# No IHP SRAM macros required.
set_property verilog_define {PE_BEHAV_IMEM} \
    [get_filesets sources_1]

set_property top pe16_basys3_top \
    [get_filesets sources_1]

# ---------------------------------------
# 48 MHz Clocking Wizard
# ---------------------------------------
create_ip \
    -name clk_wiz \
    -vendor xilinx.com \
    -library ip \
    -module_name clk_wiz_0

set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {48.000} \
    CONFIG.USE_RESET {false} \
    CONFIG.USE_LOCKED {true} \
] [get_ips clk_wiz_0]

generate_target all [get_ips clk_wiz_0]

# ---------------------------------------
# Physical constraints
# ---------------------------------------
add_files -fileset constrs_1 \
    [file join $script_dir basys3.xdc]

update_compile_order -fileset sources_1

# ---------------------------------------
# Synthesis
# ---------------------------------------
puts "=== Starting synthesis ==="

launch_runs synth_1 -jobs 4
wait_on_run synth_1

set status [get_property STATUS [get_runs synth_1]]
puts "Synthesis status: $status"

if {![string match "*Complete*" $status]} {
    error "Synthesis failed: $status"
}

open_run synth_1

report_utilization -file \
    [file join $report_dir utilization_synth.rpt]

close_design

# ---------------------------------------
# Implementation and bitstream
# ---------------------------------------
puts "=== Starting implementation ==="

launch_runs impl_1 \
    -to_step write_bitstream \
    -jobs 4

wait_on_run impl_1

set status [get_property STATUS [get_runs impl_1]]
puts "Implementation status: $status"

if {![string match "*Complete*" $status]} {
    error "Implementation failed: $status"
}

open_run impl_1

report_utilization -file \
    [file join $report_dir utilization_impl.rpt]

report_timing_summary -file \
    [file join $report_dir timing_summary.rpt]

report_drc -file \
    [file join $report_dir drc.rpt]

# ---------------------------------------
# Copy bitstream
# ---------------------------------------
set bitfiles [glob -nocomplain \
    [file join $build_dir \
        pe16_basys3_board.runs \
        impl_1 \
        *.bit]]

if {[llength $bitfiles] == 0} {
    error "No bitstream found"
}

file copy -force \
    [lindex $bitfiles 0] \
    [file join $script_dir pe16_basys3.bit]

puts "===================================="
puts "PE16 FPGA build completed"
puts "Bitstream: $script_dir/pe16_basys3.bit"
puts "Reports:   $report_dir"
puts "===================================="
