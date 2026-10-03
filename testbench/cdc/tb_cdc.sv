// tb_cdc.sv - System test of axil_cdc with pqc_mlkem_top on its own clock
//
// AXI-Lite master on s_clk (100 MHz) -> axil_cdc -> pqc_mlkem_top on m_clk
// (206 MHz, unrelated phase). Checks register write/read-back, a
// byte-strobe write into the data buffer, and full ML-KEM-768 KeyGen and
// Decaps with the first ACVP vectors (gen_vectors.py), reading results
// back through the crossing. Prints "TB_CDC PASS" on success.
//
// Plusarg +dir=<vector directory>.

`timescale 1ps/1ps

module tb_cdc;

    logic s_clk = 1'b0, m_clk = 1'b0;
    logic s_rst_n = 1'b0, m_rst_n = 1'b0;
    always #5000 s_clk = ~s_clk;            // 100 MHz
    initial begin
        #1234;
        forever #(500000.0/206.0) m_clk = ~m_clk; // 206 MHz, rounded to 1 ps
    end

    // Master side (s_clk).
    logic [15:0] awaddr = '0, araddr = '0;
    logic        awvalid = 1'b0, wvalid = 1'b0, arvalid = 1'b0;
    logic [31:0] wdata = '0;
    logic [3:0]  wstrb = 4'hF;
    logic        awready, wready, bvalid, arready, rvalid;
    logic [1:0]  bresp, rresp;
    logic [31:0] rdata;

    // Core side (m_clk).
    logic [15:0] c_awaddr, c_araddr;
    logic        c_awvalid, c_awready, c_wvalid, c_wready, c_bvalid, c_bready;
    logic        c_arvalid, c_arready, c_rvalid, c_rready;
    logic [31:0] c_wdata, c_rdata;
    logic [3:0]  c_wstrb;
    logic [1:0]  c_bresp, c_rresp;
    logic        irq, o_busy, o_done, o_error;

    axil_cdc #(.ADDR_WIDTH(16)) u_cdc (
        .s_clk (s_clk), .s_rst_n (s_rst_n),
        .s_axi_awaddr (awaddr), .s_axi_awvalid (awvalid), .s_axi_awready (awready),
        .s_axi_wdata (wdata), .s_axi_wstrb (wstrb), .s_axi_wvalid (wvalid), .s_axi_wready (wready),
        .s_axi_bresp (bresp), .s_axi_bvalid (bvalid), .s_axi_bready (1'b1),
        .s_axi_araddr (araddr), .s_axi_arvalid (arvalid), .s_axi_arready (arready),
        .s_axi_rdata (rdata), .s_axi_rresp (rresp), .s_axi_rvalid (rvalid), .s_axi_rready (1'b1),
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

    // AW and W presented with different delays to exercise both orders.
    task automatic axi_write(input logic [15:0] addr, input logic [31:0] data,
                             input logic [3:0] strb, input bit w_first);
        @(negedge s_clk);
        if (w_first) begin
            // W alone first; AW only after W has been accepted.
            wdata = data; wstrb = strb; wvalid = 1'b1;
            while (!wready) @(negedge s_clk);
            @(negedge s_clk);
            wvalid = 1'b0;
            awaddr = addr; awvalid = 1'b1;
        end else begin
            awaddr = addr; awvalid = 1'b1; wdata = data; wstrb = strb; wvalid = 1'b1;
        end
        // Ready is sampled mid-cycle (negedge), i.e. before the edge that
        // completes the handshake.
        for (int t = 0; awvalid || wvalid; t++) begin
            bit aw_acc, w_acc;
            if (t == 1000) begin
                $display("TIMEOUT write %04x handshake (awready=%0b wready=%0b)", addr, awready, wready);
                $fatal(1);
            end
            aw_acc = awvalid && awready;
            w_acc  = wvalid && wready;
            @(negedge s_clk);
            if (aw_acc) awvalid = 1'b0;
            if (w_acc)  wvalid  = 1'b0;
        end
        for (int t = 0; !bvalid; t++) begin
            if (t == 1000) begin
                $display("TIMEOUT write %04x (awvalid=%0b wvalid=%0b)", addr, awvalid, wvalid);
                $fatal(1);
            end
            @(negedge s_clk);
        end
        @(negedge s_clk);
    endtask

    task automatic axi_read(input logic [15:0] addr, output logic [31:0] data);
        @(negedge s_clk);
        araddr = addr; arvalid = 1'b1;
        while (!arready) @(negedge s_clk);
        @(negedge s_clk);
        arvalid = 1'b0;
        for (int t = 0; !rvalid; t++) begin
            if (t == 1000) begin
                $display("TIMEOUT read %04x (arready=%0b arvalid=%0b)", addr, arready, arvalid);
                $fatal(1);
            end
            @(negedge s_clk);
        end
        data = rdata;
        @(negedge s_clk);
    endtask

    logic [7:0]  kg_in [0:63], kg_out [0:3583], dc_in [0:3487], dc_out [0:31];
    string       dir;
    int          errors = 0;
    logic [31:0] v, in_addr, out_addr;

    task automatic check(input string what, input logic [31:0] got, input logic [31:0] exp);
        if (got !== exp) begin
            errors++;
            $display("FAIL %s: got %08x expected %08x", what, got, exp);
        end
    endtask

    task automatic run_op(input int op, input int in_len, input int out_len, input string name);
        int t;
        for (int i = 0; i < in_len; i += 4) begin
            if (op == 0)
                v = {kg_in[i + 3], kg_in[i + 2], kg_in[i + 1], kg_in[i]};
            else
                v = {dc_in[i + 3], dc_in[i + 2], dc_in[i + 1], dc_in[i]};
            axi_write(16'h4000 + 16'(in_addr) + 16'(i), v, 4'hF, (i % 8) == 4);
        end
        axi_write(16'h0010, 32'(op), 4'hF, 1'b0);
        axi_write(16'h0000, 32'h1, 4'hF, 1'b0);
        t = 0;
        do begin
            axi_read(16'h0004, v);
            t++;
        end while (!v[1] && !v[2] && t < 10000);
        check({name, " STATUS"}, v, 32'h2);
        axi_read(16'h002C, v);
        check({name, " DATA_OUT_LEN"}, v, 32'(out_len));
        for (int i = 0; i < out_len; i += 4) begin
            logic [31:0] e;
            axi_read(16'h4000 + 16'(out_addr) + 16'(i), v);
            e = (op == 0) ? {kg_out[i + 3], kg_out[i + 2], kg_out[i + 1], kg_out[i]}
                          : {dc_out[i + 3], dc_out[i + 2], dc_out[i + 1], dc_out[i]};
            if (v !== e) begin
                errors++;
                if (errors <= 10)
                    $display("FAIL %s output word %0d: got %08x expected %08x", name, i / 4, v, e);
            end
        end
        axi_read(16'h0014, v);
        $display("%s: %0d core cycles", name, v); $fflush();
    endtask

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = ".";
        $readmemh({dir, "/kg_in.hex"}, kg_in);
        $readmemh({dir, "/kg_out.hex"}, kg_out);
        $readmemh({dir, "/dc_in.hex"}, dc_in);
        $readmemh({dir, "/dc_out.hex"}, dc_out);
        #50000;
        s_rst_n = 1'b1;
        m_rst_n = 1'b1;
        #50000;
        $display("reset released"); $fflush();

        // Registers and the data window through the crossing.
        axi_read(16'h0008, v);  check("ALG_ID", v, 32'd1);
        $display("first read ok"); $fflush();
        axi_read(16'h000C, v);  check("SEC_LEVEL reset", v, 32'd768);
        axi_write(16'h000C, 32'd999, 4'hF, 1'b1);
        axi_read(16'h000C, v);  check("SEC_LEVEL write", v, 32'd999);
        axi_write(16'h000C, 32'd768, 4'hF, 1'b0);
        axi_write(16'h5000, 32'hA1B2C3D4, 4'hF, 1'b0);
        axi_write(16'h5000, 32'h00EE0000, 4'b0100, 1'b1);
        axi_read(16'h5000, v);  check("byte strobe", v, 32'hA1EEC3D4);
        $display("register checks done, %0d errors", errors); $fflush();
        axi_read(16'h0020, in_addr);
        axi_read(16'h0028, out_addr);

        run_op(0, 64, 3584, "KeyGen");
        run_op(2, 3488, 32, "Decaps");

        if (errors == 0) $display("TB_CDC PASS");
        else $display("TB_CDC FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
