// tb_bridge.sv - Host protocol end to end against the real core
//
// Byte-level host (this TB) -> uart_axi_bridge (100 MHz) -> axil_cdc ->
// pqc_mlkem_top (206 MHz, unrelated phase), wired as in arty_a7_top.sv
// including core_busy = o_busy. The serial PHY (uart_rx / uart_tx) is
// covered by testbench/iverilog/tb_uart_link.sv; here bytes go straight
// into the bridge so a whole Decaps fits in a short Verilator run.
//
// Checks the acquisition fast path of docs/UART_PROTOCOL.md: 0x05 probe,
// one-time configuration, dk || c upload with 0x03, then 0x05 Run that
// replies only after the operation with STATUS / CYCLE_COUNT / ERROR_CODE
// and the shared secret K (first ACVP Decaps vector), twice (c only the
// second time); no byte leaves the bridge while the core is busy; a
// parameter error comes back in the status block. Prints "TB_BRIDGE PASS".
//
// Scope trigger: the boards drive trig_out = o_busy. Checked here: exactly
// one rising edge per Decaps, none for register / buffer traffic, the 0x05
// probe or a START with bad parameters, and the pulse is exactly
// CYCLE_COUNT core cycles long (so cycle n of the operation is n core
// clocks after the trigger edge -- the time base of config.BMUL_WINDOWS).
//
// Plusarg +dir=<vector directory> (gen_vectors.py).

