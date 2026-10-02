// tb_unpack.sv - mlkem_unpack against FIPS 203 (DECODE, SAMPLE, CBD)
//
// Byte streams from gen_vectors.py are fed through the chunk source
// interface (8-byte chunks) with random stalls; the
// poly RAM writes are captured and compared with the Python reference:
//   DECODE d in {1,4,5,10,11,12}: Decompress_d(ByteDecode_d), every field
//          value covered; d = 12 also counts range_err (modulus check)
//   SAMPLE: SampleNTT (Algorithm 7) on random 672-byte strings
//   CBD eta in {2,3}: SamplePolyCBD_eta (Algorithm 8)
// DECODE and CBD sources hold exactly the bytes the poly needs, and the
// TB checks they are all consumed. Prints "TB_UNPACK PASS" on success.
//
// Plusarg +dir=<vector directory> (default ".").

`timescale 1ns/1ps

module tb_unpack;

    localparam int MAXP = 16;

    // d values under test (function: Icarus has no unpacked array parameters).
    function automatic int ds(input int i);
        case (i)
            0: return 1;   1: return 4;   2: return 5;
            3: return 10;  4: return 11;  default: return 12;
        endcase
    endfunction

    logic        clk = 1'b0;
    logic        rst_n = 1'b0;
    always #5 clk = ~clk;

    logic        start = 1'b0;
    logic [1:0]  mode = 2'd2;
    logic [3:0]  param = '0;
    logic        check = 1'b0;
    logic [2:0]  slot = '0;
    logic        done, src_take, wr_en;
    logic [1:0]  range_err;
    logic        src_valid;
    logic [63:0] src_data;
    logic [9:0]  wr_addr;
    logic [23:0] wr_data;

    mlkem_unpack dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .clr       (1'b0),
        .start     (start),
        .mode      (mode),
        .param     (param),
        .check     (check),
        .slot      (slot),
        .done      (done),
        .range_err (range_err),
        .src_valid (src_valid),
        .src_data  (src_data),
        .src_take  (src_take),
        .wr_en     (wr_en),
        .wr_addr   (wr_addr),
        .wr_data   (wr_data)
    );

    logic [7:0]  bytes [0:MAXP*32*12-1];
    logic [11:0] exp_c [0:MAXP*256-1];
    logic [11:0] exp_e [0:MAXP-1];
    logic [23:0] ram   [0:1023];
    int          ptr = 0, lim = 0, nerr, nwr;
    logic [31:0] lfsr = 32'h1;
    string       dir;
    int          errors = 0, checked = 0;

    // Chunk source: 8 bytes, valid about 3/4 of the time, never past lim.
    always @(*) begin
        src_valid = (lfsr[1:0] != 2'b00) && (ptr + 8 <= lim);
        for (int i = 0; i < 8; i++)
            src_data[8 * i +: 8] = bytes[ptr + i];
    end

    always @(posedge clk) begin
        lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        if (src_take) ptr <= ptr + 8;
        if (wr_en) begin
            ram[wr_addr] <= wr_data;
            nwr <= nwr + 1;
        end
        nerr <= nerr + int'(range_err[0]) + int'(range_err[1]);
    end

    // Run one poly: bytes [base, base + nbytes) in slot s.
    task automatic run_poly(input logic [1:0] m, input int pr, input bit chk,
                            input int s, input int base, input int nbytes);
        @(negedge clk);
        ptr = base;
        lim = base + nbytes;
        nerr = 0;
        nwr = 0;
        mode = m;
        param = 4'(pr);
        check = chk;
        slot = 3'(s);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;
        for (int t = 0; !done; t++) begin
            if (t == 20000) begin
                $display("TIMEOUT mode=%0d param=%0d slot=%0d: ptr=%0d lim=%0d", m, pr, s, ptr, lim);
                $fatal(1);
            end
            @(negedge clk);
        end
        repeat (2) @(negedge clk);
    endtask

    task automatic compare(input string what, input int s, input int ebase);
        if (nwr != 128) begin
            errors++;
            $display("%s: %0d RAM writes, expected 128", what, nwr);
        end
        for (int i = 0; i < 256; i++) begin
            logic [11:0] c;
            c = ram[s * 128 + i / 2][12 * (i % 2) +: 12];
            checked++;
            if (c !== exp_c[ebase + i]) begin
                errors++;
                if (errors <= 10)
                    $display("%s coef %0d: got %0d expected %0d", what, i, c, exp_c[ebase + i]);
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        repeat (3) @(posedge clk);
        rst_n = 1'b1;

        // DECODE.
        for (int di = 0; di < 6; di++) begin
            int dd, np;
            dd = ds(di);
            np = ((1 << dd) / 256 < 2) ? 2 : (1 << dd) / 256;
            $readmemh($sformatf("%s/unpack_bytes_d%0d.hex", dir, dd), bytes, 0, np * 32 * dd - 1);
            $readmemh($sformatf("%s/unpack_exp_d%0d.hex", dir, dd), exp_c, 0, np * 256 - 1);
            $readmemh($sformatf("%s/unpack_err_d%0d.hex", dir, dd), exp_e, 0, np - 1);
            for (int p = 0; p < np; p++) begin
                run_poly(2'd2, dd, dd == 12, p % 8, p * 32 * dd, 32 * dd);
                if (ptr != lim) begin
                    errors++;
                    $display("DECODE d=%0d poly %0d: %0d bytes left unconsumed", dd, p, lim - ptr);
                end
                if (nerr != int'(exp_e[p])) begin
                    errors++;
                    $display("DECODE d=%0d poly %0d: %0d range_err, expected %0d", dd, p, nerr, exp_e[p]);
                end
                compare($sformatf("DECODE d=%0d poly %0d", dd, p), p % 8, p * 256);
            end
        end

        // SAMPLE (may stop before the end of the string).
        $readmemh({dir, "/sample_bytes.hex"}, bytes, 0, 8 * 672 - 1);
        $readmemh({dir, "/sample_exp.hex"}, exp_c, 0, 8 * 256 - 1);
        for (int p = 0; p < 8; p++) begin
            run_poly(2'd0, 0, 1'b0, p, p * 672, 672);
            compare($sformatf("SAMPLE poly %0d", p), p, p * 256);
        end

        // CBD eta = 2, 3.
        for (int eta = 2; eta <= 3; eta++) begin
            $readmemh($sformatf("%s/cbd%0d_bytes.hex", dir, eta), bytes, 0, 4 * 64 * eta - 1);
            $readmemh($sformatf("%s/cbd%0d_exp.hex", dir, eta), exp_c, 0, 4 * 256 - 1);
            for (int p = 0; p < 4; p++) begin
                run_poly(2'd1, eta, 1'b0, p, p * 64 * eta, 64 * eta);
                if (ptr != lim) begin
                    errors++;
                    $display("CBD%0d poly %0d: %0d bytes left unconsumed", eta, p, lim - ptr);
                end
                compare($sformatf("CBD%0d poly %0d", eta, p), p, p * 256);
            end
        end

        $display("tb_unpack: %0d coefficients checked, %0d errors", checked, errors);
        if (errors == 0 && checked > 0) $display("TB_UNPACK PASS");
        else $display("TB_UNPACK FAIL");
        $finish;
    end

endmodule
