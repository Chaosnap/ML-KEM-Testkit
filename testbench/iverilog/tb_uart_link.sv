`timescale 1ns / 1ps
// tb_uart_link.sv - Full UART host-link testbench (long run, ~15 ms simulated)
//
// Part A: uart_tx -> uart_rx loopback (raw 8N1 serialisation).
// Part B: the Arty A7 host link, exactly as wired in arty_a7_top.sv:
//
//   host (this TB) --serial--> uart_rx -> uart_axi_bridge -> AXI-Lite slave
//   host (this TB) <--serial-- uart_tx <-/                   + core model
//
// The TB plays the role of the host (cpa_toolkit/uart_target.py): it
// builds command frames with CRC-32/IEEE, bit-bangs them onto the RX line,
// decodes the TX line independently and checks every response frame.
// Part C: 0x05 Run against a small core model (busy for RUN_CYCLES after
// CTRL.start, then done; error / hang modes) - the reply must come only
// after the operation, and the UART must stay idle while the core is busy.
//
// Run:   make -C testbench/iverilog uart-link
// Wave:  build/sim/tb_uart_link.vcd

module tb_uart_link;

    // Board parameters (arty_a7_top.sv). Override BAUD_RATE for a faster run:
    //   make -C testbench/iverilog uart-link BAUD=1000000
    parameter int CLK_FREQ  = 100_000_000;
    parameter int BAUD_RATE = 115200;

    localparam int CPB       = CLK_FREQ / BAUD_RATE;  // Clocks per bit.
    localparam int DATA_BASE = 16'h4000;

    // Core model for 0x05 Run (Part C).
    localparam int RUN_CYCLES  = 3000;    // Busy time of one operation.
    localparam int BUSY_LAT    = 3;       // CTRL.start -> busy (CDC + ctrl FSM).
    localparam int RUN_TIMEOUT = 50_000;  // Bridge gives up after this.

    // ---------------------------------------------------------------------
    // Clock / reset
    // ---------------------------------------------------------------------
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk = ~clk;  // 100 MHz.

    // ---------------------------------------------------------------------
    // Bookkeeping (visible in the VCD)
    // ---------------------------------------------------------------------
    logic [8*24-1:0] test_name = "reset";  // ASCII: show as "ASCII" in the viewer.
    integer          checks = 0;
    integer          errors = 0;

    task automatic check(input string what, input logic [31:0] got, input logic [31:0] exp);
        checks = checks + 1;
        if (got !== exp) begin
            errors = errors + 1;
            $display("  [FAIL] %s: got 0x%08h, expected 0x%08h", what, got, exp);
        end else begin
            $display("  [ OK ] %s = 0x%08h", what, got);
        end
    endtask

    // =====================================================================
    // Part A: uart_tx -> uart_rx loopback
    // =====================================================================
    logic       lb_line;
    logic [7:0] lb_tx_data = 8'h00;
    logic       lb_tx_valid = 1'b0;
    logic       lb_tx_ready;
    logic [7:0] lb_rx_data;
    logic       lb_rx_valid;

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_lb_tx (
        .clk(clk), .rst_n(rst_n), .tx(lb_line),
        .data(lb_tx_data), .valid(lb_tx_valid), .ready(lb_tx_ready)
    );

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_lb_rx (
        .clk(clk), .rst_n(rst_n), .rx(lb_line),
        .data(lb_rx_data), .valid(lb_rx_valid)
    );

    task automatic loopback_byte(input logic [7:0] b);
        while (!lb_tx_ready) @(posedge clk);
        @(negedge clk) begin lb_tx_data = b; lb_tx_valid = 1'b1; end
        @(negedge clk) lb_tx_valid = 1'b0;
        @(posedge lb_rx_valid);
        @(negedge clk);
        check($sformatf("loopback 0x%02h", b), {24'h0, lb_rx_data}, {24'h0, b});
    endtask

    // =====================================================================
    // Part B: host <-> uart_rx/uart_axi_bridge/uart_tx <-> AXI-Lite slave
    // =====================================================================
    logic host_tx_line = 1'b1;   // Host -> FPGA (uart_rxd).
    logic host_rx_line;          // FPGA -> host (uart_txd).

    logic [7:0]  rx_byte, tx_byte;
    logic        rx_valid, tx_valid, tx_ready;

    logic [15:0] awaddr, araddr;
    logic        awvalid, awready, wvalid, wready, bvalid, bready;
    logic        arvalid, arready, rvalid, rready;
    logic [31:0] wdata, rdata;
    logic [3:0]  wstrb;
    logic [1:0]  bresp, rresp;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_rx (
        .clk(clk), .rst_n(rst_n), .rx(host_tx_line),
        .data(rx_byte), .valid(rx_valid)
    );

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_tx (
        .clk(clk), .rst_n(rst_n), .tx(host_rx_line),
        .data(tx_byte), .valid(tx_valid), .ready(tx_ready)
    );

    logic core_busy = 1'b0;          // Core model (below) -> bridge.

    uart_axi_bridge #(.TIMEOUT_CYCLES(CLK_FREQ / 10), .RUN_TIMEOUT_CYCLES(RUN_TIMEOUT)) u_bridge (
        .clk(clk), .rst_n(rst_n), .core_busy(core_busy),
        .rx_byte(rx_byte), .rx_valid(rx_valid),
        .tx_byte(tx_byte), .tx_valid(tx_valid), .tx_ready(tx_ready),
        .m_axi_awaddr(awaddr), .m_axi_awvalid(awvalid), .m_axi_awready(awready),
        .m_axi_wdata(wdata), .m_axi_wstrb(wstrb), .m_axi_wvalid(wvalid), .m_axi_wready(wready),
        .m_axi_bresp(bresp), .m_axi_bvalid(bvalid), .m_axi_bready(bready),
        .m_axi_araddr(araddr), .m_axi_arvalid(arvalid), .m_axi_arready(arready),
        .m_axi_rdata(rdata), .m_axi_rresp(rresp), .m_axi_rvalid(rvalid), .m_axi_rready(rready)
    );

    // ---------------------------------------------------------------------
    // AXI-Lite slave model: 64 CSR words + first 256 bytes of data buffer.
    // ---------------------------------------------------------------------
    logic [31:0] csr  [0:63];
    logic [31:0] dbuf [0:63];

    assign awready = 1'b1;
    assign wready  = 1'b1;
    assign arready = 1'b1;
    assign bresp   = 2'b00;
    assign rresp   = 2'b00;

    integer      start_lat = 0;
    logic [31:0] busy_left = '0;

    // While the core is busy the FPGA must not transmit (0x05 keeps the
    // link silent during the operation).
    integer tx_edges_while_busy = 0;
    always @(host_rx_line) if (core_busy) tx_edges_while_busy = tx_edges_while_busy + 1;

    // Last write seen on the bus (handy in the VCD).
    logic [15:0] axi_last_waddr;
    logic [31:0] axi_last_wdata;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bvalid <= 1'b0;
            rvalid <= 1'b0;
            rdata  <= '0;
        end else begin
            bvalid <= 1'b0;
            rvalid <= 1'b0;
            // Core model: CTRL bit0 = start, bit1 = reset; OP_MODE 3 = bad
            // parameter (error at once, never busy), OP_MODE 7 = hangs.
            if (start_lat > 0) start_lat <= start_lat - 1;
            if (start_lat == 1) begin
                core_busy <= 1'b1;
                csr[1]    <= 32'h1;                       // STATUS = busy
                busy_left <= (csr[4] == 32'd7) ? 32'hFFFF_FFFF : RUN_CYCLES;
            end
            if (core_busy && busy_left != 32'hFFFF_FFFF) begin
                busy_left <= busy_left - 1;
                if (busy_left == 1) begin
                    core_busy <= 1'b0;
                    csr[1]    <= 32'h2;                   // STATUS = done
                    csr[5]    <= RUN_CYCLES;              // CYCLE_COUNT
                end
            end
            if (awvalid && wvalid) begin
                for (int i = 0; i < 4; i++)
                    if (wstrb[i]) begin
                        if (awaddr >= DATA_BASE) dbuf[awaddr[7:2]][8*i +: 8] <= wdata[8*i +: 8];
                        else                     csr [awaddr[7:2]][8*i +: 8] <= wdata[8*i +: 8];
                    end
                axi_last_waddr <= awaddr;
                axi_last_wdata <= wdata;
                bvalid <= 1'b1;
                if (awaddr == 16'h0000 && wdata[1]) begin           // CTRL.reset
                    core_busy <= 1'b0;
                    start_lat <= 0;
                    csr[1]    <= 32'h0;
                    csr[7]    <= 32'h0;
                end else if (awaddr == 16'h0000 && wdata[0] && !core_busy) begin  // CTRL.start
                    csr[5] <= 32'h0;
                    csr[7] <= 32'h0;
                    if (csr[4] == 32'd3) begin
                        csr[1] <= 32'h4;                  // STATUS = error, never busy
                        csr[7] <= 32'h1;                  // ERROR_CODE = 1
                    end else begin
                        csr[1]    <= 32'h0;
                        start_lat <= BUSY_LAT;
                    end
                end
            end
            if (arvalid) begin
                rdata  <= (araddr >= DATA_BASE) ? dbuf[araddr[7:2]] : csr[araddr[7:2]];
                rvalid <= 1'b1;
            end
        end
    end

    initial begin
        for (int i = 0; i < 64; i++) begin
            csr[i]  = 32'h0;
            dbuf[i] = 32'h0;
        end
        csr[8'h18 >> 2] = 32'h0001_0000;  // RegVersion = 1.0.0
    end

    // ---------------------------------------------------------------------
    // CRC-32/IEEE (independent re-implementation of Go crc32.ChecksumIEEE).
    // ---------------------------------------------------------------------
    function automatic logic [31:0] crc_step(input logic [31:0] c, input logic [7:0] b);
        logic [31:0] x;
        x = c ^ {24'h0, b};
        for (int i = 0; i < 8; i++)
            x = x[0] ? ((x >> 1) ^ 32'hEDB8_8320) : (x >> 1);
        return x;
    endfunction

    // ---------------------------------------------------------------------
    // Host serial transmitter (bit-banged 8N1 on host_tx_line).
    // ---------------------------------------------------------------------
    logic [7:0] host_tx_byte = 8'h00;   // Byte currently on the wire.

    task automatic host_send_byte(input logic [7:0] b);
        host_tx_byte = b;
        host_tx_line = 1'b0;                          // Start bit.
        repeat (CPB) @(posedge clk);
        for (int i = 0; i < 8; i++) begin
            host_tx_line = b[i];                      // LSB first.
            repeat (CPB) @(posedge clk);
        end
        host_tx_line = 1'b1;                          // Stop bit.
        repeat (CPB) @(posedge clk);
    endtask

    // ---------------------------------------------------------------------
    // Host serial receiver: decodes host_rx_line into rx_q[].
    // ---------------------------------------------------------------------
    logic [7:0] rx_q [0:1023];
    integer     rx_wr = 0;
    logic [7:0] host_rx_byte = 8'h00;   // Last byte decoded (VCD).
    logic       host_rx_strobe = 1'b0;  // 1-cycle pulse per decoded byte.
    logic       host_rx_sample = 1'b0;  // Toggles at every sampling point.

    initial begin : host_receiver
        logic [7:0] b;
        @(posedge rst_n);
        forever begin
            @(negedge host_rx_line);
            repeat (CPB / 2) @(posedge clk);          // Middle of start bit.
            if (host_rx_line == 1'b0) begin
                for (int i = 0; i < 8; i++) begin
                    repeat (CPB) @(posedge clk);
                    host_rx_sample = ~host_rx_sample;
                    b[i] = host_rx_line;
                end
                repeat (CPB) @(posedge clk);          // Middle of stop bit.
                if (host_rx_line !== 1'b1) begin
                    errors = errors + 1;
                    $display("  [FAIL] framing error on FPGA TX line");
                end
                rx_q[rx_wr]  = b;
                rx_wr        = rx_wr + 1;
                host_rx_byte = b;
                host_rx_strobe = 1'b1;
                @(posedge clk) host_rx_strobe = 1'b0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Frame helpers.
    // ---------------------------------------------------------------------
    logic [7:0] payload [0:255];
    logic [7:0] exp_data [0:255];
    integer     resp_extra = 0;    // Extra cycles a response may take (0x05 waits).

    // 0x05 Run payload: CTRL value, out offset, out count.
    task automatic run_payload(input logic [31:0] value, input logic [31:0] off, input logic [31:0] count);
        for (int i = 0; i < 4; i++) begin
            payload[i]     = value[8*i +: 8];
            payload[4 + i] = off[8*i +: 8];
            payload[8 + i] = count[8*i +: 8];
        end
    endtask

    // Expected 12-byte status block of a 0x05 reply.
    task automatic exp_block(input logic [31:0] st, input logic [31:0] cyc, input logic [31:0] err);
        for (int i = 0; i < 4; i++) begin
            exp_data[i]     = st[8*i +: 8];
            exp_data[4 + i] = cyc[8*i +: 8];
            exp_data[8 + i] = err[8*i +: 8];
        end
    endtask

    // Send [CMD][ADDR:4][LEN:4][DATA:LEN][CRC:4]. corrupt=1 flips the CRC.
    task automatic send_frame(input logic [7:0] cmd, input logic [31:0] addr,
                              input logic [31:0] len, input bit corrupt);
        logic [31:0] crc;
        crc = crc_step(32'hFFFF_FFFF, cmd);
        host_send_byte(cmd);
        for (int i = 0; i < 4; i++) begin
            crc = crc_step(crc, addr[8*i +: 8]);
            host_send_byte(addr[8*i +: 8]);
        end
        for (int i = 0; i < 4; i++) begin
            crc = crc_step(crc, len[8*i +: 8]);
            host_send_byte(len[8*i +: 8]);
        end
        for (int i = 0; i < len; i++) begin
            crc = crc_step(crc, payload[i]);
            host_send_byte(payload[i]);
        end
        crc = ~crc;
        if (corrupt) crc = crc ^ 32'h0000_0001;
        for (int i = 0; i < 4; i++)
            host_send_byte(crc[8*i +: 8]);
    endtask

    // Wait for [STATUS][LEN:4][DATA:LEN][CRC:4] starting at rx_q[base],
    // and check it against exp_status / exp_len / exp_data[].
    task automatic expect_response(input integer base, input logic [7:0] exp_status,
                                   input logic [31:0] exp_len);
        integer      n, timeout;
        logic [31:0] got_len, got_crc, crc;
        n = 1 + 4 + exp_len + 4;
        timeout = 0;
        while (rx_wr < base + n && timeout < CPB * 10 * (n + 4) + resp_extra) begin
            @(posedge clk);
            timeout = timeout + 1;
        end
        if (rx_wr < base + n) begin
            errors = errors + 1;
            $display("  [FAIL] response timeout: got %0d of %0d bytes", rx_wr - base, n);
            return;
        end
        crc = 32'hFFFF_FFFF;
        for (int i = 0; i < n - 4; i++)
            crc = crc_step(crc, rx_q[base + i]);
        got_len = {rx_q[base+4], rx_q[base+3], rx_q[base+2], rx_q[base+1]};
        got_crc = {rx_q[base+n-1], rx_q[base+n-2], rx_q[base+n-3], rx_q[base+n-4]};
        check("resp STATUS", {24'h0, rx_q[base]}, {24'h0, exp_status});
        check("resp LEN", got_len, exp_len);
        for (int i = 0; i < exp_len; i++)
            check($sformatf("resp DATA[%0d]", i), {24'h0, rx_q[base+5+i]}, {24'h0, exp_data[i]});
        check("resp CRC", got_crc, ~crc);
    endtask

    // =====================================================================
    // Stimulus
    // =====================================================================
    integer base;

    initial begin
        $dumpfile("tb_uart_link.vcd");
        $dumpvars(0, tb_uart_link);

        $display("=== tb_uart_link: CLK_FREQ=%0d BAUD=%0d (%0d clk/bit) ===", CLK_FREQ, BAUD_RATE, CPB);

        // Sanity check of the TB CRC: CRC-32("123456789") = 0xCBF43926.
        begin
            logic [31:0] c;
            string s;
            s = "123456789";
            c = 32'hFFFF_FFFF;
            for (int i = 0; i < 9; i++) c = crc_step(c, s[i]);
            check("TB CRC-32 self-test", ~c, 32'hCBF4_3926);
        end

        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (10) @(posedge clk);

        // ---------------- Part A ----------------
        test_name = "A_loopback";
        $display("--- A: uart_tx -> uart_rx loopback ---");
        loopback_byte(8'h55);
        loopback_byte(8'hA5);
        loopback_byte(8'h00);
        loopback_byte(8'hFF);
        loopback_byte(8'h3C);

        // ---------------- Part B ----------------
        test_name = "B1_write_reg";
        $display("--- B1: 0x02 write register 0x0010 <- 0xDEADBEEF ---");
        base = rx_wr;
        payload[0] = 8'hEF; payload[1] = 8'hBE; payload[2] = 8'hAD; payload[3] = 8'hDE;
        send_frame(8'h02, 32'h0000_0010, 32'd4, 1'b0);
        expect_response(base, 8'h00, 32'd0);
        check("AXI csr[0x10]", csr[8'h10 >> 2], 32'hDEAD_BEEF);

        test_name = "B2_read_reg";
        $display("--- B2: 0x01 read register 0x0010 ---");
        base = rx_wr;
        exp_data[0] = 8'hEF; exp_data[1] = 8'hBE; exp_data[2] = 8'hAD; exp_data[3] = 8'hDE;
        send_frame(8'h01, 32'h0000_0010, 32'd0, 1'b0);
        expect_response(base, 8'h00, 32'd4);

        test_name = "B3_read_version";
        $display("--- B3: 0x01 read register 0x0018 (RegVersion) ---");
        base = rx_wr;
        exp_data[0] = 8'h00; exp_data[1] = 8'h00; exp_data[2] = 8'h01; exp_data[3] = 8'h00;
        send_frame(8'h01, 32'h0000_0018, 32'd0, 1'b0);
        expect_response(base, 8'h00, 32'd4);

        test_name = "B4_write_data";
        $display("--- B4: 0x03 write data buffer[0..5] ---");
        base = rx_wr;
        payload[0] = 8'h11; payload[1] = 8'h22; payload[2] = 8'h33;
        payload[3] = 8'h44; payload[4] = 8'h55; payload[5] = 8'h66;
        send_frame(8'h03, 32'h0000_0000, 32'd6, 1'b0);
        expect_response(base, 8'h00, 32'd0);
        check("AXI dbuf word0", dbuf[0], 32'h4433_2211);
        check("AXI dbuf word1", dbuf[1] & 32'h0000_FFFF, 32'h0000_6655);

        test_name = "B5_read_data";
        $display("--- B5: 0x04 read data buffer[1..5] (unaligned) ---");
        base = rx_wr;
        payload[0] = 8'd5; payload[1] = 8'd0; payload[2] = 8'd0; payload[3] = 8'd0;
        exp_data[0] = 8'h22; exp_data[1] = 8'h33; exp_data[2] = 8'h44;
        exp_data[3] = 8'h55; exp_data[4] = 8'h66;
        send_frame(8'h04, 32'h0000_0001, 32'd4, 1'b0);
        expect_response(base, 8'h00, 32'd5);

        test_name = "B6_bad_crc";
        $display("--- B6: corrupted CRC -> STATUS 0x01 ---");
        base = rx_wr;
        payload[0] = 8'h00; payload[1] = 8'h00; payload[2] = 8'h00; payload[3] = 8'h00;
        send_frame(8'h02, 32'h0000_0010, 32'd4, 1'b1);
        expect_response(base, 8'h01, 32'd0);
        check("AXI csr[0x10] unchanged", csr[8'h10 >> 2], 32'hDEAD_BEEF);

        // ---------------- Part C: 0x05 Run ----------------
        resp_extra = RUN_TIMEOUT + 1000;

        test_name = "C1_run_probe";
        $display("--- C1: 0x05 with CTRL = 0 starts nothing -> immediate status block ---");
        base = rx_wr;
        run_payload(32'h0, 32'h0, 32'd0);
        exp_block(32'h0, 32'h0, 32'h0);
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h00, 32'd12);

        test_name = "C2_run_decaps";
        $display("--- C2: 0x05 CTRL.start, out = buffer[0..5]: reply only after the operation ---");
        base = rx_wr;
        tx_edges_while_busy = 0;
        run_payload(32'h1, 32'h0, 32'd6);
        exp_block(32'h2, RUN_CYCLES, 32'h0);
        exp_data[12] = 8'h11; exp_data[13] = 8'h22; exp_data[14] = 8'h33;
        exp_data[15] = 8'h44; exp_data[16] = 8'h55; exp_data[17] = 8'h66;
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h00, 32'd18);
        check("FPGA TX edges while the core was busy", tx_edges_while_busy, 0);

        test_name = "C3_run_unaligned";
        $display("--- C3: 0x05 again, out = buffer[2..4] (unaligned offset) ---");
        base = rx_wr;
        run_payload(32'h1, 32'h2, 32'd3);
        exp_block(32'h2, RUN_CYCLES, 32'h0);
        exp_data[12] = 8'h33; exp_data[13] = 8'h44; exp_data[14] = 8'h55;
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h00, 32'd15);

        test_name = "C4_run_error";
        $display("--- C4: 0x05 with a bad OP_MODE -> STATUS error, no output bytes ---");
        base = rx_wr;
        payload[0] = 8'd3; payload[1] = 8'd0; payload[2] = 8'd0; payload[3] = 8'd0;
        send_frame(8'h02, 32'h0000_0010, 32'd4, 1'b0);       // OP_MODE = 3
        expect_response(base, 8'h00, 32'd0);
        base = rx_wr;
        run_payload(32'h1, 32'h0, 32'd6);
        exp_block(32'h4, 32'h0, 32'h1);
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h00, 32'd12);

        test_name = "C5_run_bad_args";
        $display("--- C5: 0x05 with out offset + count past the buffer -> STATUS 0x01 ---");
        base = rx_wr;
        run_payload(32'h1, 32'd16380, 32'd8);
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h01, 32'd0);

        test_name = "C6_run_timeout";
        $display("--- C6: 0x05 on a hung core -> reply after RUN_TIMEOUT with STATUS busy ---");
        base = rx_wr;
        payload[0] = 8'd7; payload[1] = 8'd0; payload[2] = 8'd0; payload[3] = 8'd0;
        send_frame(8'h02, 32'h0000_0010, 32'd4, 1'b0);       // OP_MODE = 7: hangs
        expect_response(base, 8'h00, 32'd0);
        base = rx_wr;
        run_payload(32'h1, 32'h0, 32'd6);
        exp_block(32'h1, 32'h0, 32'h0);
        send_frame(8'h05, 32'h0000_0000, 32'd12, 1'b0);
        expect_response(base, 8'h00, 32'd12);
        base = rx_wr;
        payload[0] = 8'd2; payload[1] = 8'd0; payload[2] = 8'd0; payload[3] = 8'd0;
        send_frame(8'h02, 32'h0000_0000, 32'd4, 1'b0);       // CTRL.reset
        expect_response(base, 8'h00, 32'd0);
        check("core idle after CTRL.reset", {31'h0, core_busy}, 32'h0);

        test_name = "done";
        repeat (CPB) @(posedge clk);

        $display("=== tb_uart_link: %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("TB_UART_LINK PASS");
        else             $display("TB_UART_LINK FAIL");
        $finish;
    end

    // Global watchdog.
    initial begin
        #(64'd200_000_000);  // 200 ms simulated.
        $display("TB_UART_LINK FAIL: watchdog timeout");
        $finish;
    end

endmodule
