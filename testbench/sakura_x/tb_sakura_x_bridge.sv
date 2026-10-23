// tb_sakura_x_bridge.sv - sakura_x_bridge against the template's REAL Spartan-6 decoder.
//
// Spartan side: third_party/sakura_x_shell/sakura-x-shell-ctrl/command_control.v and fifo.v,
// unmodified, at 50 MHz. The host pushes USB bytes into the "from FTDI" FIFO and pops
// replies from the "to FTDI" FIFO (the FT2232H itself is not modelled).
// Kintex side: hdl/xilinx/sakura_x/sakura_x_bridge.sv behind the same input and
// output register stage as sakura_x_top.sv, clocked by the INVERTED Spartan clock (the
// controller forwards it through ODDR2 D0=0 / D1=1), with WIRE_NS of board delay each way.
// Behind the bridge: an AXI-Lite memory and a core that is busy for CORE_CYCLES after
// CTRL.start, then reports STATUS = DONE.
//
// Checks: unknown-command probe, IDENT, 16-word write/read-back, a full DK+CT upload,
// START (reply only after busy falls + the quiet tail), RESULT_READY, a 16-word read
// while the host does not drain (FIFO backpressure), and no handshake deadlock.
`timescale 1ns/1ps
`include "def.v"

module tb_sakura_x_bridge;
    parameter real WIRE_NS = 3.0;
    localparam int BUS_HZ = 50_000;                 // shortened delays: PRE 100, tail 1000 cycles
    localparam int CORE_CYCLES = 300;

    // ---------------- clocks / resets ----------------
    logic s_clk = 0, s_rst_n = 0;
    always #10 s_clk = ~s_clk;                       // Spartan PLL output, 50 MHz
    // Board traces are TRANSPORT delays (nonblocking intra-assignment delay): an inertial
    // `assign #d` would swallow every pulse shorter than d -- the 10 ns clock phases included.
    logic k_clk = 1;
    always @(s_clk) k_clk <= #(WIRE_NS) ~s_clk;      // forwarded inverted (ODDR2); MMCM deskews to the pin

    // ---------------- Spartan-6: FIFOs + command_control (template RTL) ----------------
    logic [7:0] h_wd; logic h_we = 0;
    wire  [7:0] in_rd; wire in_empty, in_aempty, in_full, cc_re;
    wire  [7:0] cc_wd, out_rd; wire cc_we, out_full, out_empty;
    logic h_re = 0;

    fifo from_ftdi (.clk(s_clk), .reset_n(s_rst_n), .i_data(h_wd), .i_write_enable(h_we), .o_full(in_full),
                    .o_data(in_rd), .i_read_enable(cc_re), .o_empty(in_empty), .o_almost_empty(in_aempty));
    fifo to_ftdi   (.clk(s_clk), .reset_n(s_rst_n), .i_data(cc_wd), .i_write_enable(cc_we), .o_full(out_full),
                    .o_data(out_rd), .i_read_enable(h_re), .o_empty(out_empty), .o_almost_empty());

    wire [31:0] s_common; wire s_addr_valid, s_write_enable, s_write_valid, s_read_ready, s_sw_reset_n;
    wire [31:0] s_read_data; wire s_addr_ready, s_write_ready, s_read_valid;
    command_control cc (
        .clk(s_clk), .reset_n(s_rst_n),
        .i_fifo_data(in_rd), .i_fifo_empty(in_empty), .i_fifo_almost_empty(in_aempty), .o_fifo_read_enable(cc_re),
        .o_fifo_data(cc_wd), .i_fifo_full(out_full), .o_fifo_write_enable(cc_we),
        .o_addr_valid(s_addr_valid), .o_write_enable(s_write_enable), .o_write_data_valid(s_write_valid),
        .i_addr_ready(s_addr_ready), .i_write_data_ready(s_write_ready), .i_read_data_valid(s_read_valid),
        .o_read_data_ready(s_read_ready), .o_common(s_common), .i_read_data(s_read_data),
        .o_sw_reset_n(s_sw_reset_n), .o_busy());

    // ---------------- the board between the two FPGAs ----------------
    logic [31:0] k_common = 0; logic k_addr_valid = 0, k_write_enable = 0, k_write_valid = 0, k_read_ready = 0;
    logic k_bus_reset_n = 0;
    wire [31:0] k_read_data_pin; wire k_addr_ready_pin, k_write_ready_pin, k_read_valid_pin;
    logic [31:0] s_read_data_d = 0; logic s_addr_ready_d = 0, s_write_ready_d = 0, s_read_valid_d = 0;
    always @(s_common)       k_common       <= #(WIRE_NS) s_common;
    always @(s_addr_valid)   k_addr_valid   <= #(WIRE_NS) s_addr_valid;
    always @(s_write_enable) k_write_enable <= #(WIRE_NS) s_write_enable;
    always @(s_write_valid)  k_write_valid  <= #(WIRE_NS) s_write_valid;
    always @(s_read_ready)   k_read_ready   <= #(WIRE_NS) s_read_ready;
    always @(s_rst_n, s_sw_reset_n) k_bus_reset_n <= #(WIRE_NS) (s_rst_n & s_sw_reset_n);
    always @(k_read_data_pin)   s_read_data_d   <= #(WIRE_NS) k_read_data_pin;
    always @(k_addr_ready_pin)  s_addr_ready_d  <= #(WIRE_NS) k_addr_ready_pin;
    always @(k_write_ready_pin) s_write_ready_d <= #(WIRE_NS) k_write_ready_pin;
    always @(k_read_valid_pin)  s_read_valid_d  <= #(WIRE_NS) k_read_valid_pin;
    assign s_read_data = s_read_data_d, s_addr_ready = s_addr_ready_d;
    assign s_write_ready = s_write_ready_d, s_read_valid = s_read_valid_d;

    // ---------------- Kintex-7: reset sync + I/O registers (as sakura_x_top.sv) + bridge ----------------
    logic [3:0] reset_bus;
    always_ff @(posedge k_clk or negedge k_bus_reset_n)
        if (!k_bus_reset_n) reset_bus <= 0; else reset_bus <= {reset_bus[2:0], 1'b1};
    wire rst_n = reset_bus[3];

    logic [31:0] common_q, read_data_q;
    logic addr_valid_q, write_enable_q, write_valid_q, read_ready_q, addr_ready_q, write_ready_q, read_valid_q;
    wire [31:0] bridge_read_data; wire bridge_addr_ready, bridge_write_ready, bridge_read_valid;
    always_ff @(posedge k_clk or negedge rst_n) begin
        if (!rst_n) begin
            common_q <= 0; addr_valid_q <= 0; write_enable_q <= 0; write_valid_q <= 0; read_ready_q <= 0;
            read_data_q <= 0; addr_ready_q <= 0; write_ready_q <= 0; read_valid_q <= 0;
        end else begin
            common_q <= k_common; addr_valid_q <= k_addr_valid; write_enable_q <= k_write_enable;
            write_valid_q <= k_write_valid; read_ready_q <= k_read_ready;
            read_data_q <= bridge_read_data; addr_ready_q <= bridge_addr_ready;
            write_ready_q <= bridge_write_ready; read_valid_q <= bridge_read_valid;
        end
    end
    assign k_read_data_pin = read_data_q;
    assign k_addr_ready_pin = addr_ready_q;
    assign k_write_ready_pin = write_ready_q;
    assign k_read_valid_pin = read_valid_q;

    logic [15:0] awaddr, araddr; logic awvalid, awready, wvalid, wready, bvalid, bready;
    logic arvalid, arready, rvalid, rready; logic [31:0] wdata, rdata; logic [3:0] wstrb;
    logic [1:0] bresp = 0, rresp = 0;
    logic core_busy = 0, run_active;

    sakura_x_bridge #(.BUS_HZ(BUS_HZ)) u_bridge (
        .clk(k_clk), .rst_n(rst_n),
        .i_addr_valid(addr_valid_q), .i_write_enable(write_enable_q), .i_write_data_valid(write_valid_q),
        .i_read_data_ready(read_ready_q), .i_common(common_q), .o_read_data(bridge_read_data),
        .o_addr_ready(bridge_addr_ready), .o_write_data_ready(bridge_write_ready), .o_read_data_valid(bridge_read_valid),
        .core_busy(core_busy), .core_error(1'b0), .run_active(run_active),
        .m_axi_awaddr(awaddr), .m_axi_awvalid(awvalid), .m_axi_awready(awready),
        .m_axi_wdata(wdata), .m_axi_wstrb(wstrb), .m_axi_wvalid(wvalid), .m_axi_wready(wready),
        .m_axi_bresp(bresp), .m_axi_bvalid(bvalid), .m_axi_bready(bready),
        .m_axi_araddr(araddr), .m_axi_arvalid(arvalid), .m_axi_arready(arready),
        .m_axi_rdata(rdata), .m_axi_rresp(rresp), .m_axi_rvalid(rvalid), .m_axi_rready(rready));

    // ---------------- AXI-Lite memory + a core that is busy after CTRL.start ----------------
    logic [31:0] mem [0:16383];
    logic aw_got, w_got; logic [15:0] aw_a; logic [31:0] w_d;
    int busy_left = 0, n_start = 0;
    time busy_fell_at = 0;
    assign awready = !aw_got; assign wready = !w_got; assign arready = !rvalid;
    always_ff @(posedge k_clk) begin
        if (!rst_n) begin
            aw_got <= 0; w_got <= 0; bvalid <= 0; rvalid <= 0;
        end else begin
            if (awvalid && awready) begin aw_got <= 1; aw_a <= awaddr; end
            if (wvalid && wready) begin w_got <= 1; w_d <= wdata; end
            if (aw_got && w_got && !bvalid) begin
                mem[aw_a >> 2] <= w_d;
                if (aw_a == 0 && w_d[0]) begin busy_left <= CORE_CYCLES; n_start <= n_start + 1; mem[1] <= 0; end
                bvalid <= 1; aw_got <= 0; w_got <= 0;
            end
            if (bvalid && bready) bvalid <= 0;
            if (arvalid && arready) begin rvalid <= 1; rdata <= mem[araddr >> 2]; end
            if (rvalid && rready) rvalid <= 0;
            if (busy_left > 0) begin
                core_busy <= 1;
                busy_left <= busy_left - 1;
                if (busy_left == 1) begin core_busy <= 0; mem[1] <= 32'd2; busy_fell_at <= $time; end
            end
        end
    end

    // ---------------- host model ----------------
    int errors = 0, watchdog = 0;
    task automatic push(input logic [7:0] b);
        @(negedge s_clk); while (in_full) @(negedge s_clk);
        h_wd = b; h_we = 1; @(negedge s_clk); h_we = 0;
    endtask
    task automatic pop(output logic [7:0] b);
        int t = 0;
        @(negedge s_clk);
        while (out_empty) begin
            @(negedge s_clk);
            if (++t > 400_000) begin $display("FAIL: no reply byte (handshake deadlock)"); errors++; b = 8'hxx; return; end
        end
        b = out_rd; h_re = 1; @(negedge s_clk); h_re = 0;
    endtask
    task automatic header(input logic [3:0] typ, input logic [3:0] words_m1, input logic [31:0] addr);
        logic [47:0] h; h = {4'h8, typ, words_m1, addr, 4'h1};
        for (int i = 5; i >= 0; i--) push(h[8*i +: 8]);
    endtask
    task automatic expect_ack(input logic [7:0] want, input string what);
        logic [7:0] a; pop(a);
        if (a !== want) begin $display("FAIL %s: ACK %02x, expected %02x", what, a, want); errors++; end
    endtask
    logic [31:0] w [16], r [16];                     // write payload / read result of rd() and wr()
    task automatic rd(input logic [31:0] addr, input int n);
        logic [7:0] b;
        logic [31:0] word;
        header(4'h1, 4'(n - 1), addr);
        expect_ack(8'h00, "read header");
        for (int i = 0; i < n; i++) begin
            word = 0;
            for (int k = 0; k < 4; k++) begin pop(b); word = {word[23:0], b}; end
            r[i] = word;
        end
    endtask
    task automatic wr(input logic [31:0] addr, input int n);
        logic [31:0] word;
        header(4'h2, 4'(n - 1), addr);
        expect_ack(8'h00, "write header");
        for (int i = 0; i < n; i++) begin
            word = w[i];
            for (int k = 3; k >= 0; k--) push(word[8*k +: 8]);
        end
    endtask
    task automatic check(input logic [31:0] got, input logic [31:0] want, input string what);
        if (got !== want) begin $display("FAIL %s: %08x, expected %08x", what, got, want); errors++; end
    endtask

    time t_req;
    int n_up;
    initial begin
        repeat (4) @(posedge s_clk); s_rst_n = 1;
        repeat (10) @(posedge s_clk);

        header(4'hf, 4'h0, 32'h0);                       // unknown command: Spartan-only, 0x02
        expect_ack(8'h02, "unknown-command probe");

        rd(32'h104, 1); check(r[0], 32'h534b0001, "IDENT");
        rd(32'h10c, 1); check(r[0], BUS_HZ, "CLOCK_HZ");
        rd(32'h110, 1); check(r[0], 0, "FAULT after reset");

        for (int i = 0; i < 16; i++) w[i] = 32'h1000_0000 + i * 32'h0101_0101;
        wr(32'h4000, 16);
        rd(32'h4000, 16);
        for (int i = 0; i < 16; i++) check(r[i], w[i], $sformatf("read-back word %0d", i));

        // RESET (CTRL bit 1), then a complete DK (600 words) + CT (272 words) upload
        w[0] = 32'h2; wr(32'h0, 1);
        for (int base = 0; base < 600 + 272; base += 16) begin
            n_up = (600 + 272 - base) < 16 ? (600 + 272 - base) : 16;
            for (int i = 0; i < n_up; i++) w[i] = base + i;
            wr(32'h4000 + 4 * base, n_up);
        end
        rd(32'h114, 1); check(r[0], 3, "INPUT_READY after DK+CT");
        rd(32'h4000 + 4 * 871, 1); check(r[0], 871, "last CT word");

        // START: one read whose reply comes only after busy falls + the quiet tail
        w[0] = BUS_HZ / 50; wr(32'h108, 1);           // tail = 1000 bus cycles = 20 us
        rd(32'h108, 1); check(r[0], BUS_HZ / 50, "TAIL readback");
        t_req = $time;
        rd(32'h100, 1); check(r[0], 2, "START reply = STATUS DONE");
        if (n_start != 1) begin $display("FAIL: core started %0d times", n_start); errors++; end
        if ($time < busy_fell_at + (BUS_HZ / 50) * 20) begin
            $display("FAIL: START replied %0t after busy fell -- before the quiet tail", $time - busy_fell_at); errors++;
        end
        rd(32'h118, 1); check(r[0], 1, "RESULT_READY");
        rd(32'h114, 1); check(r[0], 1, "INPUT_READY after START (CT consumed)");

        // a 16-word read while the host does not drain: the Spartan's 32-byte output FIFO fills
        header(4'h1, 4'hf, 32'h4000);
        repeat (400) @(posedge s_clk);
        expect_ack(8'h00, "backpressured read header");
        for (int i = 0; i < 16; i++) begin
            logic [7:0] b;
            logic [31:0] word;
            word = 0;
            for (int k = 0; k < 4; k++) begin pop(b); word = {word[23:0], b}; end
            check(word, i, $sformatf("backpressured word %0d", i));
        end
        rd(32'h110, 1); check(r[0], 0, "FAULT at the end");

        if (errors == 0) $display("TB_SHELL_BRIDGE PASS (wire delay %0.1f ns)", WIRE_NS);
        else             $display("TB_SHELL_BRIDGE FAIL: %0d errors (wire delay %0.1f ns)", errors, WIRE_NS);
        $finish;
    end
    initial begin #200_000_000; $display("TB_SHELL_BRIDGE FAIL: global timeout"); $finish; end
endmodule
