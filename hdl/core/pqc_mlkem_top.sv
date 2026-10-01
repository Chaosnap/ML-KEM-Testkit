// pqc_mlkem_top.sv - ML-KEM (FIPS 203) accelerator core
//
// Synthesizable top-level of the ML-KEM hardware accelerator used by the
// pqc-testkit host tool. Implements KeyGen_internal, Encaps_internal and
// Decaps_internal (with implicit rejection) for ML-KEM-768/1024.
//
// FIXED_LEVEL (default 768) builds the core for one parameter set: the
// level decode and output sizes become constants, and SEC_LEVEL must hold
// that value or START fails with ERROR_CODE 1. Set it to 0 for a core that
// selects the level at run time. The microcode ROM must contain the level:
//   python3 scripts/gen_mlkem_ucode.py --levels 768
//
// Data path:
//
//   AXI-Lite --> u_csr --(port A)--> u_dbuf (16 KB) <--(port B)--+
//                  |                                             |
//               start/status                                     |
//                  v                                             |
//               u_ctrl (microcode sequencer, u_rom) -------------+
//                  |        |            |             |
//              u_sponge   u_alu       u_unpack      u_pack
//             (u_keccak) (NTT/INTT,   (SampleNTT,   (Compress,
//                         basemul,     CBD, Decode,  Encode)
//                         add/sub)     Decompress)
//                             \           |             /
//                              +---- u_polyram (16 polys) ---+
//
// Host flow: write inputs to the data buffer (bus 0x4000 + DATA_IN_ADDR),
// set SEC_LEVEL / OP_MODE, write CTRL.start, poll STATUS.done, read
// DATA_OUT_LEN bytes from 0x4000 + DATA_OUT_ADDR.
//
//   OP_MODE 0 KeyGen: in = d || z          out = ek || dk
//   OP_MODE 1 Encaps: in = ek || m         out = c || K
//   OP_MODE 2 Decaps: in = dk || c         out = K

