`timescale 1ns / 1ps
// tb_ntt.sv - Icarus Verilog testbench for the ML-KEM NTT core
//
// DUT: mlkem_poly_alu (OP_NTT / OP_INTT) + mlkem_polyram, the NTT datapath
// instantiated by pqc_mlkem_top.sv. q = 3329, n = 256, two coefficients
// per 24-bit RAM word: word {slot, w} = {f[2w+1], f[2w]}.
//
// Every result is compared against a behavioural model of FIPS 203
// Algorithm 9 (NTT) and Algorithm 10 (NTT^-1) written in this file. The
// model computes its own zetas (17^BitRev7(i) mod q), so it does not share
// the RTL constant ROM.
//
// Run:   make -C testbench/iverilog ntt
// Wave:  build/sim/tb_ntt.vcd   (see docs/IVERILOG_SIM_GUIDE.md)

module tb_ntt;

    localparam int Q = 3329;

    localparam logic [2:0] OP_NTT  = 3'd0;
    localparam logic [2:0] OP_INTT = 3'd1;

    // ---------------------------------------------------------------------
    // Clock / reset
    // ---------------------------------------------------------------------
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk = ~clk;  // 100 MHz.

    // ---------------------------------------------------------------------
    // Bookkeeping (visible in the VCD)
    // ---------------------------------------------------------------------
    logic [8*24-1:0] test_name = "reset";  // Show as ASCII in the viewer.
    integer          checks = 0;
    integer          errors = 0;

    // Per-coefficient compare strobe (readback phase).
    logic        chk_valid = 1'b0;
    logic [7:0]  chk_idx   = '0;
    logic [11:0] chk_got   = '0;
    logic [11:0] chk_exp   = '0;
    logic        chk_err   = 1'b0;

    // Cycle count of the last ALU operation (start -> done).
    integer      op_cycles = 0;

    // ---------------------------------------------------------------------
    // DUT: poly ALU + poly RAM. Port A is muxed to the TB for load/readback.
    // ---------------------------------------------------------------------
    logic        start = 1'b0;
    logic [2:0]  op    = OP_NTT;
    logic [2:0]  slot  = '0;
    logic        done;

    logic        alu_a_en, alu_a_we, alu_b_en, alu_b_we;
    logic [9:0]  alu_a_addr, alu_b_addr;
    logic [23:0] alu_a_din, alu_b_din;
    logic [23:0] a_dout, b_dout;

    logic        tb_own  = 1'b1;   // 1: TB drives RAM port A.
    logic        tb_en   = 1'b0;
    logic        tb_we   = 1'b0;
    logic [9:0]  tb_addr = '0;
    logic [23:0] tb_din  = '0;

    mlkem_poly_alu u_alu (
        .clk     (clk),
        .rst_n   (rst_n),
        .clr     (1'b0),
        .start   (start),
        .op      (op),
        .slot_a  (slot),
        .slot_b  (3'd0),
        .slot_c  (3'd0),
        .acc     (1'b0),
        .done    (done),
        .ra_en   (alu_a_en),
        .ra_we   (alu_a_we),
        .ra_addr (alu_a_addr),
        .ra_din  (alu_a_din),
        .ra_dout (a_dout),
        .rb_en   (alu_b_en),
        .rb_we   (alu_b_we),
        .rb_addr (alu_b_addr),
        .rb_din  (alu_b_din),
        .rb_dout (b_dout)
    );

    mlkem_polyram u_ram (
        .clk    (clk),
        .a_en   (tb_own ? tb_en   : alu_a_en),
        .a_we   (tb_own ? tb_we   : alu_a_we),
        .a_addr (tb_own ? tb_addr : alu_a_addr),
        .a_din  (tb_own ? tb_din  : alu_a_din),
        .a_dout (a_dout),
        .b_en   (alu_b_en),
        .b_we   (alu_b_we),
        .b_addr (alu_b_addr),
        .b_din  (alu_b_din),
        .b_dout (b_dout)
    );

    // ---------------------------------------------------------------------
    // Reference model (FIPS 203 Algorithms 9 and 10).
    // ---------------------------------------------------------------------
    integer zetas [0:127];
    integer f_in  [0:255];   // Input polynomial.
    integer f_ref [0:255];   // Reference result.
    integer f_out [0:255];   // DUT result (read back).

    function automatic integer bitrev7(input integer x);
        integer r;
        r = 0;
        for (int i = 0; i < 7; i++)
            if (x & (1 << i)) r = r | (1 << (6 - i));
        return r;
    endfunction

    function automatic integer powmod(input integer b, input integer e);
        integer r;
        r = 1;
        for (int i = 0; i < e; i++) r = (r * b) % Q;
        return r;
    endfunction

    // f_ref = NTT(f_ref), in place.
    task automatic ref_ntt;
        integer k, len, st, j, z, t;
        k = 1;
        for (len = 128; len >= 2; len = len / 2)
            for (st = 0; st < 256; st = st + 2 * len) begin
                z = zetas[k];
                k = k + 1;
                for (j = st; j < st + len; j++) begin
                    t = (z * f_ref[j + len]) % Q;
                    f_ref[j + len] = (f_ref[j] - t + Q) % Q;
                    f_ref[j]       = (f_ref[j] + t) % Q;
                end
            end
    endtask

    // f_ref = NTT^-1(f_ref), in place.
    task automatic ref_intt;
        integer k, len, st, j, z, t;
        k = 127;
        for (len = 2; len <= 128; len = len * 2)
            for (st = 0; st < 256; st = st + 2 * len) begin
                z = zetas[k];
                k = k - 1;
                for (j = st; j < st + len; j++) begin
                    t = f_ref[j];
                    f_ref[j]       = (t + f_ref[j + len]) % Q;
                    f_ref[j + len] = (z * ((f_ref[j + len] - t + Q) % Q)) % Q;
                end
            end
        for (j = 0; j < 256; j++) f_ref[j] = (f_ref[j] * 3303) % Q;
    endtask

    // ---------------------------------------------------------------------
    // RAM access through port A.
    // ---------------------------------------------------------------------
    task automatic load_poly(input logic [2:0] s);
        tb_own = 1'b1;
        for (int w = 0; w < 128; w++) begin
            @(negedge clk);
            tb_en   = 1'b1;
            tb_we   = 1'b1;
            tb_addr = {s, 7'(w)};
            tb_din  = {12'(f_in[2*w+1]), 12'(f_in[2*w])};
        end
        @(negedge clk);
        tb_en = 1'b0;
        tb_we = 1'b0;
    endtask

    // Fill a slot with a fixed pattern (guard slots).
    task automatic fill_slot(input logic [2:0] s, input logic [23:0] pattern);
        tb_own = 1'b1;
        for (int w = 0; w < 128; w++) begin
            @(negedge clk);
            tb_en = 1'b1; tb_we = 1'b1; tb_addr = {s, 7'(w)}; tb_din = pattern;
        end
        @(negedge clk);
        tb_en = 1'b0; tb_we = 1'b0;
    endtask

    task automatic read_poly(input logic [2:0] s);
        tb_own = 1'b1;
        for (int w = 0; w < 128; w++) begin
            @(negedge clk);
            tb_en = 1'b1; tb_we = 1'b0; tb_addr = {s, 7'(w)};
            @(posedge clk); #1;
            f_out[2*w]   = a_dout[11:0];
            f_out[2*w+1] = a_dout[23:12];
        end
        @(negedge clk);
        tb_en = 1'b0;
    endtask

    // Compare f_out with f_ref coefficient by coefficient (one per cycle,
    // so the comparison is visible as chk_* in the VCD).
    task automatic compare(input string what);
        integer bad;
        bad = 0;
        for (int i = 0; i < 256; i++) begin
            @(negedge clk);
            chk_valid = 1'b1;
            chk_idx   = 8'(i);
            chk_got   = 12'(f_out[i]);
            chk_exp   = 12'(f_ref[i]);
            chk_err   = (f_out[i] != f_ref[i]);
            if (chk_err) begin
                if (bad < 8)
                    $display("  [FAIL] %s f[%0d]: got %0d, expected %0d", what, i, f_out[i], f_ref[i]);
                bad = bad + 1;
            end
        end
        @(negedge clk);
        chk_valid = 1'b0;
        chk_err   = 1'b0;
        checks = checks + 1;
        if (bad == 0) $display("  [ OK ] %s: 256/256 coefficients match", what);
        else begin
            $display("  [FAIL] %s: %0d/256 coefficients wrong", what, bad);
            errors = errors + 1;
        end
    endtask

    task automatic run_op(input logic [2:0] o, input logic [2:0] s);
        @(negedge clk);
        tb_own = 1'b0;
        op     = o;
        slot   = s;
        start  = 1'b1;
        @(negedge clk);
        start  = 1'b0;
        op_cycles = 1;
        while (!done) begin
            @(negedge clk);
            op_cycles = op_cycles + 1;
        end
        @(negedge clk);
        tb_own = 1'b1;
        $display("  %s on slot %0d finished in %0d cycles",
                 (o == OP_NTT) ? "NTT " : "INTT", s, op_cycles);
    endtask

    // =====================================================================
    // Stimulus
    // =====================================================================
    integer seed;

    initial begin
        $dumpfile("tb_ntt.vcd");
        $dumpvars(0, tb_ntt);

        for (int i = 0; i < 128; i++) zetas[i] = powmod(17, bitrev7(i));
        $display("=== tb_ntt: ML-KEM NTT core (q=%0d) ===", Q);
        $display("  reference zetas[1]=%0d zetas[127]=%0d (FIPS 203: 1729, 2154)",zetas[1], zetas[127]);

        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (5) @(posedge clk);

        // ---- T1: impulse f = 1 -> NTT(f) = (1,0,1,0,...) ----
        test_name = "T1_ntt_delta";
        $display("--- T1: NTT of f(X) = 1 (slot 0) ---");
        for (int i = 0; i < 256; i++) f_in[i] = (i == 0) ? 1 : 0;
        for (int i = 0; i < 256; i++) f_ref[i] = f_in[i];
        ref_ntt();
        load_poly(3'd0);
        run_op(OP_NTT, 3'd0);
        read_poly(3'd0);
        compare("T1 NTT(1)");

        // ---- T2: ramp f[i] = i ----
        test_name = "T2_ntt_ramp";
        $display("--- T2: NTT of f[i] = i (slot 1) ---");
        for (int i = 0; i < 256; i++) f_in[i] = i;
        for (int i = 0; i < 256; i++) f_ref[i] = f_in[i];
        ref_ntt();
        load_poly(3'd1);
        run_op(OP_NTT, 3'd1);
        read_poly(3'd1);
        compare("T2 NTT(ramp)");

        // ---- T3: random polynomial, NTT then INTT round trip ----
        test_name = "T3_ntt_random";
        $display("--- T3: random f (slot 5), guard slots 4 and 6 ---");
        seed = 32'h2025_0203;
        for (int i = 0; i < 256; i++) f_in[i] = $unsigned($random(seed)) % Q;
        fill_slot(3'd4, 24'hA5A_5A5);
        fill_slot(3'd6, 24'h5A5_A5A);
        for (int i = 0; i < 256; i++) f_ref[i] = f_in[i];
        ref_ntt();
        load_poly(3'd5);
        run_op(OP_NTT, 3'd5);
        read_poly(3'd5);
        compare("T3 NTT(random)");

        test_name = "T4_intt_roundtrip";
        $display("--- T4: INTT(NTT(f)) == f (slot 5) ---");
        ref_intt();                       // f_ref = NTT^-1(NTT(f)).
        run_op(OP_INTT, 3'd5);
        read_poly(3'd5);
        compare("T4 INTT vs reference");
        for (int i = 0; i < 256; i++) f_ref[i] = f_in[i];
        compare("T4 INTT(NTT(f)) == f");

        test_name = "T5_guard_slots";
        $display("--- T5: neighbouring slots untouched ---");
        read_poly(3'd4);
        for (int i = 0; i < 256; i++) f_ref[i] = (i % 2) ? 12'hA5A : 12'h5A5;
        compare("T5 slot 4 intact");
        read_poly(3'd6);
        for (int i = 0; i < 256; i++) f_ref[i] = (i % 2) ? 12'h5A5 : 12'hA5A;
        compare("T5 slot 6 intact");

        test_name = "done";
        repeat (10) @(posedge clk);
        $display("=== tb_ntt: %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("TB_NTT PASS");
        else             $display("TB_NTT FAIL");
        $finish;
    end

    // Global watchdog.
    initial begin
        #(64'd5_000_000);  // 5 ms simulated.
        $display("TB_NTT FAIL: watchdog timeout");
        $finish;
    end

endmodule
