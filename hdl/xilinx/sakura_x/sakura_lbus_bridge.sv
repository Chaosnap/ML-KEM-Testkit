// sakura_lbus_bridge.sv - Host byte link over the SAKURA-X (SASEBO-GIII) local bus
//
// The board's USB port (CN15, FT2232H channel A in 245-FIFO mode) is wired to
// the Spartan-6 control FPGA only. Its factory bitstream (sasebo_giii_ctrl,
// AIST; source in the SASEBO-GIII reference package, sasebo_giii_ctrl/) turns USB bytes into 16-bit
// register accesses on the inter-FPGA "local bus":
//
//   host -> USB : 0x01 AH AL DH DL   write DH:DL to address AH:AL (no reply)
//                 0x00 AH AL         read address AH:AL, reply DH DL
//
//   lbus_di_a  Spartan-6 -> Kintex-7, address while lbus_wrn is high, write
//              data while lbus_wrn is low (one multiplexed 16-bit bus)
//   lbus_wrn   low ~5 lbus clocks per write
//   lbus_rdn   low ~5 lbus clocks per read; the Spartan-6 samples lbus_do
//              about 4 lbus clocks after it went low
//   lbus_do    Kintex-7 -> Spartan-6, read data
//
// The local bus runs at 3 MHz (24 MHz / 8, CLOCK_DIVIDE in the controller)
// and is asynchronous to clk here: every input goes through a 2-FF
// synchroniser and is sampled at least ~100 ns after it last changed.
//
// This module turns that register bus back into a byte stream so the
// unchanged host protocol (docs/UART_PROTOCOL.md) and mlkem_uart_system run
// on top of it. Bytes cross to the system through a fast internal UART pair
// (ser_tx / ser_rx at BAUD_RATE) -- hdl/core is not touched.
//
// Register map (16-bit addresses, as seen by the host, cpa_toolkit/sakura_lbus.py):
//   write 0x0010  push DL                     (1 byte host -> FPGA)
//   write 0x0012  push DH, then DL            (2 bytes host -> FPGA)
//   write 0x0030  bit0: clear FPGA -> host FIFO, bit1: clear host -> FPGA FIFO
//   read  0x0002  bytes waiting FPGA -> host  (f2h count)
//   read  0x0004  free space host -> FPGA     (h2f free)
//   read  0x0020  pop 1 byte: {empty, 7'b0, byte}
//   read  0x0022  pop 2 bytes: {first, second} (host reads 0x0002 first)
//   read  0xFFFC  ID 0x4D4B ("MK")
// Unknown addresses read 0 and ignore writes.