`timescale 1ps/1ps

module tb_bridge;

    logic s_clk = 1'b0, m_clk = 1'b0;
    logic s_rst_n = 1'b0, m_rst_n = 1'b0;
    always #5000 s_clk = ~s_clk;            // 100 MHz
    initial begin
        #1234;
        forever #(500000.0/206.0) m_clk = ~m_clk; // 206 MHz, rounded to 1 ps
    end

    // ---------------------------------------------------------------------
    // DUT: bridge -> CDC -> core.
    // ---------------------------------------------------------------------
    logic [7:0]  rx_byte = '0;
    logic        rx_valid = 1'b0;
    logic [7:0]  tx_byte;
    logic        tx_valid;
    logic        tx_ready = 1'b1;

    logic [15:0] awaddr, araddr;
    logic        awvalid, awready, wvalid, wready, bvalid, bready;
    logic        arvalid, arready, rvalid, rready;
    logic [31:0] wdata, rdata;
    logic [3:0]  wstrb;
    logic [1:0]  bresp, rresp;

    logic [15:0] c_awaddr, c_araddr;
    logic        c_awvalid, c_awready, c_wvalid, c_wready, c_bvalid, c_bready;
    logic        c_arvalid, c_arready, c_rvalid, c_rready;
    logic [31:0] c_wdata, c_rdata;
    logic [3:0]  c_wstrb;
    logic [1:0]  c_bresp, c_rresp;
    logic        irq, o_busy, o_done, o_error;

    uart_axi_bridge #(.TIMEOUT_CYCLES(10_000_000)) u_bridge (
        .clk (s_clk), .rst_n (s_rst_n), .core_busy (o_busy),
        .rx_byte (rx_byte), .rx_valid (rx_valid),
        .tx_byte (tx_byte), .tx_valid (tx_valid), .tx_ready (tx_ready),
        .m_axi_awaddr (awaddr), .m_axi_awvalid (awvalid), .m_axi_awready (awready),
        .m_axi_wdata (wdata), .m_axi_wstrb (wstrb), .m_axi_wvalid (wvalid), .m_axi_wready (wready),
        .m_axi_bresp (bresp), .m_axi_bvalid (bvalid), .m_axi_bready (bready),
        .m_axi_araddr (araddr), .m_axi_arvalid (arvalid), .m_axi_arready (arready),
        .m_axi_rdata (rdata), .m_axi_rresp (rresp), .m_axi_rvalid (rvalid), .m_axi_rready (rready)
    );

    axil_cdc #(.ADDR_WIDTH(16)) u_cdc (
        .s_clk (s_clk), .s_rst_n (s_rst_n),
        .s_axi_awaddr (awaddr), .s_axi_awvalid (awvalid), .s_axi_awready (awready),
        .s_axi_wdata (wdata), .s_axi_wstrb (wstrb), .s_axi_wvalid (wvalid), .s_axi_wready (wready),
        .s_axi_bresp (bresp), .s_axi_bvalid (bvalid), .s_axi_bready (bready),
        .s_axi_araddr (araddr), .s_axi_arvalid (arvalid), .s_axi_arready (arready),
        .s_axi_rdata (rdata), .s_axi_rresp (rresp), .s_axi_rvalid (rvalid), .s_axi_rready (rready),
        .m_clk (m_clk), .m_rst_n (m_rst_n),
        .m_axi_awaddr (c_awaddr), .m_axi_awvalid (c_awvalid), .m_axi_awready (c_awready),
        .m_axi_wdata (c_wdata), .m_axi_wstrb (c_wstrb), .m_axi_wvalid (c_wvalid), .m_axi_wready (c_wready),
        .m_axi_bresp (c_bresp), .m_axi_bvalid (c_bvalid), .m_axi_bready (c_bready),
        .m_axi_araddr (c_araddr), .m_axi_arvalid (c_arvalid), .m_axi_arready (c_arready),
        .m_axi_rdata (c_rdata), .m_axi_rresp (c_rresp), .m_axi_rvalid (c_rvalid), .m_axi_rready (c_rready)
    );

    pqc_mlkem_top dut (
        .clk (m_clk), .rst_n (m_rst_n),
        .s_axi_awaddr (c_awaddr), .s_axi_awvalid (c_awvalid), .s_axi_awready (c_awready),
        .s_axi_wdata (c_wdata), .s_axi_wstrb (c_wstrb), .s_axi_wvalid (c_wvalid), .s_axi_wready (c_wready),
        .s_axi_bresp (c_bresp), .s_axi_bvalid (c_bvalid), .s_axi_bready (c_bready),
        .s_axi_araddr (c_araddr), .s_axi_arvalid (c_arvalid), .s_axi_arready (c_arready),
        .s_axi_rdata (c_rdata), .s_axi_rresp (c_rresp), .s_axi_rvalid (c_rvalid), .s_axi_rready (c_rready),
        .irq (irq), .o_busy (o_busy), .o_done (o_done), .o_error (o_error)
    );

    // ---------------------------------------------------------------------
    // Scope trigger (trig_out = o_busy): rising edges and pulse width in core cycles.
    // ---------------------------------------------------------------------
    int   trig_edges = 0, trig_count = 0, trig_width = 0;
    logic busy_q = 1'b0;
    always @(posedge m_clk) begin
        busy_q <= o_busy;
        if (o_busy && !busy_q) begin
            trig_edges <= trig_edges + 1;
            trig_count <= 1;
        end else if (o_busy)
            trig_count <= trig_count + 1;
        if (!o_busy && busy_q)
            trig_width <= trig_count;
    end

    // ---------------------------------------------------------------------
    // uart_tx stand-in: takes a byte, is busy for a few cycles.
    // ---------------------------------------------------------------------
    logic [7:0] rxq [0:8191];
    int         rxq_n = 0;
    int         tx_while_busy = 0;
    int         tx_hold = 0;
    always @(posedge s_clk) begin
        if (tx_valid) begin
            rxq[rxq_n] <= tx_byte;
            rxq_n      <= rxq_n + 1;
            tx_ready   <= 1'b0;
            tx_hold    <= 3;
            if (o_busy) tx_while_busy <= tx_while_busy + 1;
        end else if (tx_hold > 0) begin
            tx_hold <= tx_hold - 1;
            if (tx_hold == 1) tx_ready <= 1'b1;
        end
    end

    // ---------------------------------------------------------------------
    // Host side.
    // ---------------------------------------------------------------------
    int errors = 0;

    task automatic check(input string what, input logic [31:0] got, input logic [31:0] exp);
        if (got !== exp) begin
            errors++;
            $display("  [FAIL] %s: got 0x%08x, expected 0x%08x", what, got, exp);
        end else
            $display("  [ OK ] %s = 0x%08x", what, got);
    endtask

    function automatic logic [31:0] crc_step(input logic [31:0] c, input logic [7:0] b);
        logic [31:0] x;
        x = c ^ {24'h0, b};
        for (int i = 0; i < 8; i++)
            x = x[0] ? ((x >> 1) ^ 32'hEDB8_8320) : (x >> 1);
        return x;
    endfunction

    task automatic put(input logic [7:0] b);
        @(negedge s_clk);
        rx_byte = b; rx_valid = 1'b1;
        @(negedge s_clk);
        rx_valid = 1'b0;
        repeat (2) @(negedge s_clk);
    endtask

    logic [7:0] pl [0:255];   // Payload of the next frame.

    task automatic send(input logic [7:0] cmd, input logic [31:0] addr, input int len);
        logic [31:0] crc;
        crc = crc_step(32'hFFFF_FFFF, cmd);
        put(cmd);
        for (int i = 0; i < 4; i++) begin crc = crc_step(crc, addr[8*i +: 8]); put(addr[8*i +: 8]); end
        for (int i = 0; i < 4; i++) begin crc = crc_step(crc, 8'(len >> (8*i))); put(8'(len >> (8*i))); end
        for (int i = 0; i < len; i++) begin crc = crc_step(crc, pl[i]); put(pl[i]); end
        crc = ~crc;
        for (int i = 0; i < 4; i++) put(crc[8*i +: 8]);
    endtask

    // Receive one response frame; returns its LEN, data in resp[].
    logic [7:0] resp [0:4095];
    int         rd_pos = 0;

    task automatic recv(input string what, output logic [7:0] status, output int n, input int max_cycles);
        logic [31:0] crc, got;
        int t;
        t = 0;
        while (rxq_n < rd_pos + 5 && t < max_cycles) begin @(posedge s_clk); t++; end
        if (rxq_n < rd_pos + 5) begin
            errors++; $display("  [FAIL] %s: no response", what); status = 8'hFF; n = 0; return;
        end
        status = rxq[rd_pos];
        n = int'({rxq[rd_pos+4], rxq[rd_pos+3], rxq[rd_pos+2], rxq[rd_pos+1]});
        while (rxq_n < rd_pos + 5 + n + 4) @(posedge s_clk);
        crc = 32'hFFFF_FFFF;
        for (int i = 0; i < 5 + n; i++) crc = crc_step(crc, rxq[rd_pos + i]);
        for (int i = 0; i < n; i++) resp[i] = rxq[rd_pos + 5 + i];
        got = {rxq[rd_pos+5+n+3], rxq[rd_pos+5+n+2], rxq[rd_pos+5+n+1], rxq[rd_pos+5+n]};
        check({what, " CRC"}, got, ~crc);
        rd_pos = rd_pos + 5 + n + 4;
    endtask

    function automatic logic [31:0] word(input int at);
        return {resp[at+3], resp[at+2], resp[at+1], resp[at]};
    endfunction

    task automatic wr_reg(input logic [15:0] a, input logic [31:0] v);
        logic [7:0] st; int n;
        for (int i = 0; i < 4; i++) pl[i] = v[8*i +: 8];
        send(8'h02, {16'h0, a}, 4);
        recv($sformatf("write 0x%02x", a), st, n, 100000);
        check($sformatf("write 0x%02x status", a), {24'h0, st}, 32'h0);
    endtask

    task automatic run(input logic [31:0] value, input logic [31:0] off, input logic [31:0] cnt,
                       output logic [7:0] st, output int n);
        for (int i = 0; i < 4; i++) begin
            pl[i] = value[8*i +: 8]; pl[4+i] = off[8*i +: 8]; pl[8+i] = cnt[8*i +: 8];
        end
        send(8'h05, 32'h0, 12);
        recv("run", st, n, 2_000_000);
    endtask

    // ---------------------------------------------------------------------
    // Test.
    // ---------------------------------------------------------------------
    localparam logic [31:0] OUT_BASE = 32'h0E00;    // cpa_toolkit/config.py
    logic [7:0] kg_in [0:63], kg_out [0:3583], dc_in [0:3487], dc_out [0:31];
    string      dir;

    task automatic upload(input int from, input int count);
        logic [7:0] st; int n;
        for (int at = from; at < from + count; at += 256) begin
            int len = (from + count - at > 256) ? 256 : from + count - at;
            for (int i = 0; i < len; i++) pl[i] = dc_in[at + i];
            send(8'h03, 32'(at), len);
            recv("write data", st, n, 1_000_000);
            if (st != 8'h00) begin errors++; $display("  [FAIL] write data @%0d status %02x", at, st); end
        end
    endtask

    task automatic decaps(input string name, input logic [31:0] exp_cycles, output logic [31:0] cycles);
        logic [7:0] st; int n, k_bad, edges0;
        tx_while_busy = 0;
        edges0 = trig_edges;
        run(32'h1, OUT_BASE, 32'd32, st, n);
        check({name, ": response status"}, {24'h0, st}, 32'h0);
        check({name, ": LEN = 12 + 32"}, n, 44);
        check({name, ": STATUS = done"}, word(0), 32'h2);
        check({name, ": ERROR_CODE"}, word(8), 32'h0);
        cycles = word(4);
        if (exp_cycles != 0) check({name, ": CYCLE_COUNT (constant time)"}, cycles, exp_cycles);
        else $display("  [info] %s: CYCLE_COUNT = %0d", name, cycles);
        k_bad = 0;
        for (int i = 0; i < 32; i++) if (resp[12 + i] !== dc_out[i]) k_bad++;
        check({name, ": K bytes different from ACVP"}, k_bad, 0);
        check({name, ": bytes sent while the core was busy"}, tx_while_busy, 0);
        check({name, ": trigger edges (trig_out = busy)"}, trig_edges - edges0, 1);
        check({name, ": trigger pulse width = CYCLE_COUNT core cycles"}, trig_width, cycles);
    endtask

    initial begin
        logic [7:0] st; int n; logic [31:0] cyc;
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        $readmemh({dir, "/dc_in.hex"}, dc_in);
        $readmemh({dir, "/dc_out.hex"}, dc_out);
        #50000;
        s_rst_n = 1'b1;
        m_rst_n = 1'b1;
        #50000;

        $display("--- 0x05 probe: write to read-only ALG_ID, nothing starts ---");
        for (int i = 0; i < 12; i++) pl[i] = 8'h00;
        send(8'h05, 32'h0000_0008, 12);
        recv("probe", st, n, 100000);
        check("probe status", {24'h0, st}, 32'h0);
        check("probe LEN", n, 12);
        check("probe STATUS (idle)", word(0), 32'h0);

        $display("--- one-time configuration ---");
        wr_reg(16'h000C, 32'd768);                 // SEC_LEVEL
        wr_reg(16'h0010, 32'd2);                   // OP_MODE = Decaps
        wr_reg(16'h0020, 32'h0);                   // DATA_IN_ADDR
        wr_reg(16'h0024, 32'd3488);                // DATA_IN_LEN
        wr_reg(16'h0028, OUT_BASE);                // DATA_OUT_ADDR

        check("no trigger from probe, register writes", trig_edges, 0);

        $display("--- Decaps 1: upload dk || c, 0x05 Run ---");
        upload(0, 3488);
        decaps("Decaps 1", 0, cyc);

        $display("--- Decaps 2: c only (dk stays in the buffer) ---");
        upload(2400, 1088);
        check("no trigger from buffer uploads", trig_edges, 1);
        decaps("Decaps 2", cyc, cyc);

        $display("--- parameter error: OP_MODE = 3 ---");
        wr_reg(16'h0010, 32'd3);
        run(32'h1, OUT_BASE, 32'd32, st, n);
        check("error: response status", {24'h0, st}, 32'h0);
        check("error: LEN = 12 (no output)", n, 12);
        check("error: STATUS = error", word(0), 32'h4);
        check("error: ERROR_CODE = 1", word(8), 32'h1);
        check("error: no trigger (START refused, never busy)", trig_edges, 2);

        if (errors == 0) $display("TB_BRIDGE PASS");
        else $display("TB_BRIDGE FAIL (%0d errors)", errors);
        $finish;
    end

    initial begin
        #(64'd20_000_000_000);  // 20 ms simulated.
        $display("TB_BRIDGE FAIL: watchdog");
        $finish;
    end

endmodule
