# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v pe_core.v pe_imem.v sram_256x16_bb.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
ifeq ($(IMEM),flop)
COMPILE_ARGS    += -DPE_FLOP_IMEM
else ifeq ($(SRAM_MODEL),yes)
# IHP 256x16 SRAM functional model (needs PDK_ROOT)
COMPILE_ARGS += -DFUNCTIONAL -DSIM

VERILOG_SOURCES += $(PWD)/sram_model/RM_IHPSG13_1P_256x16_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/sram_model/RM_IHPSG13_1P_core_behavioral_bm_bist.v
else
COMPILE_ARGS    += -DPE_BEHAV_IMEM
endif

else

# Gate-level simulation
SIM_BUILD = sim_build/gl
COMPILE_ARGS += -DGL_TEST -DFUNCTIONAL -DSIM

PDK_LIBS_REF = $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref

VERILOG_SOURCES += $(PDK_LIBS_REF)/sg13cmos5l_io/verilog/sg13cmos5l_io.v

# IHP user-defined primitives (flip-flops and muxes)
VERILOG_SOURCES += $(wildcard $(PDK_LIBS_REF)/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v)

VERILOG_SOURCES += $(PDK_LIBS_REF)/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v

# SRAM functional models
VERILOG_SOURCES += $(PWD)/sram_model/RM_IHPSG13_1P_256x16_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/sram_model/RM_IHPSG13_1P_core_behavioral_bm_bist.v

# Copied by the Tiny Tapeout GDS workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test,test_protocols,test_multi,test_cps,test_pin_arb,test_cfg_read,test_spi_robust,test_fifo_edge,test_engine_reset

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
