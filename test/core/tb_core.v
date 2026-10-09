`default_nettype none
`timescale 1ns/1ps
module tb_core;
  reg clk = 0, rst_n = 0, run = 0;
  reg [15:0] poly = 0; reg [3:0] crcw = 15; reg osr_lsb = 1, isr_lsb = 1;
  reg [2:0] stuff_n = 0; reg stuff_ones = 0; reg nrzi = 0, diff = 0;
  reg we = 0; reg [7:0] waddr = 0; reg [15:0] wdata = 0;
  reg host_push = 0; reg [15:0] host_wdata = 0; reg host_pop = 0;
  reg [7:0] pin_in = 0;
  wire [7:0] core_addr; wire [15:0] q;
  wire [15:0] host_rdata; wire [2:0] txc, rxc; wire [7:0] pout, poe, pc; wire halt, ov;
  pe_imem m (.clk(clk), .we(we), .addr(we ? waddr : core_addr), .wdata(wdata), .rdata(q));
  pe_core u (.clk(clk), .rst_n(rst_n), .srst(1'b0), .run(run), .cfg_poly(poly), .cfg_crcw(crcw),
    .cfg_osr_lsb(osr_lsb), .cfg_isr_lsb(isr_lsb), .cfg_stuff_n(stuff_n), .cfg_stuff_ones(stuff_ones), .cfg_nrzi(nrzi), .cfg_diff(diff),
    .imem_addr(core_addr), .imem_data(q), .host_push(host_push), .host_wdata(host_wdata),
    .host_pop(host_pop), .host_rdata(host_rdata), .tx_count(txc), .rx_count(rxc),
    .pin_in(pin_in), .pout(pout), .poe(poe), .pc(pc), .halt(halt), .ov(ov));
endmodule
