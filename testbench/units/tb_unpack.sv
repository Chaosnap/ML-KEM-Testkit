// tb_unpack.sv - mlkem_unpack DECODE mode against FIPS 203
//
// For each d, streams ByteEncode_d of polys whose fields cover every value
// in [0, 2^d) into mlkem_unpack (MODE_DECODE, modulus check on for d = 12),
// with a byte source that stalls pseudo-randomly, captures the poly RAM
// writes and compares them with Decompress_d(ByteDecode_d) (field mod q for
// d = 12) from gen_vectors.py; for d = 12 also counts range_err pulses.
// Prints "TB_UNPACK PASS" on success.
//
// Plusarg +dir=<vector directory> (default ".").

`timescale 1ns/1ps

module tb_unpack;

    localparam int ND = 6;
    // d values under test (function: Icarus has no unpacked array parameters).
    function automatic int ds(input int i);
        case (i)
            0: return 1;   1: return 4;   2: return 5;
            3: return 10;  4: return 11;  default: return 12;
        endcase
    endfunction
    localparam int MAXP = 16;

    logic        clk = 1'b0;
    logic        rst_n = 1'b0;
    always #5 clk = ~clk;

    logic        start = 1'b0;
    logic [3:0]  param = '0;
    logic        check = 1'b0;
    logic [2:0]  slot = '0;
    logic        done, range_err, src_take, wr_en;
    logic        src_valid;
    logic [7:0]  src_byte;
    logic [9:0]  wr_addr;
    logic [23:0] wr_data;

    mlkem_unpack dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .clr       (1'b0),
        .start     (start),
        .mode      (2'd2),           // MODE_DECODE
        .param     (param),
        .check     (check),
        .slot      (slot),
        .done      (done),
        .range_err (range_err),
        .src_valid (src_valid),
        .src_byte  (src_byte),
        .src_take  (src_take),
        .wr_en     (wr_en),
        .wr_addr   (wr_addr),
        .wr_data   (wr_data)
    );

    logic [7:0]  bytes [0:MAXP*32*12-1];
    logic [11:0] exp_c [0:MAXP*256-1];
    logic [11:0] exp_e [0:MAXP-1];
    logic [23:0] ram   [0:1023];
    int          ptr, nerr, nwr;
    logic [31:0] lfsr = 32'h1;
    string       dir;
    int          errors = 0, checked = 0;

    // Byte source: valid about 3/4 of the time.
    assign src_valid = lfsr[1:0] != 2'b00;
    assign src_byte  = bytes[ptr];

    always @(posedge clk) begin
        lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        if (src_take) ptr <= ptr + 1;
        if (wr_en) begin
            ram[wr_addr] <= wr_data;
            nwr <= nwr + 1;
        end
        if (range_err) nerr <= nerr + 1;
    end

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        repeat (3) @(posedge clk);
        rst_n = 1'b1;

        for (int di = 0; di < ND; di++) begin
            int dd, np;
            dd = ds(di);
            np = ((1 << dd) / 256 < 2) ? 2 : (1 << dd) / 256;
            $readmemh($sformatf("%s/unpack_bytes_d%0d.hex", dir, dd), bytes, 0, np * 32 * dd - 1);
            $readmemh($sformatf("%s/unpack_exp_d%0d.hex", dir, dd), exp_c, 0, np * 256 - 1);
            $readmemh($sformatf("%s/unpack_err_d%0d.hex", dir, dd), exp_e, 0, np - 1);
            for (int p = 0; p < np; p++) begin
                int s;
                s = p % 8;
                @(negedge clk);
                ptr = p * 32 * dd;
                nerr = 0;
                nwr = 0;
                param = 4'(dd);
                check = (dd == 12);
                slot = 3'(s);
                start = 1'b1;
                @(negedge clk);
                start = 1'b0;
                while (!done) @(negedge clk);
                repeat (2) @(negedge clk);
                if (ptr != (p + 1) * 32 * dd) begin
                    errors++;
                    $display("d=%0d poly %0d: consumed %0d bytes, expected %0d",
                             dd, p, ptr - p * 32 * dd, 32 * dd);
                end
                if (nwr != 128) begin
                    errors++;
                    $display("d=%0d poly %0d: %0d RAM writes, expected 128", dd, p, nwr);
                end
                if (nerr != int'(exp_e[p])) begin
                    errors++;
                    $display("d=%0d poly %0d: %0d range_err pulses, expected %0d",
                             dd, p, nerr, exp_e[p]);
                end
                for (int i = 0; i < 256; i++) begin
                    logic [11:0] c;
                    c = ram[s * 128 + i / 2][12 * (i % 2) +: 12];
                    checked++;
                    if (c !== exp_c[p * 256 + i]) begin
                        errors++;
                        if (errors <= 10)
                            $display("d=%0d poly %0d coef %0d: got %0d expected %0d",
                                     dd, p, i, c, exp_c[p * 256 + i]);
                    end
                end
            end
        end
        $display("tb_unpack: %0d coefficients checked, %0d errors", checked, errors);
        if (errors == 0 && checked > 0) $display("TB_UNPACK PASS");
        else $display("TB_UNPACK FAIL");
        $finish;
    end

endmodule
