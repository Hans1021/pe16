
`default_nettype none

module pe16_basys3_top (
    input  wire       clk100,
    input  wire       btnC,

    // Host SPI: JB Pmod
    input  wire       spi_sck,
    input  wire       spi_mosi,
    input  wire       spi_cs_n,
    output wire       spi_miso,

    // Shared protocol pins: JA Pmod
    inout  wire [7:0] pe_io,

    // Onboard status LEDs
    output wire [4:0] led
);

    // ---------------------------------------
    // Clock: 100 MHz -> 48 MHz
    // ---------------------------------------
    wire clk48;
    wire clock_locked;

    clk_wiz_0 u_clk_wiz (
        .clk_in1  (clk100),
        .clk_out1 (clk48),
        .locked   (clock_locked)
    );

    // ---------------------------------------
    // Reset
    // Async assertion, sync deassertion
    // ---------------------------------------
    wire reset_request = btnC | ~clock_locked;

    reg [1:0] reset_sync;

    always @(posedge clk48 or posedge reset_request) begin
        if (reset_request)
            reset_sync <= 2'b00;
        else
            reset_sync <= {reset_sync[0], 1'b1};
    end

    wire rst_n = reset_sync[1];

    // ---------------------------------------
    // Tiny Tapeout interface
    // ---------------------------------------
    wire [7:0] ui_in;
    wire [7:0] uo_out;

    wire [7:0] uio_in;
    wire [7:0] uio_out;
    wire [7:0] uio_oe;

    assign ui_in = {
        5'b00000,
        spi_mosi,
        spi_cs_n,
        spi_sck
    };

    assign spi_miso = uo_out[0];

    // ---------------------------------------
    // Bidirectional protocol pins
    // ---------------------------------------
    assign uio_in = pe_io;

    genvar i;
    generate
        for (i = 0; i < 8; i = i + 1) begin : gen_io
            assign pe_io[i] =
                uio_oe[i] ? uio_out[i] : 1'bz;
        end
    endgenerate

    // ---------------------------------------
    // Four-engine PE16 instance
    // ---------------------------------------
    tt_um_pe16 #(
        .NENG(4)
    ) u_pe16 (
        .ui_in   (ui_in),
        .uo_out  (uo_out),
        .uio_in  (uio_in),
        .uio_out (uio_out),
        .uio_oe  (uio_oe),
        .ena     (1'b1),
        .clk     (clk48),
        .rst_n   (rst_n)
    );

    // ---------------------------------------
    // Heartbeat and status LEDs
    // ---------------------------------------
    reg [25:0] heartbeat;

    always @(posedge clk48 or negedge rst_n) begin
        if (!rst_n)
            heartbeat <= 26'd0;
        else
            heartbeat <= heartbeat + 26'd1;
    end

    // LED0: Clock generator locked
    // LED1: Any engine running
    // LED2: Any engine halted
    // LED3: Any engine overrun
    // LED4: Heartbeat (~1.43 Hz full cycle)

    assign led[0] = clock_locked;
    assign led[1] = uo_out[3];
    assign led[2] = uo_out[1];
    assign led[3] = uo_out[2];
    assign led[4] = heartbeat[24];

endmodule

`default_nettype wire
