
# PE16 Basys 3 - Initial Synthesis
# Target: Xilinx Artix-7 XC7A35T

set script_dir [file dirname [file normalize [info script]]]
set rtl_dir [file join $script_dir rtl]
set build_dir [file join $script_dir build]

# Create Vivado project
create_project pe16_basys3 $build_dir \
    -part xc7a35tcpg236-1 -force

# Add existing PE16 RTL
add_files [list \
    [file join $rtl_dir project.v] \
    [file join $rtl_dir pe_core.v] \
    [file join $rtl_dir pe_imem.v] \
]

# Use 256x16 behavioral instruction memory
# instead of the IHP SRAM macro.
set_property verilog_define {PE_BEHAV_IMEM} \
    [current_fileset]

# Top-level module (4 engines by default)
set_property top tt_um_pe16 [current_fileset]

update_compile_order -fileset sources_1

# Run synthesis
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Check that synthesis actually succeeded
set synth_status [get_property STATUS [get_runs synth_1]]
puts "Synthesis status: $synth_status"

if {![string match "*Complete*" $synth_status]} {
    error "Synthesis failed: $synth_status"
}

# Open synthesized design
open_run synth_1

# Generate reports
report_utilization -file \
    [file join $script_dir utilization.rpt]

report_utilization -hierarchical -file \
    [file join $script_dir utilization_hierarchical.rpt]

puts "PE16 synthesis completed successfully."
