// tb_tvla.sv - Single-trace testbench for simulation-based TVLA
//
// Runs one ML-KEM operation on pqc_mlkem_top and dumps a VCD of the core
// for exactly the duration of the operation. Driven once per trace by
// `pqc-testkit sca sim`; see docs/SIM_TVLA_GUIDE.md.
//
// Plusargs:
//   +in=<file>   input bytes, one hex byte per line ($readmemh)
//   +len=<n>     number of input bytes
//   +op=<n>      OP_MODE: 0 KeyGen, 2 Decaps
//   +vcd=<file>  VCD output path
//
// Prints "TVLA_RESULT status=<STATUS> cycles=<CYCLE_COUNT>" on completion.
//
// The dump starts one cycle before the start pulse and stops when the core
// raises done or error, so the first rising clock edge in the VCD is cycle
// 0 of the trace. The host interface is idle while dumping (completion is
// taken from o_done/o_error, not by polling STATUS over AXI), so the only
// activity in the waveform comes from the core itself.

`timescale 1ns/1ps

module tb_tvla;

    localparam int MAX_IN = 4096;

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

    logic [7:0]  in_bytes [0:MAX_IN-1];
    string       in_file, vcd_file;
    int          in_len, op;
    logic [31:0] status, cycles;

    initial begin
        if (!$value$plusargs("in=%s", in_file) || !$value$plusargs("len=%d", in_len) ||
            !$value$plusargs("op=%d", op) || !$value$plusargs("vcd=%s", vcd_file)) begin
            $display("usage: +in=<file> +len=<bytes> +op=<0|2> +vcd=<file>");
            $fatal(1);
        end
        if (in_len <= 0 || in_len > MAX_IN) begin
            $display("bad +len=%0d", in_len);
            $fatal(1);
        end
        for (int i = 0; i < MAX_IN; i++) in_bytes[i] = 8'h00;
        $readmemh(in_file, in_bytes, 0, in_len - 1);

        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // Input into the data buffer (window at 0x4000, DATA_IN_ADDR = 0).
        for (int i = 0; i < in_len; i += 4)
            axi_write(16'h4000 + 16'(i),
                      {in_bytes[i + 3], in_bytes[i + 2], in_bytes[i + 1], in_bytes[i]});
        axi_write(16'h0010, 32'(op));   // OP_MODE; SEC_LEVEL resets to 768.

        // Start and record only the operation itself.
        @(negedge clk);
        $dumpfile(vcd_file);
        $dumpvars(0, dut);
        axi_write(16'h0000, 32'h1);
        wait (o_done || o_error);
        @(posedge clk);
        $dumpoff;
        $dumpflush;

        axi_read(16'h0004, status);
        axi_read(16'h0014, cycles);
        $display("TVLA_RESULT status=%0d cycles=%0d", status, cycles);
        $finish;
    end

endmodule
