// tb_core.sv - Batch testbench for the full ML-KEM core (pqc_mlkem_top)
//
// Runs a list of ML-KEM operations through the AXI-Lite interface exactly
// as the host does, and writes every result to a file. Driven by
// scripts/coresim.py (scripts/sim_acvp.py, scripts/profile_ucode.py).
//
// Plusargs:
//   +jobs=<file>   job list (below)
//   +out=<file>    results
//   +prof=<file>   optional: per-instruction cycle profile of every job
//
// Job file (whitespace separated, hex bytes without 0x):
//   <njobs>
//   <op> <sec_level> <inlen> <b0> <b1> ... <b(inlen-1)>      (repeated)
//
// Result file, one line per job:
//   JOB <i> status=<STATUS> err=<ERROR_CODE> cycles=<CYCLE_COUNT> len=<n> out=<hex>
//
// Profile file, one line per (job, pc) with non-zero counts:
//   P <job> <pc> <op> <exec_cycles> <overhead_cycles>
// Every busy cycle is counted exactly once: C_FETCH / C_DECODE / C_NEXT are
// sequencer overhead, all other states are execution of the instruction at
// pc. pc = 2047 is the start-up C_NEXT that loads the program entry.
//
// Input and output offsets are taken from the reset values of DATA_IN_ADDR
// and DATA_OUT_ADDR, so the testbench follows the buffer layout of the RTL.

