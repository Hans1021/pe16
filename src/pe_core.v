// pe_core: one PE16 protocol engine. Behaviour is defined by isa/pe16.py
// (golden model) and docs/isa.md. Every instruction retires in one cycle
// unless it stalls (WAIT, DLY, PULL on empty, PUSH on full, HALT).
// Instruction memory is a synchronous-read RAM: imem_addr is the PC for the
// NEXT cycle, imem_data is the instruction at the CURRENT pc.
`default_nettype none
module pe_core (
  input  wire        clk,
  input  wire        rst_n,
  input  wire        srst,        // synchronous engine reset (host)
  input  wire        run,         // engine advances only when run=1
  // configuration
  input  wire [15:0] cfg_poly,
  input  wire [3:0]  cfg_crcw,    // CRC width minus one
  input  wire        cfg_osr_lsb,
  input  wire        cfg_isr_lsb,
  input  wire [2:0]  cfg_stuff_n,
  input  wire        cfg_stuff_ones,
  input  wire        cfg_nrzi,       // NRZI line coding on OUT/IN (0 = toggle)
  input  wire        cfg_diff,       // OUT also drives pin i+1 with the complement
  // instruction memory
  output wire [7:0]  imem_addr,
  input  wire [15:0] imem_data,
  // host FIFO side
  input  wire        host_push,
  input  wire [15:0] host_wdata,
  input  wire        host_pop,
  output wire [15:0] host_rdata,
  output wire [2:0]  tx_count,
  output wire [2:0]  rx_count,
  // pins (already synchronised)
  input  wire [7:0]  pin_in,
  output reg  [7:0]  pout,
  output reg  [7:0]  poe,
  // status
  output reg  [7:0]  pc,
  output reg         halt,
  output reg         ov
);
  localparam JMP=3'd0, WAIT=3'd1, DLY=3'd2, OUT=3'd3, IN=3'd4, SET=3'd5, MOV=3'd6, FIFO=3'd7;

  reg [15:0] x, y, osr, isr, crc, since;
  reg        b, s, last, rxl;
  reg [2:0]  runl;

  // ---------------- FIFOs (depth 4) ----------------
  reg [15:0] txm [0:3]; reg [1:0] txr, txw; reg [2:0] txc;
  reg [15:0] rxm [0:3]; reg [1:0] rxr, rxw; reg [2:0] rxc;
  assign tx_count = txc; assign rx_count = rxc;
  assign host_rdata = (rxc == 3'd0) ? 16'd0 : rxm[rxr];   // defined value when empty

  // ---------------- decode ----------------
  wire [15:0] ins = imem_data;
  wire [2:0]  op  = ins[15:13];
  wire [2:0]  f3a = ins[12:10];   // JMP cond, SET dst, MOV dst
  wire [1:0]  mm  = ins[12:11];   // OUT/IN mode, FIFO func
  wire [2:0]  pi  = ins[10:8];    // OUT/IN pin
  wire [2:0]  wpi = ins[11:9];    // WAIT pin
  wire [9:0]  v10 = ins[9:0];

  wire [15:0] cmask = ~(16'hFFFE << cfg_crcw);
  wire        osr_bit = cfg_osr_lsb ? osr[0] : osr[15];
  wire [15:0] osr_sh  = cfg_osr_lsb ? {1'b0, osr[15:1]} : {osr[14:0], 1'b0};
  wire        pin_raw = pin_in[pi];
  wire        pin_bit = cfg_nrzi ? ~(pin_raw ^ rxl) : pin_raw;   // NRZI decode
  wire [15:0] isr_sh  = cfg_isr_lsb ? {pin_bit, isr[15:1]} : {isr[14:0], pin_bit};
  wire        stuff_due = (cfg_stuff_n != 3'd0) && (runl >= cfg_stuff_n);

  function [15:0] crc_step;
    input [15:0] c; input d; input [3:0] w; input [15:0] p; input [15:0] m;
    reg fb;
    begin
      fb = c[w] ^ d;
      crc_step = ((c << 1) & m) ^ (fb ? (p & m) : 16'd0);
    end
  endfunction

  wire        out_v    = (mm == 2'd2 && stuff_due) ? (cfg_stuff_ones ? 1'b0 : ~last) : osr_bit;
  wire        out_cur  = pout[pi];
  wire        out_line = cfg_nrzi ? (out_v ? out_cur : ~out_cur) : out_v;
  wire [2:0]  pi1      = pi + 3'd1;
  wire [15:0] crc_out = crc_step(crc, osr_bit, cfg_crcw, cfg_poly, cmask);
  wire [15:0] crc_in  = crc_step(crc, pin_bit, cfg_crcw, cfg_poly, cmask);

  wire [2:0]  run_inc = (runl == 3'd7) ? 3'd7 : runl + 3'd1;
  function [2:0] run_next; input bit_; input last_; input ones; input [2:0] inc;
    run_next = ones ? (bit_ ? inc : 3'd0) : ((bit_ == last_) ? inc : 3'd1);
  endfunction

  reg [15:0] mov_src;
  always @* case (ins[9:7])
    3'd0: mov_src = x;   3'd1: mov_src = y;   3'd2: mov_src = osr; 3'd3: mov_src = isr;
    3'd4: mov_src = crc; 3'd5: mov_src = {8'd0, pin_in}; 3'd6: mov_src = {8'd0, poe};
    default: mov_src = 16'd0;
  endcase
  wire [15:0] mov_inv = ins[6] ? ~mov_src : mov_src;
  wire [3:0]  aln_sh  = 4'd15 - cfg_crcw;
  wire [15:0] mov_aln = ins[5] ? ((mov_inv << aln_sh) | ~(16'hFFFF << aln_sh)) : mov_inv;
  reg  [15:0] mov_val;
  integer ri;
  always @* for (ri = 0; ri < 16; ri = ri + 1) mov_val[ri] = ins[4] ? mov_aln[15-ri] : mov_aln[ri];

  wire [15:0] dly_k0 = ins[12] ? x : {4'd0, ins[11:0]};
  wire [15:0] dly_k  = (dly_k0 == 16'd0) ? 16'd1 : dly_k0;
  wire [16:0] since1 = {1'b0, since} + 17'd1;

  reg jtake;
  always @* if (ins[9]) jtake = (f3a == 3'd0) ? (x != y) : (f3a == 3'd1) ? stuff_due :
                               (f3a == 3'd2) ? (txc == 3'd0) : 1'b0;
  else case (f3a)
    3'd0: jtake = 1'b1;       3'd1: jtake = (x == 0);   3'd2: jtake = (x != 0);
    3'd3: jtake = (y == 0);   3'd4: jtake = (y != 0);   3'd5: jtake = b;
    3'd6: jtake = s;          default: jtake = (crc == 0);
  endcase

  // stall / branch (combinational, drives next imem address)
  reg stall; reg branch;
  always @* begin
    stall = 1'b0; branch = 1'b0;
    if (halt) stall = 1'b1;
    else case (op)
      JMP:  branch = jtake;
      WAIT: stall  = (pin_in[wpi] != ins[12]);
      DLY:  stall  = (since1 < {1'b0, dly_k});
      FIFO: stall  = (mm == 2'd0) ? (txc == 0) : (mm == 2'd1) ? (rxc == 3'd4) : (mm == 2'd2);
      default: ;
    endcase
  end
  wire [7:0] pc_next = branch ? ins[7:0] : stall ? pc : pc + 8'd1;
  wire       step    = run & ~srst;
  assign imem_addr = (!rst_n || srst) ? 8'd0 : step ? pc_next : pc;

  wire eng_pop  = step & ~halt & (op == FIFO) & (mm == 2'd0) & (txc != 0);
  wire eng_push = step & ~halt & (op == FIFO) & (mm == 2'd1) & (rxc != 3'd4);
  wire h_push = host_push & (txc != 3'd4);
  wire h_pop  = host_pop  & (rxc != 0);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc<=0; x<=0; y<=0; osr<=0; isr<=0; crc<=0; since<=0; pout<=0; poe<=0;
      b<=0; s<=0; ov<=0; last<=0; rxl<=0; runl<=0; halt<=0;
      txr<=0; txw<=0; txc<=0; rxr<=0; rxw<=0; rxc<=0;
    end else if (srst) begin
      pc<=0; x<=0; y<=0; osr<=0; isr<=0; crc<=0; since<=0; pout<=0; poe<=0;
      b<=0; s<=0; ov<=0; last<=0; rxl<=0; runl<=0; halt<=0;
      txr<=0; txw<=0; txc<=0; rxr<=0; rxw<=0; rxc<=0;
    end else begin
      // host side of FIFOs is live even when the engine is stopped
      if (h_push) begin txm[txw] <= host_wdata; txw <= txw + 2'd1; end
      if (h_pop)  rxr <= rxr + 2'd1;
      if (eng_pop) begin osr <= txm[txr]; txr <= txr + 2'd1; end
      if (eng_push) begin rxm[rxw] <= isr; rxw <= rxw + 2'd1; end
      txc <= txc + {2'd0, h_push} - {2'd0, eng_pop};
      rxc <= rxc + {2'd0, eng_push} - {2'd0, h_pop};

      if (run) begin
        pc    <= pc_next;
        since <= (since == 16'hFFFF) ? since : since + 16'd1;
        if (!halt) case (op)
          JMP: begin
            if (!ins[9] && f3a == 3'd2) x <= x - 16'd1;
            if (!ins[9] && f3a == 3'd4) y <= y - 16'd1;
          end
          DLY: if (!stall) begin
            since <= 16'd0;
            if (since1 > {1'b0, dly_k}) ov <= 1'b1;
          end
          OUT: begin
            if (mm == 2'd3) begin
              poe[pi] <= osr_bit; osr <= osr_sh; b <= osr_bit;
            end else begin
              pout[pi] <= out_line; b <= out_v;
              if (cfg_diff) pout[pi1] <= ~out_line;
              if (mm == 2'd2 && stuff_due) begin
                s <= 1'b1; last <= out_v; runl <= cfg_stuff_ones ? 3'd0 : 3'd1;
              end else begin
                osr <= osr_sh; s <= 1'b0;
                if (mm != 2'd0) crc <= crc_out;
                if (mm == 2'd2) begin
                  last <= osr_bit; runl <= run_next(osr_bit, last, cfg_stuff_ones, run_inc);
                end
              end
            end
          end
          IN: begin
            if (mm == 2'd3) b <= pin_raw;          // peek: raw, NRZI state untouched
            else begin b <= pin_bit; rxl <= pin_raw; end
            if (mm == 2'd3) ;
            else if (mm == 2'd2 && stuff_due) begin
              s <= 1'b1; last <= pin_bit; runl <= cfg_stuff_ones ? 3'd0 : 3'd1;
            end else begin
              isr <= isr_sh; s <= 1'b0;
              if (mm != 2'd0) crc <= crc_in;
              if (mm == 2'd2) begin
                last <= pin_bit; runl <= run_next(pin_bit, last, cfg_stuff_ones, run_inc);
              end
            end
          end
          SET: case (f3a)
            3'd0: pout[v10[9:7]] <= v10[0];
            3'd1: poe[v10[9:7]]  <= v10[0];
            3'd2: x <= {6'd0, v10};
            3'd3: y <= {6'd0, v10};
            3'd4: begin
              if (v10[1]) crc <= cmask; else if (v10[0]) crc <= 16'd0;
              if (v10[2]) begin last <= 1'b0; runl <= 3'd0; end
              if (v10[3]) isr <= 16'd0;
              if (v10[4]) ov <= 1'b0;
              if (v10[5]) since <= 16'd0;
            end
            3'd5: pout <= v10[7:0];
            3'd6: poe  <= v10[7:0];
            default: ;
          endcase
          MOV: case (f3a)
            3'd0: x <= mov_val;  3'd1: y <= mov_val;  3'd2: osr <= mov_val;
            3'd3: isr <= mov_val; 3'd4: crc <= mov_val & cmask;
            3'd5: pout <= mov_val[7:0]; 3'd6: poe <= mov_val[7:0];
            default: ;
          endcase
          FIFO: if (mm == 2'd2) halt <= 1'b1;
          default: ;
        endcase
      end
    end
  end
endmodule