module sakura_lbus_bridge #(
    parameter int CLK_FREQ = 103_125_000,
    parameter int BAUD_RATE = CLK_FREQ / 16, // Must match the connected system UART.
    parameter int F2H_AW   = 12,              // FPGA -> host FIFO: 4096 bytes (keygen output 3584 + frame)
    parameter int H2F_AW   = 11               // host -> FPGA FIFO: 2048 bytes
) (
    input  logic        clk,
    input  logic        rst_n,

    // SAKURA-X local bus (asynchronous)
    input  logic [15:0] lbus_di_a,
    input  logic        lbus_wrn,
    input  logic        lbus_rdn,
    output logic [15:0] lbus_do,

    // Byte stream to / from mlkem_uart_system (internal serial lines)
    output logic        ser_tx,                // -> system uart_rxd
    input  logic        ser_rx                 // <- system uart_txd
);

    localparam int SER_BAUD = BAUD_RATE;
    localparam logic [H2F_AW:0] H2F_DEPTH = 1 << H2F_AW;
    localparam logic [F2H_AW:0] F2H_DEPTH = 1 << F2H_AW;

    localparam logic [15:0] A_F2H_CNT  = 16'h0002;
    localparam logic [15:0] A_H2F_FREE = 16'h0004;
    localparam logic [15:0] A_PUSH1    = 16'h0010;
    localparam logic [15:0] A_PUSH2    = 16'h0012;
    localparam logic [15:0] A_POP1     = 16'h0020;
    localparam logic [15:0] A_POP2     = 16'h0022;
    localparam logic [15:0] A_FLUSH    = 16'h0030;
    localparam logic [15:0] A_ID       = 16'hFFFC;

    // =========================================================================
    // Synchronisers
    // =========================================================================
    (* ASYNC_REG = "TRUE" *) logic [15:0] di_m, di_s;
    (* ASYNC_REG = "TRUE" *) logic [1:0]  wrn_m, rdn_m;
    logic wrn_s, rdn_s, wrn_q, rdn_q;

    always_ff @(posedge clk) begin
        di_m  <= lbus_di_a;  di_s  <= di_m;
        wrn_m <= {wrn_m[0], lbus_wrn};
        rdn_m <= {rdn_m[0], lbus_rdn};
        wrn_q <= wrn_s;
        rdn_q <= rdn_s;
    end
    assign wrn_s = wrn_m[1];
    assign rdn_s = rdn_m[1];

    // The bus switches from address to write data on the same lbus clock edge
    // lbus_wrn falls, so the address is taken from a copy of the bus delayed
    // by 8 clocks: when the synchronised wrn falls, addr still holds the bus
    // value from well before the switch. Reads never drive data, so addr is
    // simply the (long stable) address.
    logic [15:0] di_dly [8];
    logic [15:0] addr;
    always_ff @(posedge clk) begin
        di_dly[0] <= di_s;
        for (int i = 1; i < 8; i++) di_dly[i] <= di_dly[i-1];
        if (wrn_s && wrn_q) addr <= di_dly[7];
    end

    logic wr_fall, rd_fall;
    assign wr_fall = wrn_q && !wrn_s;
    assign rd_fall = rdn_q && !rdn_s;

    // Write data: sampled 6 clocks after the synchronised wrn fell (the bus
    // holds the data for ~1.6 us).
    logic [2:0]  wcnt;
    logic        wpend, wr_do;
    logic [15:0] wdata;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wcnt <= '0; wpend <= 1'b0; wr_do <= 1'b0; wdata <= '0;
        end else begin
            wr_do <= 1'b0;
            if (wr_fall) begin
                wpend <= 1'b1;
                wcnt  <= '0;
            end else if (wpend) begin
                wcnt <= wcnt + 3'd1;
                if (wcnt == 3'd5) begin
                    wpend <= 1'b0;
                    wdata <= di_s;
                    wr_do <= 1'b1;
                end
            end
        end
    end

    // =========================================================================
    // Host -> FPGA FIFO, drained into the internal UART transmitter
    // =========================================================================
    logic [7:0]      h2f_mem [2**H2F_AW];
    logic [H2F_AW:0] h2f_wp, h2f_rp;
    logic [H2F_AW:0] h2f_cnt;
    logic [7:0]      h2f_q;
    assign h2f_cnt = h2f_wp - h2f_rp;

    logic       push_en, push2_pend, h2f_clr;
    logic [7:0] push_byte, push2_lo;

    // Pushes: one byte per clock; 0x0012 pushes DH now and DL next clock.
    always_comb begin
        push_en   = 1'b0;
        push_byte = 8'h00;
        if (push2_pend) begin
            push_en = 1'b1; push_byte = push2_lo;
        end else if (wr_do && addr == A_PUSH1) begin
            push_en = 1'b1; push_byte = wdata[7:0];
        end else if (wr_do && addr == A_PUSH2) begin
            push_en = 1'b1; push_byte = wdata[15:8];
        end
    end

    always_ff @(posedge clk) begin
        if (push_en && h2f_cnt != H2F_DEPTH)
            h2f_mem[h2f_wp[H2F_AW-1:0]] <= push_byte;
        h2f_q <= h2f_mem[h2f_rp[H2F_AW-1:0]];
    end

    logic [7:0] tx_data;
    logic       tx_valid, tx_ready;
    logic [1:0] dstate;                        // 0 idle, 1 wait for h2f_q, 2 wait for the UART to start

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            h2f_wp <= '0; h2f_rp <= '0; push2_pend <= 1'b0; push2_lo <= '0;
            tx_data <= '0; tx_valid <= 1'b0; dstate <= '0;
        end else begin
            push2_pend <= 1'b0;
            if (wr_do && addr == A_PUSH2) begin
                push2_pend <= 1'b1;
                push2_lo   <= wdata[7:0];
            end
            if (push_en && h2f_cnt != H2F_DEPTH)
                h2f_wp <= h2f_wp + 1'b1;   // a byte that does not fit is dropped (host checks 0x0004)

            tx_valid <= 1'b0;
            case (dstate)
                2'd0: if (h2f_cnt != 0 && tx_ready) dstate <= 2'd1;
                2'd1: begin                // h2f_q is now mem[rp]
                    tx_data  <= h2f_q;
                    tx_valid <= 1'b1;
                    h2f_rp   <= h2f_rp + 1'b1;
                    dstate   <= 2'd2;
                end
                2'd2: if (!tx_ready) dstate <= 2'd3;
                default: dstate <= 2'd0;   // one more clock: h2f_q follows the new rp
            endcase

            if (h2f_clr) begin
                h2f_rp <= h2f_wp;
            end
        end
    end

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(SER_BAUD)) u_tx (
        .clk (clk), .rst_n (rst_n), .tx (ser_tx), .data (tx_data), .valid (tx_valid), .ready (tx_ready)
    );

    // =========================================================================
    // FPGA -> host FIFO, filled by the internal UART receiver
    // =========================================================================
    logic [7:0] rx_data;
    logic       rx_valid;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(SER_BAUD)) u_rx (
        .clk (clk), .rst_n (rst_n), .rx (ser_rx), .data (rx_data), .valid (rx_valid)
    );

    logic [7:0]      f2h_mem [2**F2H_AW];
    logic [F2H_AW:0] f2h_wp, f2h_rp;
    logic [F2H_AW:0] f2h_cnt;
    logic [F2H_AW-1:0] f2h_ra;
    logic [7:0]      f2h_q;
    assign f2h_cnt = f2h_wp - f2h_rp;

    always_ff @(posedge clk) begin
        if (rx_valid && f2h_cnt != F2H_DEPTH)
            f2h_mem[f2h_wp[F2H_AW-1:0]] <= rx_data;
        f2h_q <= f2h_mem[f2h_ra];
    end

    // =========================================================================
    // Register reads: lbus_do is updated within ~6 clocks of the synchronised
    // rdn falling, long before the controller samples it (~1 us later), and
    // then held until the next read.
    // =========================================================================
    logic [H2F_AW:0] h2f_free;
    assign h2f_free = H2F_DEPTH - h2f_cnt;

    logic [2:0]  rstate;
    logic [15:0] raddr;
    logic [7:0]  b0;
    logic        f2h_clr;

    assign f2h_clr = wr_do && addr == A_FLUSH && wdata[0];
    assign h2f_clr = wr_do && addr == A_FLUSH && wdata[1];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            f2h_wp <= '0; f2h_rp <= '0; f2h_ra <= '0;
            rstate <= '0; raddr <= '0; b0 <= '0; lbus_do <= '0;
        end else begin
            if (rx_valid && f2h_cnt != F2H_DEPTH)
                f2h_wp <= f2h_wp + 1'b1;   // overflow drops bytes (host drains continuously)

            case (rstate)
                3'd0: if (rd_fall) begin
                    raddr  <= addr;
                    f2h_ra <= f2h_rp[F2H_AW-1:0];
                    rstate <= 3'd1;
                end
                3'd1: begin                // memory read of rp in flight
                    f2h_ra <= f2h_rp[F2H_AW-1:0] + 1'b1;
                    rstate <= 3'd2;
                end
                3'd2: begin                // f2h_q = mem[rp]
                    b0     <= f2h_q;
                    rstate <= 3'd3;
                end
                3'd3: begin                // f2h_q = mem[rp + 1]
                    rstate <= 3'd0;
                    case (raddr)
                        A_F2H_CNT:  lbus_do <= {{(15-F2H_AW){1'b0}}, f2h_cnt};
                        A_H2F_FREE: lbus_do <= {{(15-H2F_AW){1'b0}}, h2f_free};
                        A_ID:       lbus_do <= 16'h4D4B;
                        A_POP1: begin
                            if (f2h_cnt != 0) begin
                                lbus_do <= {8'h00, b0};
                                f2h_rp  <= f2h_rp + 1'b1;
                            end else begin
                                lbus_do <= 16'h8000;
                            end
                        end
                        A_POP2: begin
                            lbus_do <= {b0, f2h_q};
                            if (f2h_cnt >= 2)      f2h_rp <= f2h_rp + (F2H_AW+1)'(2);
                            else if (f2h_cnt != 0) f2h_rp <= f2h_rp + 1'b1;
                        end
                        default:    lbus_do <= 16'h0000;
                    endcase
                end
                default: rstate <= 3'd0;
            endcase

            if (f2h_clr) f2h_rp <= f2h_wp;
        end
    end

endmodule
