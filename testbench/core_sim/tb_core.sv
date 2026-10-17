// tb_core.sv - Batch testbench for the full ML-KEM core (pqc_mlkem_top)
//
// Runs a list of ML-KEM operations through the AXI-Lite interface exactly
// as the host does, and writes every result to a file. Driven by
// scripts/test/acvp_test.py (sim target).
//
// Plusargs:
//   +jobs=<file>   job list (below)
//   +out=<file>    results
//   +prof=<file>   optional: per-instruction cycle profile of every job
//   +trace=<file>  optional: instruction timeline of every job
//   +bmul=<file>   optional: BMUL datapath registers, every cycle a BMUL runs
//   +hd=<file>     optional: toggle count (Hamming distance to the previous
//                  cycle) of every register in bmul_regs.svh, every cycle a
//                  BMUL runs; +until=<cycle> stops the +bmul / +hd dumps at
//                  that busy cycle (4800 = just the three decryption BMULs)
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
// pc. pc = all-ones is the start-up C_NEXT that loads the program entry.
//
// Timeline file, one line per pc change (cycle = busy cycles before it, so
// cycle 0 is the cycle trig_out = STATUS.busy rises in, and CYCLE_COUNT
// counts the same cycles), plus one line per poly ALU start:
//   I <job> <cycle> <pc> <op>
//   A <job> <cycle> <alu_op> <slot_a> <slot_b> <slot_c> <acc>
//
// BMUL file, one line per busy cycle in which the poly ALU runs a BMUL
// (register values during that cycle, hex):
//   B <job> <cycle> <ra_en> <ra_addr> <rb_en> <rb_we> <rb_addr> <la> <lb>
//     <mul0 a> <mul0 b> <mul0 p1> <mul1 a> <mul1 b> <mul1 p1>
//     <p00> <p11> <h0> <h1d4> <wb> <wbd> <rb_din>
// Used by scripts/sim/bmul_timing.py to measure the BMUL windows and the
// pipeline stage of every register the leakage models name.
//
// HD file: "REG <i> <name> <bits>" header lines, then one line per BMUL cycle
//   H <job> <cycle> <hd of reg 0> <hd of reg 1> ...
// (the cycle's register values against the previous cycle's, i.e. the flops
// that toggle at the clock edge ending that cycle). Used by
// scripts/sim/register_leakage.py.
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

    always @(posedge clk) begin
        if (profiling && dut.u_ctrl.status_busy) begin
            int p;
            p = int'(dut.u_ctrl.pc);
            if (dut.u_ctrl.seq_overhead)
                prof_ovh[p]++;
            else begin
                prof_exec[p]++;
                prof_op[p] = dut.u_ctrl.i_op;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Instruction timeline and BMUL datapath dump (same busy cycles).
    // ---------------------------------------------------------------------
    int          ft = 0, fb = 0, fh = 0, job_idx = 0;
    int unsigned busy_cyc = 0;
    int          last_pc = -1;
    int unsigned dump_until = 32'hFFFF_FFFF;

    // The registers of bmul_regs.svh as 64-bit values (NREG of them).
    localparam int NREG = 0
`define R(NAME, EXPR, W) + 1
`include "bmul_regs.svh"
`undef R
    ;
    logic [63:0] reg_prev [NREG];

    function automatic void get_regs(output logic [63:0] v [NREG]);
        int k = 0;
`define R(NAME, EXPR, W) v[k] = 64'(EXPR); k++;
`include "bmul_regs.svh"
`undef R
    endfunction

    task automatic write_reg_header(input int f);
        int k = 0;
`define R(NAME, EXPR, W) $fwrite(f, "REG %0d %s %0d\n", k, NAME, W); k++;
`include "bmul_regs.svh"
`undef R
    endtask

    always @(posedge clk) begin
        if (profiling && dut.u_ctrl.status_busy) begin
            if (ft != 0 && int'(dut.u_ctrl.pc) != last_pc) begin
                last_pc = int'(dut.u_ctrl.pc);
                $fwrite(ft, "I %0d %0d %0d %0d\n", job_idx, busy_cyc, last_pc, dut.u_ctrl.i_op);
            end
            if (ft != 0 && dut.u_alu.start)
                $fwrite(ft, "A %0d %0d %0d %0d %0d %0d %0d\n", job_idx, busy_cyc, dut.u_alu.op,
                        dut.u_alu.slot_a, dut.u_alu.slot_b, dut.u_alu.slot_c, dut.u_alu.acc);
            if (fh != 0 && busy_cyc < dump_until && dut.u_alu.is_bmul && (dut.u_alu.running || dut.u_alu.pv != '0)) begin
                logic [63:0] cur [NREG];
                get_regs(cur);
                $fwrite(fh, "H %0d %0d", job_idx, busy_cyc);
                for (int i = 0; i < NREG; i++)
                    $fwrite(fh, " %0d", $countones(cur[i] ^ reg_prev[i]));
                $fwrite(fh, "\n");
            end
            if (fh != 0) get_regs(reg_prev);
            if (fb != 0 && busy_cyc < dump_until && dut.u_alu.is_bmul && (dut.u_alu.running || dut.u_alu.pv != '0))
                $fwrite(fb, "B %0d %0d %0d %h %0d %0d %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h\n",
                        job_idx, busy_cyc, dut.u_alu.ra_en, dut.u_alu.ra_addr, dut.u_alu.rb_en,
                        dut.u_alu.rb_we, dut.u_alu.rb_addr, dut.u_alu.la, dut.u_alu.lb,
                        dut.u_alu.u_mul0.a0, dut.u_alu.u_mul0.b0, dut.u_alu.u_mul0.p1,
                        dut.u_alu.u_mul1.a0, dut.u_alu.u_mul1.b0, dut.u_alu.u_mul1.p1,
                        dut.u_alu.p00, dut.u_alu.p11, dut.u_alu.h0, dut.u_alu.h1d[4],
                        dut.u_alu.wb, dut.u_alu.wbd, dut.u_alu.rb_din);
            busy_cyc++;
        end
    end

    // ---------------------------------------------------------------------
    // Job runner.
    // ---------------------------------------------------------------------
    logic [7:0]  in_bytes [0:MAX_IN-1];
    string       jobs_file, out_file, prof_file, trace_file, bmul_file, hd_file, hex;
    int          fj, fo, fp, njobs, op, level, in_len, r, t;
    logic [31:0] in_addr, out_addr, status, cycles, err, out_len, w;
    bit          do_prof;

    initial begin
        if (!$value$plusargs("jobs=%s", jobs_file) || !$value$plusargs("out=%s", out_file)) begin
            $display("usage: +jobs=<file> +out=<file> [+prof=<file>]");
            $fatal(1);
        end
        do_prof = $value$plusargs("prof=%s", prof_file);
        if ($value$plusargs("trace=%s", trace_file)) ft = $fopen(trace_file, "w");
        if ($value$plusargs("bmul=%s", bmul_file)) fb = $fopen(bmul_file, "w");
        if ($value$plusargs("hd=%s", hd_file)) begin
            fh = $fopen(hd_file, "w");
            write_reg_header(fh);
        end
        if ($value$plusargs("until=%d", dump_until)) ;
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
            job_idx   = j;
            busy_cyc  = 0;
            last_pc   = -1;
            profiling = do_prof || ft != 0 || fb != 0 || fh != 0;
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
        if (ft != 0) $fclose(ft);
        if (fb != 0) $fclose(fb);
        if (fh != 0) $fclose(fh);
        $display("TB_CORE DONE %0d jobs", njobs);
        $finish;
    end

endmodule
