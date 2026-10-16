// tb_system.sv - The board system as one block: serial host -> mlkem_uart_system
//
// mlkem_uart_system (uart_rx/tx, uart_axi_bridge, axil_cdc, pqc_mlkem_top,
// status_leds) exactly as every board top instantiates it, driven over a
// real 8N1 serial line (10 Mbaud on a 100 MHz clock, to keep the run short)
// with the first ACVP Decaps vector. Checks:
//   - Decaps via 0x05 Run returns the ACVP K; trig_out pulses once, for
//     CYCLE_COUNT core cycles;
//   - no LED / RGB output changes while trig_out (the core) is high;
//   - afterwards: activity and done LEDs on, error off; link (blue),
//     completion (green) and Decaps (green, dimmed by PWM) RGB LEDs light;
//   - a frame with a bad CRC lights the link LED red; a START with a bad
//     OP_MODE lights the error LED and turns the operation LED red;
//   - in_reset drives the power LED red.
// Prints "TB_SYSTEM PASS". Plusarg +dir=<vector directory> (gen_vectors.py).

`timescale 1ps/1ps

module tb_system;

    localparam int CLK_FREQ = 100_000_000;
    localparam int BAUD     = 10_000_000;
    localparam int CPB      = CLK_FREQ / BAUD;
    localparam logic [31:0] OUT_BASE = 32'h0E00;

    logic clk = 1'b0, clk_core = 1'b0;
    logic rst_n = 1'b0, core_rst_n = 1'b0, in_reset = 1'b0;
    always #5000 clk = ~clk;                              // 100 MHz
    initial begin #1234; forever #(500000.0/206.0) clk_core = ~clk_core; end

    logic       host_tx = 1'b1;                           // host -> FPGA (uart_rxd)
    logic       uart_txd, trig_out;
    logic [3:0] led, rgb_r, rgb_g, rgb_b;

    mlkem_uart_system #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD), .N_RGB(4)) dut (
        .clk (clk), .rst_n (rst_n), .clk_core (clk_core), .core_rst_n (core_rst_n), .in_reset (in_reset),
        .uart_rxd (host_tx), .uart_txd (uart_txd), .trig_out (trig_out),
        .led (led), .rgb_r (rgb_r), .rgb_g (rgb_g), .rgb_b (rgb_b)
    );

    int errors = 0;
    task automatic check(input string what, input logic [31:0] got, input logic [31:0] exp);
        if (got !== exp) begin errors++; $display("  [FAIL] %s: got 0x%08x, expected 0x%08x", what, got, exp); end
        else $display("  [ OK ] %s = 0x%08x", what, got);
    endtask

    // ---- watchers -------------------------------------------------------
    int trig_edges = 0, trig_count = 0, trig_width = 0, led_changes_busy = 0;
    logic busy_q = 1'b0;
    always @(posedge clk_core) begin
        busy_q <= trig_out;
        if (trig_out && !busy_q) begin trig_edges <= trig_edges + 1; trig_count <= 1; end
        else if (trig_out) trig_count <= trig_count + 1;
        if (!trig_out && busy_q) trig_width <= trig_count;
    end
    always @(led or rgb_r or rgb_g or rgb_b) if (trig_out) led_changes_busy++;
    logic [3:0] seen_r = '0, seen_g = '0, seen_b = '0;      // RGB channels seen lit
    always @(posedge clk) begin seen_r <= seen_r | rgb_r; seen_g <= seen_g | rgb_g; seen_b <= seen_b | rgb_b; end

    // ---- serial host ----------------------------------------------------
    task automatic put(input logic [7:0] b);
        host_tx = 1'b0; repeat (CPB) @(posedge clk);
        for (int i = 0; i < 8; i++) begin host_tx = b[i]; repeat (CPB) @(posedge clk); end
        host_tx = 1'b1; repeat (CPB) @(posedge clk);
    endtask

    logic [7:0] rxq [0:8191];
    int         rxq_n = 0, rd_pos = 0;
    initial begin : host_rx
        logic [7:0] b;
        @(posedge rst_n);
        forever begin
            @(negedge uart_txd);
            repeat (CPB / 2) @(posedge clk);
            for (int i = 0; i < 8; i++) begin repeat (CPB) @(posedge clk); b[i] = uart_txd; end
            repeat (CPB) @(posedge clk);
            rxq[rxq_n] = b; rxq_n = rxq_n + 1;
        end
    end

    function automatic logic [31:0] crc_step(input logic [31:0] c, input logic [7:0] b);
        logic [31:0] x;
        x = c ^ {24'h0, b};
        for (int i = 0; i < 8; i++) x = x[0] ? ((x >> 1) ^ 32'hEDB8_8320) : (x >> 1);
        return x;
    endfunction

    logic [7:0] pl [0:255];
    task automatic send(input logic [7:0] cmd, input logic [31:0] addr, input int len, input bit corrupt = 0);
        logic [31:0] crc;
        crc = crc_step(32'hFFFF_FFFF, cmd); put(cmd);
        for (int i = 0; i < 4; i++) begin crc = crc_step(crc, addr[8*i +: 8]); put(addr[8*i +: 8]); end
        for (int i = 0; i < 4; i++) begin crc = crc_step(crc, 8'(len >> (8*i))); put(8'(len >> (8*i))); end
        for (int i = 0; i < len; i++) begin crc = crc_step(crc, pl[i]); put(pl[i]); end
        crc = ~crc ^ (corrupt ? 32'h1 : 32'h0);
        for (int i = 0; i < 4; i++) put(crc[8*i +: 8]);
    endtask

    logic [7:0] resp [0:4095];
    task automatic recv(output logic [7:0] status, output int n);
        int t = 0;
        while (rxq_n < rd_pos + 5 && t < 4_000_000) begin @(posedge clk); t++; end
        status = rxq[rd_pos];
        n = int'({rxq[rd_pos+4], rxq[rd_pos+3], rxq[rd_pos+2], rxq[rd_pos+1]});
        while (rxq_n < rd_pos + 9 + n) @(posedge clk);
        for (int i = 0; i < n; i++) resp[i] = rxq[rd_pos + 5 + i];
        rd_pos = rd_pos + 9 + n;
    endtask
    function automatic logic [31:0] word(input int at);
        return {resp[at+3], resp[at+2], resp[at+1], resp[at]};
    endfunction

    task automatic wr_reg(input logic [15:0] a, input logic [31:0] v);
        logic [7:0] st; int n;
        for (int i = 0; i < 4; i++) pl[i] = v[8*i +: 8];
        send(8'h02, {16'h0, a}, 4); recv(st, n);
    endtask
    task automatic run(output logic [7:0] st, output int n);
        for (int i = 0; i < 4; i++) begin pl[i] = 8'(i == 0); pl[4+i] = OUT_BASE[8*i +: 8]; pl[8+i] = 8'(i == 0 ? 32 : 0); end
        send(8'h05, 32'h0, 12); recv(st, n);
    endtask

    logic [7:0] dc_in [0:3487], dc_out [0:31];
    string dir;

    initial begin
        logic [7:0] st; int n, k_bad;
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        $readmemh({dir, "/dc_in.hex"}, dc_in);
        $readmemh({dir, "/dc_out.hex"}, dc_out);
        #100000; rst_n = 1'b1; core_rst_n = 1'b1; #100000;

        $display("--- configure, upload dk || c over the serial line ---");
        wr_reg(16'h0010, 32'd2); wr_reg(16'h0020, 32'h0); wr_reg(16'h0024, 32'd3488); wr_reg(16'h0028, OUT_BASE);
        for (int at = 0; at < 3488; at += 256) begin
            int len = (3488 - at > 256) ? 256 : 3488 - at;
            for (int i = 0; i < len; i++) pl[i] = dc_in[at + i];
            send(8'h03, 32'(at), len); recv(st, n);
        end
        check("trigger edges before the operation", trig_edges, 0);
        seen_r = '0; seen_g = '0; seen_b = '0;

        $display("--- Decaps via 0x05 Run ---");
        run(st, n);
        check("reply status / LEN", {st, 24'(n)}, {8'h00, 24'd44});
        check("STATUS = done", word(0), 32'h2);
        k_bad = 0;
        for (int i = 0; i < 32; i++) if (resp[12 + i] !== dc_out[i]) k_bad++;
        check("K bytes different from ACVP", k_bad, 0);
        check("trigger edges", trig_edges, 1);
        check("trigger width = CYCLE_COUNT", trig_width, word(4));
        repeat (300_000) @(posedge clk);                   // 3 ms: a few PWM periods
        check("LED changes while the core was busy", led_changes_busy, 0);
        check("led = {beat, error, done, activity} (beat masked)", {28'h0, led & 4'b0111}, 32'b0011);
        check("RGB1 link: blue flash", seen_b[1], 1);
        check("RGB2 completion: green flash", seen_g[2], 1);
        check("RGB3 last operation: Decaps green (PWM)", {seen_r[3], seen_g[3], seen_b[3]}, 3'b010);
        check("RGB0 power: green (PWM), not red", {seen_r[0], seen_g[0]}, 2'b01);

        $display("--- rejected frame, failed operation, reset LED ---");
        seen_r = '0;
        for (int i = 0; i < 4; i++) pl[i] = 8'h00;
        send(8'h02, 32'h0010, 4, 1); recv(st, n);
        check("bad CRC -> STATUS 0x01", st, 8'h01);
        repeat (1000) @(posedge clk);
        check("RGB1 link: red", seen_r[1], 1);
        wr_reg(16'h0010, 32'd3);                          // OP_MODE 3: START refused
        run(st, n);
        check("bad OP_MODE: STATUS = error", word(0), 32'h4);
        repeat (300_000) @(posedge clk);
        check("error LED", led[2], 1);
        check("done LED off", led[1], 0);
        check("RGB3 last operation: red", seen_r[3], 1);
        in_reset = 1'b1; #20000;
        check("RGB0 power: red while in_reset", rgb_r[0], 1);
        check("LED changes while the core was busy (total)", led_changes_busy, 0);

        if (errors == 0) $display("TB_SYSTEM PASS");
        else $display("TB_SYSTEM FAIL (%0d errors)", errors);
        $finish;
    end

    initial begin #(64'd60_000_000_000); $display("TB_SYSTEM FAIL: watchdog"); $finish; end

endmodule
