// Instruction store: 256x16 synchronous-read RAM.
// Synthesis/GL: IHP 256x16 SRAM macro. RTL simulation without the PDK: behavioural model
// with the same read timing (data valid the cycle after the address is presented).
`default_nettype none
module pe_imem (
  input  wire        clk,
  input  wire        we,
  input  wire [7:0]  addr,
  input  wire [15:0] wdata,
  output wire [15:0] rdata
);
`ifdef PE_FLOP_IMEM
  // Standard-cell fallback when the SRAM macro cannot be powered in the TT flow.
  // 128 words; address bit 7 is ignored (programs must fit in 128 words).
  reg [15:0] m [0:127];
  reg [15:0] q;
  always @(posedge clk) begin
    if (we) m[addr[6:0]] <= wdata;
    q <= we ? wdata : m[addr[6:0]];
  end
  assign rdata = q;
`elsif PE_BEHAV_IMEM
  reg [15:0] m [0:255];
  reg [15:0] q;
  always @(posedge clk) begin
    if (we) m[addr] <= wdata;
    q <= we ? wdata : m[addr];   // write-through, as the macro does with REN=1
  end
  assign rdata = q;
`else
  RM_IHPSG13_1P_256x16_c2_bm_bist u_sram (
    .A_CLK(clk), .A_MEN(1'b1), .A_WEN(we), .A_REN(1'b1), .A_ADDR(addr),
    .A_DIN(wdata), .A_DLY(1'b1), .A_DOUT(rdata), .A_BM(16'hFFFF),
    .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0),
    .A_BIST_REN(1'b0), .A_BIST_ADDR(8'd0), .A_BIST_DIN(16'd0), .A_BIST_BM(16'd0));
`endif
endmodule