`timescale 1ns/1ps

module tb_core;

    localparam int MAX_IN  = 8192;
    localparam int TIMEOUT = 2_000_000;

    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk = ~clk;   // 100 MHz

    logic [15:0] awaddr = '0, araddr = '0;
    logic        awvalid = 1'b0, wvalid = 1'b0, arvalid = 1'b0;
    logic [31:0] wdata = '0;
    logic        awready, wready, bvalid, arready, rvalid;
    logic [1:0]  bresp, rresp;
    logic [31:0] rdata;
    logic        irq, o_busy, o_done, o_error;

    pqc_mlkem_top dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .s_axi_awaddr  (awaddr),
        .s_axi_awvalid (awvalid),
        .s_axi_awready (awready),
        .s_axi_wdata   (wdata),
        .s_axi_wstrb   (4'hF),
        .s_axi_wvalid  (wvalid),
        .s_axi_wready  (wready),
        .s_axi_bresp   (bresp),
        .s_axi_bvalid  (bvalid),
        .s_axi_bready  (1'b1),
        .s_axi_araddr  (araddr),
        .s_axi_arvalid (arvalid),
        .s_axi_arready (arready),
        .s_axi_rdata   (rdata),
        .s_axi_rresp   (rresp),
        .s_axi_rvalid  (rvalid),
        .s_axi_rready  (1'b1),
        .irq           (irq),
        .o_busy        (o_busy),
        .o_done        (o_done),
        .o_error       (o_error)
    );

    task automatic axi_write(input logic [15:0] addr, input logic [31:0] data);
        @(negedge clk);
        awaddr  = addr;
        wdata   = data;
        awvalid = 1'b1;
        wvalid  = 1'b1;
        do @(posedge clk); while (!(awready && wready));
        @(negedge clk);
        awvalid = 1'b0;
        wvalid  = 1'b0;
        do @(posedge clk); while (!bvalid);
    endtask

    task automatic axi_read(input logic [15:0] addr, output logic [31:0] data);
        @(negedge clk);
        araddr  = addr;
        arvalid = 1'b1;
        do @(posedge clk); while (!arready);
        @(negedge clk);
        arvalid = 1'b0;
        while (!rvalid) @(posedge clk);
        data = rdata;
        @(posedge clk);
    endtask

    // ---------------------------------------------------------------------
    // Per-instruction profile (sequencer state sampled on every busy cycle,
    // the same cycles CYCLE_COUNT counts).
    // ---------------------------------------------------------------------
    int unsigned prof_exec [0:2047];
    int unsigned prof_ovh  [0:2047];
    logic [7:0]  prof_op   [0:2047];
    bit          profiling = 1'b0;

    // mlkem_ctrl state_t: C_NEXT = 12, C_FETCH = 1, C_DECODE = 2.
    always @(posedge clk) begin
        if (profiling && dut.u_ctrl.status_busy) begin
            int s, p;
            s = int'(dut.u_ctrl.state);
            p = int'(dut.u_ctrl.pc);
            if (s == 1 || s == 2 || s == 12)
                prof_ovh[p]++;
            else begin
                prof_exec[p]++;
                prof_op[p] = dut.u_ctrl.i_op;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Job runner.
    // ---------------------------------------------------------------------
    logic [7:0]  in_bytes [0:MAX_IN-1];
    string       jobs_file, out_file, prof_file, hex;
    int          fj, fo, fp, njobs, op, level, in_len, r, t;
    logic [31:0] in_addr, out_addr, status, cycles, err, out_len, w;
    bit          do_prof;

    initial begin
        if (!$value$plusargs("jobs=%s", jobs_file) || !$value$plusargs("out=%s", out_file)) begin
            $display("usage: +jobs=<file> +out=<file> [+prof=<file>]");
            $fatal(1);
        end
        do_prof = $value$plusargs("prof=%s", prof_file);
        fj = $fopen(jobs_file, "r");
        fo = $fopen(out_file, "w");
        if (fj == 0 || fo == 0) begin
            $display("cannot open job/result file");
            $fatal(1);
        end
        if (do_prof) fp = $fopen(prof_file, "w");
        r = $fscanf(fj, "%d", njobs);

        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        axi_read(16'h0020, in_addr);
        axi_read(16'h0028, out_addr);

        for (int j = 0; j < njobs; j++) begin
            r = $fscanf(fj, "%d %d %d", op, level, in_len);
            if (r != 3 || in_len < 0 || in_len > MAX_IN) begin
                $display("bad job header %0d", j);
                $fatal(1);
            end
            for (int i = 0; i < MAX_IN; i++) in_bytes[i] = 8'h00;
            for (int i = 0; i < in_len; i++) begin
                r = $fscanf(fj, "%h", w);
                in_bytes[i] = w[7:0];
            end

            for (int i = 0; i < in_len; i += 4)
                axi_write(16'h4000 + 16'(in_addr) + 16'(i),
                          {in_bytes[i + 3], in_bytes[i + 2], in_bytes[i + 1], in_bytes[i]});
            axi_write(16'h0024, 32'(in_len));
            axi_write(16'h000C, 32'(level));
            axi_write(16'h0010, 32'(op));

            for (int i = 0; i < 2048; i++) begin
                prof_exec[i] = 0;
                prof_ovh[i]  = 0;
                prof_op[i]   = 8'h00;
            end
            profiling = do_prof;
            axi_write(16'h0000, 32'h1);
            t = 0;
            do begin
                @(posedge clk);
                t++;
            end while (!(o_done || o_error) && t < TIMEOUT);
            repeat (2) @(posedge clk);
            profiling = 1'b0;

            axi_read(16'h0004, status);
            axi_read(16'h001C, err);
            axi_read(16'h0014, cycles);
            axi_read(16'h002C, out_len);
            if (t >= TIMEOUT) status = 32'hFFFF_FFFF;

            hex = "";
            if (status == 32'd2) begin   // done, no error
                for (int i = 0; i < int'(out_len); i += 4) begin
                    axi_read(16'h4000 + 16'(out_addr) + 16'(i), w);
                    for (int b = 0; b < 4 && i + b < int'(out_len); b++)
                        hex = {hex, $sformatf("%02x", w[8*b +: 8])};
                end
            end
            $fwrite(fo, "JOB %0d status=%0d err=%0d cycles=%0d len=%0d out=%s\n",
                    j, status, err, cycles, out_len, hex);
            if (do_prof)
                for (int i = 0; i < 2048; i++)
                    if (prof_exec[i] != 0 || prof_ovh[i] != 0)
                        $fwrite(fp, "P %0d %0d %0d %0d %0d\n",
                                j, i, prof_op[i], prof_exec[i], prof_ovh[i]);

            if (o_error) axi_write(16'h0000, 32'h2);   // CTRL.reset clears error
        end

        $fclose(fo);
        if (do_prof) $fclose(fp);
        $display("TB_CORE DONE %0d jobs", njobs);
        $finish;
    end

endmodule
