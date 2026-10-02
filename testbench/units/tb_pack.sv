// tb_pack.sv - mlkem_pack against FIPS 203 ByteEncode_d(Compress_d(f))
//
// Polys from gen_vectors.py cover every x in [0, q). For each d the TB loads
// each poly into a behavioural poly RAM (2-cycle read latency, like
// mlkem_polyram), runs mlkem_pack and compares the emitted stream (32-bit
// little-endian words) with the Python reference. Prints "TB_PACK PASS" on success.
//
// Plusarg +dir=<vector directory> (default ".").

`timescale 1ns/1ps

module tb_pack;

    localparam int NP = 14;                 // ceil(q / 256) polys.
    localparam int ND = 6;
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
    logic [3:0]  d = '0;
    logic [2:0]  slot = '0;
    logic        done, rd_en, out_valid;
    logic [9:0]  rd_addr;
    logic [23:0] rd_data;
    logic [31:0] out_word;

    mlkem_pack dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .clr       (1'b0),
        .start     (start),
        .d         (d),
        .slot      (slot),
        .done      (done),
        .rd_en     (rd_en),
        .rd_addr   (rd_addr),
        .rd_data   (rd_data),
        .out_valid (out_valid),
        .out_word  (out_word)
    );

    logic [23:0] ram [0:1023];
    logic [23:0] rd_q;
    always_ff @(posedge clk) begin                  // Like mlkem_polyram: latency 2.
        if (rd_en) rd_q <= ram[rd_addr];
        rd_data <= rd_q;
    end

    logic [11:0] coefs [0:NP*256-1];
    logic [7:0]  exp_b [0:NP*32*12-1];
    logic [7:0]  got   [0:32*12-1];
    int          ngot;
    string       dir;
    int          errors = 0, checked = 0;

    always @(posedge clk)
        if (out_valid)
            for (int b = 0; b < 4; b++) begin
                if (ngot < 32 * 12) got[ngot] = out_word[8 * b +: 8];
                ngot++;
            end

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        $readmemh({dir, "/pack_coefs.hex"}, coefs);
        repeat (3) @(posedge clk);
        rst_n = 1'b1;

        for (int di = 0; di < ND; di++) begin
            int dd;
            dd = ds(di);
            $readmemh($sformatf("%s/pack_exp_d%0d.hex", dir, dd), exp_b, 0, NP * 32 * dd - 1);
            for (int p = 0; p < NP; p++) begin
                int s;
                s = p % 8;
                for (int w = 0; w < 128; w++)
                    ram[s * 128 + w] = {coefs[p * 256 + 2 * w + 1], coefs[p * 256 + 2 * w]};
                ngot = 0;
                @(negedge clk);
                d = 4'(dd);
                slot = 3'(s);
                start = 1'b1;
                @(negedge clk);
                start = 1'b0;
                while (!done) @(negedge clk);
                @(negedge clk);
                if (ngot != 32 * dd) begin
                    errors++;
                    $display("d=%0d poly %0d: %0d bytes, expected %0d", dd, p, ngot, 32 * dd);
                end else begin
                    for (int i = 0; i < 32 * dd; i++) begin
                        checked++;
                        if (got[i] !== exp_b[p * 32 * dd + i]) begin
                            errors++;
                            if (errors <= 10)
                                $display("d=%0d poly %0d byte %0d: got %02x expected %02x",
                                         dd, p, i, got[i], exp_b[p * 32 * dd + i]);
                        end
                    end
                end
            end
        end
        $display("tb_pack: %0d bytes checked, %0d errors", checked, errors);
        if (errors == 0 && checked > 0) $display("TB_PACK PASS");
        else $display("TB_PACK FAIL");
        $finish;
    end

endmodule
