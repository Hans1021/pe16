
# PE16 - Digilent Basys 3
# FPGA: XC7A35T-1CPG236C
# I/O voltage: 3.3 V

# ---------------------------------------
# 100 MHz onboard clock
# ---------------------------------------
set_property PACKAGE_PIN W5 [get_ports clk100]
set_property IOSTANDARD LVCMOS33 [get_ports clk100]

# create_clock -name sys_clk100 \
#    -period 10.000 [get_ports clk100]

# ---------------------------------------
# Center pushbutton - Reset
# ---------------------------------------
set_property PACKAGE_PIN U18 [get_ports btnC]
set_property IOSTANDARD LVCMOS33 [get_ports btnC]

# ---------------------------------------
# Onboard LEDs
# ---------------------------------------
set_property PACKAGE_PIN U16 [get_ports {led[0]}]
set_property PACKAGE_PIN E19 [get_ports {led[1]}]
set_property PACKAGE_PIN U19 [get_ports {led[2]}]
set_property PACKAGE_PIN V19 [get_ports {led[3]}]
set_property PACKAGE_PIN W18 [get_ports {led[4]}]

set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]

# ---------------------------------------
# JA - Shared protocol I/O
#
# pe_io[0] = JA1
# pe_io[1] = JA2
# pe_io[2] = JA3
# pe_io[3] = JA4
# pe_io[4] = JA7
# pe_io[5] = JA8
# pe_io[6] = JA9
# pe_io[7] = JA10
# ---------------------------------------
set_property PACKAGE_PIN J1 [get_ports {pe_io[0]}]
set_property PACKAGE_PIN L2 [get_ports {pe_io[1]}]
set_property PACKAGE_PIN J2 [get_ports {pe_io[2]}]
set_property PACKAGE_PIN G2 [get_ports {pe_io[3]}]
set_property PACKAGE_PIN H1 [get_ports {pe_io[4]}]
set_property PACKAGE_PIN K2 [get_ports {pe_io[5]}]
set_property PACKAGE_PIN H2 [get_ports {pe_io[6]}]
set_property PACKAGE_PIN G3 [get_ports {pe_io[7]}]

set_property IOSTANDARD LVCMOS33 [get_ports {pe_io[*]}]

# ---------------------------------------
# JB - Host SPI
#
# JB1 = SCK  (input)
# JB2 = MOSI (input)
# JB3 = MISO (output)
# JB4 = CS_N (input)
# ---------------------------------------
set_property PACKAGE_PIN A14 [get_ports spi_sck]
set_property PACKAGE_PIN A16 [get_ports spi_mosi]
set_property PACKAGE_PIN B15 [get_ports spi_miso]
set_property PACKAGE_PIN B16 [get_ports spi_cs_n]

set_property IOSTANDARD LVCMOS33 \
    [get_ports {spi_sck spi_mosi spi_miso spi_cs_n}]

# Default deselected level if SPI master is absent
set_property PULLUP true [get_ports spi_cs_n]

# ---------------------------------------
# Asynchronous inputs
# ---------------------------------------
# SPI and protocol inputs are synchronized by
# the PE16 RTL. Do not treat their external
# arrival as synchronous to the FPGA clock.

set_false_path -from \
    [get_ports {spi_sck spi_mosi spi_cs_n pe_io[*]}]

# Button reset is asynchronously asserted.
set_false_path -from [get_ports btnC]
