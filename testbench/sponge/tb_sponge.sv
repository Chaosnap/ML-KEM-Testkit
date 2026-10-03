// tb_sponge.sv - keccak_sponge against Python hashlib (SHA3-256/512, SHAKE128/256)
//
// For every vector from gen_vectors.py: init, absorb the message in random
// chunks (1 .. bytes left in the current lane, so lane boundaries are
// respected but chunks are often unaligned), finalize, then squeeze the
// output in random chunks (half a lane or all of squeeze_avail). Valid /
// take are dropped at
// random to exercise stalls. Also checks that a full SHAKE128 block costs
// 21 absorb cycles + 1 + 48 permutation cycles.
// Prints "TB_SPONGE PASS" on success.
//
// Plusarg +vec=<vector file> (default "sponge_vectors.txt").

`timescale 1ns/1ps

module tb_sponge;

    logic        clk = 1'b0;
    logic        rst_n = 1'b0;
    always #5 clk = ~clk;

    logic        init = 1'b0, absorb_valid = 1'b0, finalize = 1'b0, squeeze_take = 1'b0;
    logic [1:0]  mode = '0;
    logic [63:0] absorb_data = '0;
    logic [3:0]  absorb_n = 4'd1, squeeze_n = 4'd1;
    logic        idle, absorb_ready, squeeze_valid;
    logic [63:0] squeeze_data;
    logic [3:0]  squeeze_avail;

    keccak_sponge dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .init          (init),
        .mode          (mode),
        .idle          (idle),
        .absorb_valid  (absorb_valid),
        .absorb_data   (absorb_data),
        .absorb_n      (absorb_n),
        .absorb_ready  (absorb_ready),
        .finalize      (finalize),
        .squeeze_valid (squeeze_valid),
        .squeeze_data  (squeeze_data),
        .squeeze_avail (squeeze_avail),
        .squeeze_take  (squeeze_take),
        .squeeze_n     (squeeze_n)
    );

    logic [7:0]  msg [0:4095];
    logic [7:0]  exp_o [0:4095];
    logic [7:0]  got [0:4095];
    logic [31:0] lfsr = 32'hACE1;
    string       vec;
    int          fd, r, nt, m, ml, ol, errors = 0, checked = 0;
    logic [31:0] w;

    function automatic int rnd(input int n);   // 0 .. n-1
        lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        return int'(lfsr[15:0]) % n;
    endfunction

    task automatic run_one();
        int i, off, n, k, cyc;
        // Init (only accepted while idle: the last squeeze may have
        // started a permutation).
        @(negedge clk);
        while (!idle) @(negedge clk);
        mode = 2'(m);
        init = 1'b1;
        @(negedge clk);
        init = 1'b0;
        // Absorb.
        i = 0;
        off = 0;
        while (i < ml) begin
            n = 1 + rnd(8 - off);
            if (n > ml - i) n = ml - i;
            if (rnd(4) == 0) begin              // Random bubble.
                absorb_valid = 1'b0;
                @(negedge clk);
                continue;
            end
            absorb_valid = 1'b1;
            absorb_n = 4'(n);
            absorb_data = {$urandom, $urandom};  // Bytes beyond n must be ignored.
            for (k = 0; k < n; k++) absorb_data[8 * k +: 8] = msg[i + k];
            @(posedge clk);
            if (absorb_ready) begin
                i += n;
                off = (off + n) % 8;
            end
            @(negedge clk);
        end
        absorb_valid = 1'b0;
        // Finalize.
        finalize = 1'b1;
        do @(posedge clk); while (!absorb_ready);
        @(negedge clk);
        finalize = 1'b0;
        // Squeeze.
        i = 0;
        while (i < ol) begin
            if (!squeeze_valid || rnd(4) == 0) begin
                @(negedge clk);
                continue;
            end
            n = (squeeze_avail == 4'd8 && rnd(2) == 0) ? 4 : int'(squeeze_avail);
            if (n > ol - i) n = ol - i;
            for (k = 0; k < n; k++) got[i + k] = squeeze_data[8 * k +: 8];
            squeeze_take = 1'b1;
            squeeze_n = 4'(n);
            @(negedge clk);
            squeeze_take = 1'b0;
            i += n;
        end
        for (k = 0; k < ol; k++) begin
            checked++;
            if (got[k] !== exp_o[k]) begin
                errors++;
                if (errors <= 10)
                    $display("mode %0d msglen %0d outlen %0d: byte %0d got %02x expected %02x",
                             m, ml, ol, k, got[k], exp_o[k]);
            end
        end
    endtask

    // A full SHAKE128 block absorbed one lane per cycle: 21 + 24 cycles until ready.
    task automatic check_block_timing();
        int cyc;
        @(negedge clk);
        while (!idle) @(negedge clk);
        mode = 2'd2;
        init = 1'b1;
        @(negedge clk);
        init = 1'b0;
        absorb_valid = 1'b1;
        absorb_n = 4'd8;
        absorb_data = 64'h0123_4567_89AB_CDEF;
        cyc = 0;
        for (int l = 0; l < 21; l++) begin
            @(posedge clk);
            cyc++;
            if (!absorb_ready) begin errors++; $display("absorb stalled at lane %0d", l); end
            @(negedge clk);
        end
        absorb_valid = 1'b0;
        while (!absorb_ready) begin @(posedge clk); cyc++; @(negedge clk); end
        $display("SHAKE128 block: %0d cycles (21 lanes + 1 + 48 permutation cycles)", cyc);
        if (cyc != 70) begin errors++; $display("expected 70 cycles per block"); end
    endtask

    task automatic reset_during_permutation(input int phase_delay);
        @(negedge clk);
        while (!idle) @(negedge clk);
        init = 1'b1;
        @(negedge clk); init = 1'b0; finalize = 1'b1;
        do @(posedge clk); while (!absorb_ready);
        @(negedge clk); finalize = 1'b0;
        while (!dut.running) @(negedge clk);
        repeat (phase_delay) @(negedge clk);
        rst_n = 1'b0;
        repeat (4) @(negedge clk);
        rst_n = 1'b1;
        repeat (4) @(negedge clk);
        if (!idle || squeeze_valid) $fatal(1, "sponge reset did not abort permutation");
        run_one(); // Retained last hashlib vector must still match after reset.
    endtask

    initial begin
        if (!$value$plusargs("vec=%s", vec)) vec = "sponge_vectors.txt";
        fd = $fopen(vec, "r");
        if (fd == 0) begin $display("cannot open %s", vec); $fatal(1); end
        r = $fscanf(fd, "%d", nt);
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        for (int t = 0; t < nt; t++) begin
            r = $fscanf(fd, "%d %d %d", m, ml, ol);
            for (int i = 0; i < ml; i++) begin r = $fscanf(fd, "%h", w); msg[i] = w[7:0]; end
            for (int i = 0; i < ol; i++) begin r = $fscanf(fd, "%h", w); exp_o[i] = w[7:0]; end
            run_one();
        end
        reset_during_permutation(1);
        reset_during_permutation(2);
        check_block_timing();
        $display("tb_sponge: %0d vectors, %0d output bytes checked, %0d errors", nt, checked, errors);
        if (errors == 0 && checked > 0) $display("TB_SPONGE PASS");
        else $display("TB_SPONGE FAIL");
        $finish;
    end

endmodule
