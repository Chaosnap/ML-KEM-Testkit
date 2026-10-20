`timescale 1ns/1ps
module tb_sakura_top_link;
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

    // Real board top and system; only vendor clock primitives are modelled.
    logic clk200 = 0;
    always #2.5 clk200 = ~clk200;
    wire [15:0] k_do;
    wire [9:0] leds;
    sakura_x_top dut (
        .clk200_p(clk200), .clk200_n(~clk200), .pushsw(s_rst),
        .lbus_di_a(k_di_a), .lbus_wrn(k_wrn), .lbus_rdn(k_rdn), .lbus_do(k_do),
        .uart_rxd(1'b1), .uart_txd(), .osc_en_b(), .trig_out(), .led(leds));
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

    logic [15:0] d;
    logic [7:0] reply [0:8];
    int n = 0;
    initial begin
        repeat (10) @(negedge s_clk);
        // SASEBO-GIII schematic p.54: SW4 pulls K_PUSHSW high when pressed;
        // R212 pulls it low when released. Do not derive this from DUT polarity.
        if (leds[4] !== 1'b1 || leds[9] !== 1'b0)
            $fatal(1, "pressed SW4 must assert reset and unlock the MMCM");
        s_rst = 0;
        repeat (100) @(negedge s_clk);
        if (leds[4] !== 1'b0 || leds[9] !== 1'b1)
            $fatal(1, "released SW4 must release reset and allow MMCM lock");
        lb_rd(16'hFFFC, d);
        if (d !== 16'h4D4B) $fatal(1, "bridge ID: %04x", d);
        $display("bridge ID OK; testing full protocol path");
        lb_wr(16'h0010, 16'h007f);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h0000);
        lb_wr(16'h0010, 16'h00aa);
        lb_wr(16'h0010, 16'h0018);
        lb_wr(16'h0010, 16'h0013);
        lb_wr(16'h0010, 16'h00c9);
        for (int polls = 0; polls < 300 && n < 9; polls++) begin
            lb_rd(16'h0002, d);
            if (d != 0) begin
                lb_rd(16'h0020, d);
                reply[n] = d[7:0];
                n++;
            end
        end
        if (n != 9) $fatal(1, "full protocol reply timeout: %0d of 9 bytes", n);
        if (reply[0] !== 8'h01) $fatal(1, "reply byte 0: %02x", reply[0]);
        if (reply[1] !== 8'h00) $fatal(1, "reply byte 1: %02x", reply[1]);
        if (reply[2] !== 8'h00) $fatal(1, "reply byte 2: %02x", reply[2]);
        if (reply[3] !== 8'h00) $fatal(1, "reply byte 3: %02x", reply[3]);
        if (reply[4] !== 8'h00) $fatal(1, "reply byte 4: %02x", reply[4]);
        if (reply[5] !== 8'had) $fatal(1, "reply byte 5: %02x", reply[5]);
        if (reply[6] !== 8'hde) $fatal(1, "reply byte 6: %02x", reply[6]);
        if (reply[7] !== 8'h42) $fatal(1, "reply byte 7: %02x", reply[7]);
        if (reply[8] !== 8'hfb) $fatal(1, "reply byte 8: %02x", reply[8]);
        $display("TB_SAKURA_TOP_LINK PASS");
        $finish;
    end
    initial begin #20_000_000; $fatal(1, "global timeout"); end
endmodule

// Functional models only: no claims about MMCM lock timing or physical pins.
module IBUFDS(input I, IB, output O); assign O = I; endmodule
module BUFG(input I, output O); assign O = I; endmodule
module MMCME2_BASE #(
    parameter real CLKIN1_PERIOD=5.0, CLKFBOUT_MULT_F=4.125, CLKOUT0_DIVIDE_F=4.0,
    parameter integer DIVCLK_DIVIDE=1, CLKOUT1_DIVIDE=8
)(input CLKIN1, CLKFBIN, RST, PWRDWN,
  output CLKFBOUT, output reg CLKOUT0=0, CLKOUT1=0, output LOCKED,
  output CLKFBOUTB, CLKOUT0B, CLKOUT1B, CLKOUT2, CLKOUT2B, CLKOUT3,
  CLKOUT3B, CLKOUT4, CLKOUT5, CLKOUT6);
    always #(CLKIN1_PERIOD*DIVCLK_DIVIDE*CLKOUT0_DIVIDE_F/CLKFBOUT_MULT_F/2) CLKOUT0=~CLKOUT0;
    always #(CLKIN1_PERIOD*DIVCLK_DIVIDE*CLKOUT1_DIVIDE/CLKFBOUT_MULT_F/2) CLKOUT1=~CLKOUT1;
    assign LOCKED = !RST && !PWRDWN;
    assign CLKFBOUT = CLKIN1;
endmodule
