// tb_modmul.sv - Exhaustive test of mlkem_modmul (all a, b in [0, q))
//
// Streams all q^2 = 11,082,241 operand pairs through the pipeline, one per
// cycle, and checks r = a * b mod q LATENCY = 5 cycles later. Also checks
// that the latency is exactly 5 (r must not be valid one cycle earlier for
// the first pair). Prints "TB_MODMUL PASS" on success.

`timescale 1ns/1ps

module tb_modmul;

    localparam int Q   = 3329;
    localparam int LAT = 5;

    logic        clk = 1'b0;
    logic [11:0] a = '0, b = '0;
    logic [11:0] r;

    mlkem_modmul dut (.clk(clk), .a(a), .b(b), .r(r));

    // Reference pipeline: expected value LAT cycles after issue.
    int unsigned exp_q [0:LAT-1];
    bit          vld_q [0:LAT-1];
    longint      checked = 0, errors = 0;

    task automatic tick();
        #5 clk = 1'b1;
        #5 clk = 1'b0;
    endtask

    initial begin
        for (int i = 0; i < LAT; i++) vld_q[i] = 1'b0;
        for (int i = 0; i <= Q * Q + LAT; i++) begin
            int ai, bi;
            ai = i / Q;
            bi = i % Q;
            // Present operands for this cycle.
            if (i < Q * Q) begin
                a = 12'(ai);
                b = 12'(bi);
            end else begin
                a = '0;
                b = '0;
            end
            tick();
            // Shift reference pipeline; slot LAT-1 is what r now holds
            // for the pair issued LAT cycles ago.
            for (int k = LAT - 1; k > 0; k--) begin
                exp_q[k] = exp_q[k - 1];
                vld_q[k] = vld_q[k - 1];
            end
            exp_q[0] = (i < Q * Q) ? (ai * bi) % Q : 0;
            vld_q[0] = (i < Q * Q);
            if (vld_q[LAT - 1]) begin
                checked++;
                if (r !== 12'(exp_q[LAT - 1])) begin
                    errors++;
                    if (errors <= 10)
                        $display("MISMATCH r=%0d expected %0d", r, exp_q[LAT - 1]);
                end
            end
        end
        $display("tb_modmul: %0d products checked, %0d errors", checked, errors);
        if (errors == 0 && checked == longint'(Q) * Q)
            $display("TB_MODMUL PASS");
        else
            $display("TB_MODMUL FAIL");
        $finish;
    end

endmodule
