// tb_lbus_bridge.sv - sakura_lbus_bridge against AIST's own SASEBO-GIII
// controller logic (CTRL_LBUS + SYNCFIFO_8x31 from the reference package's sasebo_giii_ctrl/,
// with the output registers of chip_sasebo_giii_ctrl.v) at 3 MHz, the bridge at
// 103.125 MHz with its byte stream looped back (ser_tx -> ser_rx). The host
// side pushes the USB command bytes the PC would send and pops the replies.
`timescale 1ns/1ps
module tb_lbus_bridge;
    // ---------------- Spartan-6 side (3 MHz) ----------------
    logic s_clk = 0, s_rst = 1;
    always #166.667 s_clk = ~s_clk;

    logic [7:0] h_wd;  logic h_we = 0;  logic h_ful, h_aful;      // PC -> FIFO
    logic [7:0] sf_rd; logic sf_re, sf_emp, sf_aemp;
    logic [7:0] sf_wd; logic sf_we, sf_ful, sf_aful;
    logic [7:0] h_rd;  logic h_re = 0;  logic h_emp, h_aemp;      // FIFO -> PC

    SYNCFIFO_8x31 fifo_rd (.wd(h_wd), .we(h_we), .ful(h_ful), .aful(h_aful),
                           .rd(sf_rd), .re(sf_re), .emp(sf_emp), .aemp(sf_aemp),
                           .cnt(), .clk(s_clk), .rst(s_rst));
    SYNCFIFO_8x31 fifo_wr (.wd(sf_wd), .we(sf_we), .ful(sf_ful), .aful(sf_aful),
                           .rd(h_rd), .re(h_re), .emp(h_emp), .aemp(h_aemp),
                           .cnt(), .clk(s_clk), .rst(s_rst));

    logic [15:0] lbus_a, lbus_di, lbus_do;
    logic lbus_wrn0, lbus_rdn0;
    CTRL_LBUS ctrl (.wd(sf_wd), .we(sf_we), .ful(sf_ful), .aful(sf_aful),
                    .rd(sf_rd), .re(sf_re), .emp(sf_emp), .aemp(sf_aemp),
                    .lbus_a(lbus_a), .lbus_di(lbus_di), .lbus_do(lbus_do),
                    .lbus_wrn(lbus_wrn0), .lbus_rdn(lbus_rdn0), .clk(s_clk), .rst(s_rst));

    // Output registers exactly as chip_sasebo_giii_ctrl.v, plus board skew.
    logic [15:0] di_a_r; logic wrn_r = 1, rdn_r = 1;
    always @(posedge s_clk) begin
        wrn_r  <= s_rst ? 1'b1 : lbus_wrn0;
        rdn_r  <= s_rst ? 1'b1 : lbus_rdn0;
        di_a_r <= lbus_wrn0 ? lbus_a : lbus_di;
    end
    wire [15:0] k_di_a;  wire k_wrn, k_rdn;
    genvar g;
    for (g = 0; g < 16; g++) begin : skew
        assign #(1 + (g * 7) % 9) k_di_a[g] = di_a_r[g];
    end
    assign #4 k_wrn = wrn_r;
    assign #6 k_rdn = rdn_r;

    // ---------------- Kintex-7 side (103.125 MHz) ----------------
    logic k_clk = 0, k_rst_n = 0;
    always #4.8485 k_clk = ~k_clk;
    wire ser;
    wire [15:0] k_do;
    sakura_lbus_bridge #(.CLK_FREQ(103_125_000)) dut (
        .clk(k_clk), .rst_n(k_rst_n),
        .lbus_di_a(k_di_a), .lbus_wrn(k_wrn), .lbus_rdn(k_rdn), .lbus_do(k_do),
        .ser_tx(ser), .ser_rx(ser));
    assign #5 lbus_do = k_do;

    // ---------------- host model ----------------
    task automatic put(input [7:0] b);
        @(negedge s_clk); while (h_ful) @(negedge s_clk);
        h_wd = b; h_we = 1; @(negedge s_clk); h_we = 0;
    endtask
    task automatic get(output [7:0] b);
        int t;
        t = 0;
        @(negedge s_clk);
        while (h_emp) begin @(negedge s_clk); if (++t > 20000) $fatal(1, "reply timeout"); end
        h_re = 1; @(negedge s_clk); h_re = 0;
        b = h_rd;   // rd = mem[ra1]: still the popped byte for one clock
    endtask
    task automatic lb_wr(input [15:0] a, input [15:0] d);
        put(8'h01); put(a[15:8]); put(a[7:0]); put(d[15:8]); put(d[7:0]);
    endtask
    task automatic lb_rd(input [15:0] a, output [15:0] d);
        logic [7:0] hi, lo;
        put(8'h00); put(a[15:8]); put(a[7:0]);
        get(hi); get(lo); d = {hi, lo};
    endtask

    int errors = 0;
    int n, k;
    task automatic expect16(input int id, input [15:0] got, input [15:0] exp);
        if (got !== exp) begin errors++; $display("FAIL check %0d (n=%0d): got %04x expected %04x", id, n, got, exp); end
    endtask

    logic [7:0] sent [0:300];
    logic [15:0] d;

    initial begin
        repeat (5) @(posedge s_clk); s_rst = 0;
        #200 k_rst_n = 1;
        repeat (5) @(posedge s_clk);

        lb_rd(16'hFFFC, d); expect16(1, d, 16'h4D4B);
        lb_rd(16'h0004, d); expect16(2, d, 16'd2048);
        lb_rd(16'h0002, d); expect16(3, d, 16'd0);
        lb_rd(16'h0020, d); expect16(4, d, 16'h8000);
        lb_rd(16'h1234, d); expect16(5, d, 16'h0000);

        // 301 random bytes, mostly as 2-byte pushes; loopback returns them.
        for (int i = 0; i < 301; i++) sent[i] = 8'($urandom);
        for (int i = 0; i + 1 < 301; i += 2) lb_wr(16'h0012, {sent[i], sent[i+1]});
        lb_wr(16'h0010, {8'hAA, sent[300]});
        n = 0;
        while (n < 301) begin
            lb_rd(16'h0002, d);
            k = d;
            while (k >= 2 && n + 1 < 301) begin
                lb_rd(16'h0022, d);
                expect16(6, d, {sent[n], sent[n+1]});
                n += 2; k -= 2;
            end
            if (k >= 1) begin
                lb_rd(16'h0020, d);
                expect16(7, d, {8'h00, sent[n]});
                n += 1;
            end
        end
        lb_rd(16'h0002, d); expect16(8, d, 16'd0);

        // Flush: bytes that arrived are discarded.
        lb_wr(16'h0012, 16'h1122);
        #20000;
        lb_rd(16'h0002, d); expect16(9, d, 16'd2);
        lb_wr(16'h0030, 16'h0003);
        lb_rd(16'h0002, d); expect16(10, d, 16'd0);

        // The factory parser traps on invalid command bytes. Neither reading
        // again nor a host-side input purge can reset that hardware state.
        put(8'h7F); put(8'h00); put(8'h00);
        repeat (50) @(negedge s_clk);
        put(8'h00); put(8'hFF); put(8'hFC);
        repeat (100) @(negedge s_clk);
        if (!h_emp) $fatal(1, "unexpected reply while factory parser is trapped");
        s_rst = 1;
        repeat (5) @(negedge s_clk);
        s_rst = 0;
        repeat (5) @(negedge s_clk);
        lb_rd(16'hFFFC, d); expect16(11, d, 16'h4D4B);

        if (errors == 0) $display("TB_LBUS_BRIDGE PASS");
        else             $display("TB_LBUS_BRIDGE FAIL (%0d errors)", errors);
        $finish;
    end
    initial begin #50_000_000; $fatal(1, "global timeout"); end
endmodule
