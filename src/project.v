/*
 * PE16 protocol emulator: Tiny Tapeout top.
 * ui_in[0]=SCK ui_in[1]=CS_N ui_in[2]=MOSI (SPI mode 0 host port)
 * uo_out[0]=MISO uo_out[1]=halt(any) uo_out[2]=overrun(any) uo_out[3]=run(any)
 * uio[7:0] = shared protocol pins with configurable per-pin engine ownership
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
`ifdef PE_FLOP_IMEM
`define PE_NENG_DEFAULT 2   // flip-flop instruction store: 4 engines do not fit the 8x4 die
`else
`define PE_NENG_DEFAULT 4
`endif
module tt_um_pe16 #(parameter NENG = `PE_NENG_DEFAULT) (
  input  wire [7:0] ui_in,
  output wire [7:0] uo_out,
  input  wire [7:0] uio_in,
  output wire [7:0] uio_out,
  output wire [7:0] uio_oe,
  input  wire       ena,
  input  wire       clk,
  input  wire       rst_n
);
  // ---------------- input synchronisers ----------------
  reg [2:0] sck_s, cs_s, mosi_s;
  reg [7:0] pin_s1, pin_s2;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin sck_s<=0; cs_s<=3'b111; mosi_s<=0; pin_s1<=0; pin_s2<=0; end
    else begin
      sck_s <= {sck_s[1:0], ui_in[0]}; cs_s <= {cs_s[1:0], ui_in[1]};
      mosi_s <= {mosi_s[1:0], ui_in[2]};
      pin_s1 <= uio_in; pin_s2 <= pin_s1;
    end
  wire s_rise = sck_s[1] & ~sck_s[2];
  wire s_fall = ~sck_s[1] & sck_s[2];
  wire s_sel  = ~cs_s[1];
  wire s_din  = mosi_s[1];

  // ---------------- SPI command engine ----------------
  localparam C_IMEM=6'd1, C_CFG=6'd2, C_TX=6'd3, C_RX=6'd4,
             C_STAT=6'd5, C_CNT=6'd6, C_CTRL=6'd7, C_OWNER=6'd8, C_CFGREAD=6'd9;
  reg [7:0]  cmd;  reg cmd_ok;
  reg [2:0]  bcnt;           // bit within byte
  reg [3:0]  wcnt;           // bit within word
  reg [7:0]  sh8;  reg [15:0] sh16;
  reg        have_addr;
  reg [7:0]  haddr;
  reg [15:0] rd_sh; reg miso;
  wire [1:0] eid  = cmd[7:6];
  wire [5:0] code = cmd[5:0];
  wire needs_addr = (code == C_IMEM) ||
                  (code == C_CFG) ||
                  (code == C_CFGREAD);

  // per-engine strobes
  reg        st_imem, st_cfg, st_tx, st_pop, st_ctrl;
  reg [15:0] st_word;

  // ---------------- global pin ownership ----------------
  // pin_owner[2*p +: 2] selects the engine controlling pin p.
  // pin_valid[p] enables its assignment; reset leaves all pins unassigned.
  reg [15:0] pin_owner;
  reg [7:0]  pin_valid;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pin_owner <= 16'd0;
      pin_valid <= 8'd0;
    end else if (st_cfg && eid == 2'd0) begin
      if (haddr == 8'd2)
        pin_owner <= st_word;
      else if (haddr == 8'd3)
        pin_valid <= st_word[7:0];
    end
  end

  wire [15:0] rd_word;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cmd<=0; cmd_ok<=0; bcnt<=0; wcnt<=0; sh8<=0; sh16<=0; have_addr<=0; haddr<=0;
      rd_sh<=0; miso<=0; st_imem<=0; st_cfg<=0; st_tx<=0; st_pop<=0; st_ctrl<=0; st_word<=0;
    end else begin
      {st_imem, st_cfg, st_tx, st_pop, st_ctrl} <= 5'd0;
      if (!s_sel) begin
        cmd_ok<=0; bcnt<=0; wcnt<=0; have_addr<=0;
      end else if (s_rise) begin
        if (!cmd_ok) begin
          sh8 <= {sh8[6:0], s_din}; bcnt <= bcnt + 3'd1;
          if (bcnt == 3'd7) begin cmd <= {sh8[6:0], s_din}; cmd_ok <= 1'b1; end
        end else if (needs_addr && !have_addr) begin
          sh8 <= {sh8[6:0], s_din}; bcnt <= bcnt + 3'd1;
          if (bcnt == 3'd7) begin haddr <= {sh8[6:0], s_din}; have_addr <= 1'b1; end
        end else if (code == C_CTRL) begin
          sh8 <= {sh8[6:0], s_din}; bcnt <= bcnt + 3'd1;
          if (bcnt == 3'd7) begin st_word <= {8'd0, sh8[6:0], s_din}; st_ctrl <= 1'b1; end
        end else begin
          sh16 <= {sh16[14:0], s_din}; wcnt <= wcnt + 4'd1;
          if (wcnt == 4'd15) begin
            st_word <= {sh16[14:0], s_din};
            case (code)
              C_IMEM: st_imem <= 1'b1;
              C_CFG:  st_cfg  <= 1'b1;
              C_TX:   st_tx   <= 1'b1;
              C_RX:   st_pop  <= 1'b1;
              default: ;
            endcase
          end
        end
      end else if (s_fall && cmd_ok) begin
        if (wcnt == 4'd0) begin miso <= rd_word[15]; rd_sh <= {rd_word[14:0], 1'b0}; end
        else begin miso <= rd_sh[15]; rd_sh <= {rd_sh[14:0], 1'b0}; end
      end
      if (st_imem) haddr <= haddr + 8'd1;
    end
  end

  // ---------------- engines ----------------
  wire [7:0]  e_pout [0:NENG-1];
  wire [7:0]  e_poe  [0:NENG-1];
  wire [15:0] e_poly [0:NENG-1];
  wire [11:0] e_misc [0:NENG-1];
  wire [15:0] e_rd   [0:NENG-1];
  wire [7:0]  e_pc   [0:NENG-1];
  wire [2:0]  e_txc  [0:NENG-1];
  wire [2:0]  e_rxc  [0:NENG-1];
  wire [NENG-1:0] e_halt, e_ov, e_run;

  genvar g;
  generate for (g = 0; g < NENG; g = g + 1) begin : eng
    wire sel = (eid == g);
    reg [15:0] poly; reg [11:0] misc; 
    assign e_poly[g] = poly;
    assign e_misc[g] = misc;
    reg run; reg srst;
    always @(posedge clk or negedge rst_n)
      if (!rst_n) begin poly<=0; misc<=12'h03F; run<=0; srst<=0; end
      else begin
        srst <= 1'b0;
        if (st_cfg && sel && haddr == 8'd0) poly <= st_word;
        if (st_cfg && sel && haddr == 8'd1) misc <= st_word[11:0];
        if (st_ctrl && sel) begin run <= st_word[0]; srst <= st_word[1]; end
      end
    wire [7:0]  core_addr;
    wire        imem_we = st_imem & sel & ~run;
    wire [15:0] imem_q;
    pe_imem u_imem (.clk(clk), .we(imem_we), .addr(imem_we ? haddr : core_addr),
                    .wdata(st_word), .rdata(imem_q));
    pe_core u_core (
      .clk(clk), .rst_n(rst_n), .srst(srst), .run(run),
      .cfg_poly(poly), .cfg_crcw(misc[3:0]), .cfg_osr_lsb(misc[4]), .cfg_isr_lsb(misc[5]),
      .cfg_stuff_n(misc[8:6]), .cfg_stuff_ones(misc[9]),
      .cfg_nrzi(misc[10]), .cfg_diff(misc[11]),
      .imem_addr(core_addr), .imem_data(imem_q),
      .host_push(st_tx & sel), .host_wdata(st_word),
      .host_pop(st_pop & sel), .host_rdata(e_rd[g]),
      .tx_count(e_txc[g]), .rx_count(e_rxc[g]),
      .pin_in(pin_s2), .pout(e_pout[g]), .poe(e_poe[g]),
      .pc(e_pc[g]), .halt(e_halt[g]), .ov(e_ov[g]));
    assign e_run[g] = run;
  end endgenerate

  // read mux
  wire valid_eid = ({30'd0, eid} < NENG);
  wire [1:0] es = valid_eid ? eid : 2'd0;

  wire [15:0] cfg_read_word =
      (haddr == 8'd2 && eid == 2'd0) ? pin_owner :
      (haddr == 8'd3 && eid == 2'd0) ? {8'd0, pin_valid} :
      !valid_eid                     ? 16'd0 :
      (haddr == 8'd0)                ? e_poly[es] :
      (haddr == 8'd1)                ? {4'd0, e_misc[es]} :
                                      16'd0;

  assign rd_word = (code == C_OWNER) ? pin_owner :
                  !valid_eid       ? 16'd0 :
                  (code == C_RX)    ? e_rd[es] :
                  (code == C_STAT)  ? {e_pc[es], e_run[es], e_halt[es], e_ov[es], 5'd0} :
                  (code == C_CNT)   ? {10'd0, e_rxc[es], e_txc[es]} :
                  (code == C_CFGREAD) ? cfg_read_word : 16'd0;

  // ---------------- protocol pin ownership mux ----------------
  integer k, p;
  reg [7:0] po, pe;

  always @* begin
      po = 8'd0;
      pe = 8'd0;

      for (p = 0; p < 8; p = p + 1) begin
          for (k = 0; k < NENG; k = k + 1) begin
              if (pin_valid[p] && pin_owner[2*p +: 2] == k[1:0]) begin
                  po[p] = e_pout[k][p];
                  pe[p] = e_poe[k][p];
              end
          end
      end
  end
  assign uio_out = po;
  assign uio_oe  = pe;
  assign uo_out  = {4'd0, |e_run, |e_ov, |e_halt, miso};

  wire _unused = &{ena, ui_in[7:3], 1'b0};
endmodule
