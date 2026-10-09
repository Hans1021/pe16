SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR ?= $(PWD)/../../src
VERILOG_SOURCES = $(SRC_DIR)/pe_core.v $(SRC_DIR)/pe_imem.v $(PWD)/tb_core.v
COMPILE_ARGS += -DPE_BEHAV_IMEM
TOPLEVEL = tb_core
COCOTB_TEST_MODULES = test_diff
include $(shell cocotb-config --makefiles)/Makefile.sim