module pqc_mlkem_top #(
    parameter int AXI_ADDR_WIDTH = 16,     // Address width for AXI-Lite.
    parameter int FIXED_LEVEL    = 768     // 768/1024, or 0 = run-time SEC_LEVEL.
) (
    input  logic                        clk,
    input  logic                        rst_n,

    // AXI4-Lite slave interface.
    input  logic [AXI_ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  logic                        s_axi_awvalid,
    output logic                        s_axi_awready,
    input  logic [31:0]                 s_axi_wdata,
    input  logic [3:0]                  s_axi_wstrb,
    input  logic                        s_axi_wvalid,
    output logic                        s_axi_wready,
    output logic [1:0]                  s_axi_bresp,
    output logic                        s_axi_bvalid,
    input  logic                        s_axi_bready,
    input  logic [AXI_ADDR_WIDTH-1:0]   s_axi_araddr,
    input  logic                        s_axi_arvalid,
    output logic                        s_axi_arready,
    output logic [31:0]                 s_axi_rdata,
    output logic [1:0]                  s_axi_rresp,
    output logic                        s_axi_rvalid,
    input  logic                        s_axi_rready,

    // Interrupt output (active-high, level while done).
    output logic                        irq,

    // Status outputs for board-level indicators (active-high).
    output logic                        o_busy,
    output logic                        o_done,
    output logic                        o_error
);

    // =========================================================================
    // CSR <-> sequencer
    // =========================================================================

    logic        ctrl_start, ctrl_reset;
    logic        status_busy, status_done, status_error;
    logic [31:0] sec_level, op_mode, cycle_count, error_code;
    logic [31:0] data_in_addr, data_in_len, data_out_addr, data_out_len;

    // Data buffer ports.
    logic        dbuf_a_en, dbuf_b_en;
    logic [3:0]  dbuf_a_we, dbuf_b_we;
    logic [11:0] dbuf_a_addr, dbuf_b_addr;
    logic [31:0] dbuf_a_din, dbuf_a_dout, dbuf_b_din, dbuf_b_dout;

    pqc_axi_csr #(
        .ALG_ID      (1),            // ML-KEM.
        .VERSION_MAJ (2),
        .VERSION_MIN (0),
        .VERSION_PAT (0),
        .ADDR_WIDTH  (AXI_ADDR_WIDTH),
        .BUF_AW      (12),
        .DEFAULT_SEC_LEVEL ((FIXED_LEVEL != 0) ? 32'(FIXED_LEVEL) : 32'd768)
    ) u_csr (
        .clk            (clk),
        .rst_n          (rst_n),
        .s_axi_awaddr   (s_axi_awaddr),
        .s_axi_awvalid  (s_axi_awvalid),
        .s_axi_awready  (s_axi_awready),
        .s_axi_wdata    (s_axi_wdata),
        .s_axi_wstrb    (s_axi_wstrb),
        .s_axi_wvalid   (s_axi_wvalid),
        .s_axi_wready   (s_axi_wready),
        .s_axi_bresp    (s_axi_bresp),
        .s_axi_bvalid   (s_axi_bvalid),
        .s_axi_bready   (s_axi_bready),
        .s_axi_araddr   (s_axi_araddr),
        .s_axi_arvalid  (s_axi_arvalid),
        .s_axi_arready  (s_axi_arready),
        .s_axi_rdata    (s_axi_rdata),
        .s_axi_rresp    (s_axi_rresp),
        .s_axi_rvalid   (s_axi_rvalid),
        .s_axi_rready   (s_axi_rready),
        .ctrl_start     (ctrl_start),
        .ctrl_reset     (ctrl_reset),
        .status_busy    (status_busy),
        .status_done    (status_done),
        .status_error   (status_error),
        .sec_level      (sec_level),
        .op_mode        (op_mode),
        .cycle_count    (cycle_count),
        .error_code     (error_code),
        .data_in_addr   (data_in_addr),
        .data_in_len    (data_in_len),
        .data_out_addr  (data_out_addr),
        .data_out_len   (data_out_len),
        .mem_en         (dbuf_a_en),
        .mem_we         (dbuf_a_we),
        .mem_addr       (dbuf_a_addr),
        .mem_din        (dbuf_a_din),
        .mem_dout       (dbuf_a_dout)
    );

    // =========================================================================
    // Data buffer (16 KB true dual-port BRAM)
    // =========================================================================

    pqc_data_buffer #(
        .DEPTH      (4096),
        .ADDR_WIDTH (12)
    ) u_dbuf (
        .clk    (clk),
        .a_en   (dbuf_a_en),
        .a_we   (dbuf_a_we),
        .a_addr (dbuf_a_addr),
        .a_din  (dbuf_a_din),
        .a_dout (dbuf_a_dout),
        .b_en   (dbuf_b_en),
        .b_we   (dbuf_b_we),
        .b_addr (dbuf_b_addr),
        .b_din  (dbuf_b_din),
        .b_dout (dbuf_b_dout)
    );

    // =========================================================================
    // Sequencer
    // =========================================================================

    logic        units_clr;
    logic        h_init, h_idle, h_absorb_valid, h_absorb_ready, h_finalize;
    logic        h_squeeze_valid, h_squeeze_take;
    logic [1:0]  h_mode;
    logic [7:0]  h_absorb_byte, h_squeeze_byte;
    logic        alu_start, alu_acc, alu_done;
    logic [2:0]  alu_op;
    logic [3:0]  alu_sa, alu_sb, alu_sc;
    logic        up_start, up_check, up_done, up_range_err;
    logic        up_src_valid, up_src_take;
    logic [1:0]  up_mode;
    logic [3:0]  up_param, up_slot;
    logic [7:0]  up_src_byte;
    logic        pk_start, pk_done, pk_out_valid;
    logic [3:0]  pk_d, pk_slot;
    logic [7:0]  pk_out_byte;
    logic [1:0]  pr_sel;

    mlkem_ctrl #(
        .FIXED_LEVEL (FIXED_LEVEL)
    ) u_ctrl (
        .clk             (clk),
        .rst_n           (rst_n),
        .ctrl_start      (ctrl_start),
        .ctrl_reset      (ctrl_reset),
        .sec_level       (sec_level),
        .op_mode         (op_mode),
        .data_in_addr    (data_in_addr),
        .data_out_addr   (data_out_addr),
        .status_busy     (status_busy),
        .status_done     (status_done),
        .status_error    (status_error),
        .error_code      (error_code),
        .cycle_count     (cycle_count),
        .buf_en          (dbuf_b_en),
        .buf_we          (dbuf_b_we),
        .buf_addr        (dbuf_b_addr),
        .buf_din         (dbuf_b_din),
        .buf_dout        (dbuf_b_dout),
        .h_init          (h_init),
        .h_mode          (h_mode),
        .h_idle          (h_idle),
        .h_absorb_valid  (h_absorb_valid),
        .h_absorb_byte   (h_absorb_byte),
        .h_absorb_ready  (h_absorb_ready),
        .h_finalize      (h_finalize),
        .h_squeeze_valid (h_squeeze_valid),
        .h_squeeze_byte  (h_squeeze_byte),
        .h_squeeze_take  (h_squeeze_take),
        .alu_start       (alu_start),
        .alu_op          (alu_op),
        .alu_sa          (alu_sa),
        .alu_sb          (alu_sb),
        .alu_sc          (alu_sc),
        .alu_acc         (alu_acc),
        .alu_done        (alu_done),
        .up_start        (up_start),
        .up_mode         (up_mode),
        .up_param        (up_param),
        .up_check        (up_check),
        .up_slot         (up_slot),
        .up_done         (up_done),
        .up_range_err    (up_range_err),
        .up_src_valid    (up_src_valid),
        .up_src_byte     (up_src_byte),
        .up_src_take     (up_src_take),
        .pk_start        (pk_start),
        .pk_d            (pk_d),
        .pk_slot         (pk_slot),
        .pk_done         (pk_done),
        .pk_out_valid    (pk_out_valid),
        .pk_out_byte     (pk_out_byte),
        .pr_sel          (pr_sel),
        .units_clr       (units_clr)
    );

    // =========================================================================
    // Keccak sponge (SHA3-256/512, SHAKE128/256)
    // =========================================================================

    keccak_sponge u_sponge (
        .clk           (clk),
        .rst_n         (rst_n),
        .init          (h_init),
        .mode          (h_mode),
        .idle          (h_idle),
        .absorb_valid  (h_absorb_valid),
        .absorb_byte   (h_absorb_byte),
        .absorb_ready  (h_absorb_ready),
        .finalize      (h_finalize),
        .squeeze_valid (h_squeeze_valid),
        .squeeze_byte  (h_squeeze_byte),
        .squeeze_take  (h_squeeze_take)
    );

    // =========================================================================
    // Polynomial RAM and the three units that share it
    // =========================================================================

    logic        pr_a_en, pr_a_we, pr_b_en, pr_b_we;
    logic [10:0] pr_a_addr, pr_b_addr;
    logic [23:0] pr_a_din, pr_a_dout, pr_b_din, pr_b_dout;

    logic        alu_a_en, alu_a_we, alu_b_en, alu_b_we;
    logic [10:0] alu_a_addr, alu_b_addr;
    logic [23:0] alu_a_din, alu_b_din;
    logic        up_wr_en;
    logic [10:0] up_wr_addr;
    logic [23:0] up_wr_data;
    logic        pk_rd_en;
    logic [10:0] pk_rd_addr;

    mlkem_poly_alu u_alu (
        .clk     (clk),
        .rst_n   (rst_n),
        .clr     (units_clr),
        .start   (alu_start),
        .op      (alu_op),
        .slot_a  (alu_sa),
        .slot_b  (alu_sb),
        .slot_c  (alu_sc),
        .acc     (alu_acc),
        .done    (alu_done),
        .ra_en   (alu_a_en),
        .ra_we   (alu_a_we),
        .ra_addr (alu_a_addr),
        .ra_din  (alu_a_din),
        .ra_dout (pr_a_dout),
        .rb_en   (alu_b_en),
        .rb_we   (alu_b_we),
        .rb_addr (alu_b_addr),
        .rb_din  (alu_b_din),
        .rb_dout (pr_b_dout)
    );

    mlkem_unpack u_unpack (
        .clk       (clk),
        .rst_n     (rst_n),
        .clr       (units_clr),
        .start     (up_start),
        .mode      (up_mode),
        .param     (up_param),
        .check     (up_check),
        .slot      (up_slot),
        .done      (up_done),
        .range_err (up_range_err),
        .src_valid (up_src_valid),
        .src_byte  (up_src_byte),
        .src_take  (up_src_take),
        .wr_en     (up_wr_en),
        .wr_addr   (up_wr_addr),
        .wr_data   (up_wr_data)
    );

    mlkem_pack u_pack (
        .clk       (clk),
        .rst_n     (rst_n),
        .clr       (units_clr),
        .start     (pk_start),
        .d         (pk_d),
        .slot      (pk_slot),
        .done      (pk_done),
        .rd_en     (pk_rd_en),
        .rd_addr   (pk_rd_addr),
        .rd_data   (pr_a_dout),
        .out_valid (pk_out_valid),
        .out_byte  (pk_out_byte)
    );

    always_comb begin
        pr_b_en   = alu_b_en;
        pr_b_we   = alu_b_we;
        pr_b_addr = alu_b_addr;
        pr_b_din  = alu_b_din;
        case (pr_sel)
            2'd1: begin
                pr_a_en   = up_wr_en;
                pr_a_we   = up_wr_en;
                pr_a_addr = up_wr_addr;
                pr_a_din  = up_wr_data;
            end
            2'd2: begin
                pr_a_en   = pk_rd_en;
                pr_a_we   = 1'b0;
                pr_a_addr = pk_rd_addr;
                pr_a_din  = '0;
            end
            default: begin
                pr_a_en   = alu_a_en;
                pr_a_we   = alu_a_we;
                pr_a_addr = alu_a_addr;
                pr_a_din  = alu_a_din;
            end
        endcase
    end

    mlkem_polyram u_polyram (
        .clk    (clk),
        .a_en   (pr_a_en),
        .a_we   (pr_a_we),
        .a_addr (pr_a_addr),
        .a_din  (pr_a_din),
        .a_dout (pr_a_dout),
        .b_en   (pr_b_en),
        .b_we   (pr_b_we),
        .b_addr (pr_b_addr),
        .b_din  (pr_b_din),
        .b_dout (pr_b_dout)
    );

    // =========================================================================
    // Output length and status
    // =========================================================================

    // FIPS 203 sizes: ek = 384k+32, dk = 768k+96, c = 32(du*k+dv).
    always_comb begin
        logic [31:0] ek, dk, ct;
        case ((FIXED_LEVEL != 0) ? 32'(FIXED_LEVEL) : sec_level)
            32'd1024: begin ek = 32'd1568; dk = 32'd3168; ct = 32'd1568; end
            default:  begin ek = 32'd1184; dk = 32'd2400; ct = 32'd1088; end
        endcase
        case (op_mode)
            32'd0:   data_out_len = ek + dk;
            32'd1:   data_out_len = ct + 32'd32;
            32'd2:   data_out_len = 32'd32;
            default: data_out_len = 32'd0;
        endcase
    end

    assign irq     = status_done;
    assign o_busy  = status_busy;
    assign o_done  = status_done;
    assign o_error = status_error;

endmodule
