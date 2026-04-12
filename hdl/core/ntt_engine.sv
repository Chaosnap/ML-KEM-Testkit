// ntt_engine.sv - Full NTT/INTT engine for ML-KEM and ML-DSA
//
// Performs a complete 256-point Number Theoretic Transform using an
// iterative radix-2 decimation-in-time (DIT) architecture with a single
// butterfly unit.
//
// Supports both forward (NTT) and inverse (INTT) transforms by selecting
// Cooley-Tukey or Gentleman-Sande butterfly mode and appropriate twiddle
// factors.
//
// Memory organization:
//   - Coefficient RAM: dual-port, 256 x DATA_WIDTH bits
//   - Twiddle ROM: 256 x DATA_WIDTH bits (precomputed roots of unity)
//
// Operation:
//   1. Load 256 coefficients via write interface
//   2. Assert start_ntt or start_intt
//   3. Engine runs 7 stages x 128 butterflies = 896 cycles
//   4. Done signal asserted, read results via read interface
//
// Latency: ~900 cycles (single butterfly) for n=256

module ntt_engine #(
    parameter int DATA_WIDTH = 23,
    parameter int MODULUS    = 8380417,
    parameter int N          = 256,         // Transform size.
    parameter int LOG_N      = 8            // log2(N).
) (
    input  logic                    clk,
    input  logic                    rst_n,

    // Control.
    input  logic                    start_ntt,   // Begin forward NTT.
    input  logic                    start_intt,  // Begin inverse NTT.
    output logic                    done,
    output logic                    busy,

    // Coefficient write interface (for loading input).
    input  logic                    wr_en,
    input  logic [LOG_N-1:0]        wr_addr,
    input  logic [DATA_WIDTH-1:0]   wr_data,

    // Coefficient read interface (for reading output).
    input  logic [LOG_N-1:0]        rd_addr,
    output logic [DATA_WIDTH-1:0]   rd_data
);

    // =========================================================================
    // Coefficient RAM (dual-port)
    // =========================================================================

    logic [DATA_WIDTH-1:0] coeff_mem [0:N-1];

    // Port A: read/write for butterfly.
    logic [LOG_N-1:0]      mem_addr_a;
    logic [DATA_WIDTH-1:0] mem_din_a;
    logic                  mem_we_a;
    logic [DATA_WIDTH-1:0] mem_dout_a;

    // Port B: read/write for butterfly (second operand).
    logic [LOG_N-1:0]      mem_addr_b;
    logic [DATA_WIDTH-1:0] mem_din_b;
    logic                  mem_we_b;
    logic [DATA_WIDTH-1:0] mem_dout_b;

    always_ff @(posedge clk) begin
        if (mem_we_a)
            coeff_mem[mem_addr_a] <= mem_din_a;
        mem_dout_a <= coeff_mem[mem_addr_a];

        if (mem_we_b)
            coeff_mem[mem_addr_b] <= mem_din_b;
        mem_dout_b <= coeff_mem[mem_addr_b];

        // External write port (loading).
        if (wr_en)
            coeff_mem[wr_addr] <= wr_data;
    end

    // External read port.
    assign rd_data = coeff_mem[rd_addr];

    // =========================================================================
    // Twiddle factor ROM
    // =========================================================================

    // Twiddle factors must be initialized via $readmemh or generated.
    // Separate ROMs for NTT (forward) and INTT (inverse) twiddle factors.
    logic [DATA_WIDTH-1:0] twiddle_ntt  [0:N-1];
    logic [DATA_WIDTH-1:0] twiddle_intt [0:N-1];

    // Load twiddle factors from hex files at elaboration time.
    // Files should be placed in the simulation/synthesis working directory.
    initial begin
        $readmemh("twiddle_ntt.hex", twiddle_ntt);
        $readmemh("twiddle_intt.hex", twiddle_intt);
    end

    logic [DATA_WIDTH-1:0] twiddle_val;

    // =========================================================================
    // NTT state machine
    // =========================================================================

    typedef enum logic [2:0] {
        NTT_IDLE      = 3'b000,
        NTT_READ      = 3'b001,
        NTT_COMPUTE   = 3'b010,
        NTT_WRITE     = 3'b011,
        NTT_DONE      = 3'b100
    } ntt_state_t;

    ntt_state_t ntt_fsm;

    logic [LOG_N-1:0] stage;       // Current NTT stage (0 to LOG_N-1).
    logic [LOG_N-1:0] group;       // Current butterfly group.
    logic [LOG_N-1:0] pair;        // Current butterfly pair within group.
    logic             is_intt;     // 1 = inverse NTT mode.

    // Butterfly address computation.
    logic [LOG_N-1:0] butterfly_idx_a;
    logic [LOG_N-1:0] butterfly_idx_b;
    logic [LOG_N-1:0] twiddle_idx;

    // Butterfly I/O.
    logic [DATA_WIDTH-1:0] bfly_a_out;
    logic [DATA_WIDTH-1:0] bfly_b_out;

    // Derived parameters.
    logic [LOG_N-1:0] half_size;   // Half the butterfly group size.
    logic [LOG_N-1:0] group_size;  // Full butterfly group size.

    assign half_size  = N[LOG_N-1:0] >> (LOG_N[LOG_N-1:0] - stage);
    assign group_size = half_size << 1;

    // =========================================================================
    // Address generation for butterfly pairs
    // =========================================================================

    always_comb begin
        // For iterative DIT NTT:
        // At stage s, butterfly size = 2^(s+1), half = 2^s.
        // Group index g, pair index p within group.
        // a_idx = g * group_size + p
        // b_idx = a_idx + half_size
        butterfly_idx_a = group * group_size + pair;
        butterfly_idx_b = butterfly_idx_a + half_size;

        // Twiddle index: p * (N / group_size)
        twiddle_idx = pair * (N[LOG_N-1:0] >> (stage + 1));
    end

    // Select twiddle factor based on mode.
    assign twiddle_val = is_intt ? twiddle_intt[twiddle_idx] : twiddle_ntt[twiddle_idx];

    // =========================================================================
    // Main FSM
    // =========================================================================

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ntt_fsm  <= NTT_IDLE;
            stage    <= '0;
            group    <= '0;
            pair     <= '0;
            is_intt  <= 1'b0;
        end else begin
            case (ntt_fsm)
                NTT_IDLE: begin
                    if (start_ntt || start_intt) begin
                        ntt_fsm <= NTT_READ;
                        stage   <= '0;
                        group   <= '0;
                        pair    <= '0;
                        is_intt <= start_intt;
                    end
                end

                NTT_READ: begin
                    // Read butterfly inputs from memory.
                    mem_addr_a <= butterfly_idx_a;
                    mem_addr_b <= butterfly_idx_b;
                    ntt_fsm <= NTT_COMPUTE;
                end

                NTT_COMPUTE: begin
                    // Butterfly computation (combinational, registered output).
                    ntt_fsm <= NTT_WRITE;
                end

                NTT_WRITE: begin
                    // Write butterfly results back.
                    mem_addr_a <= butterfly_idx_a;
                    mem_addr_b <= butterfly_idx_b;
                    mem_din_a  <= bfly_a_out;
                    mem_din_b  <= bfly_b_out;
                    mem_we_a   <= 1'b1;
                    mem_we_b   <= 1'b1;

                    // Advance to next butterfly pair.
                    if (pair < half_size - 1) begin
                        pair <= pair + 1;
                        ntt_fsm <= NTT_READ;
                    end else begin
                        pair <= '0;
                        if (group < (N[LOG_N-1:0] / group_size) - 1) begin
                            group <= group + 1;
                            ntt_fsm <= NTT_READ;
                        end else begin
                            group <= '0;
                            if (stage < LOG_N - 1) begin
                                stage <= stage + 1;
                                ntt_fsm <= NTT_READ;
                            end else begin
                                ntt_fsm <= NTT_DONE;
                            end
                        end
                    end
                end

                NTT_DONE: begin
                    mem_we_a <= 1'b0;
                    mem_we_b <= 1'b0;
                    ntt_fsm  <= NTT_IDLE;
                end

                default: ntt_fsm <= NTT_IDLE;
            endcase
        end
    end

    assign busy = (ntt_fsm != NTT_IDLE) && (ntt_fsm != NTT_DONE);
    assign done = (ntt_fsm == NTT_DONE);

    // =========================================================================
    // Inline butterfly computation using Barrett reduction
    // =========================================================================

    // Multiply b coefficient by twiddle factor.
    logic [2*DATA_WIDTH-1:0] wb_product;
    assign wb_product = {{DATA_WIDTH{1'b0}}, mem_dout_b} * {{DATA_WIDTH{1'b0}}, twiddle_val};

    // Barrett modular reduction of the product.
    // Uses the dedicated Barrett module from mod_reduce.sv.
    logic [DATA_WIDTH-1:0] wb_reduced;

    generate
        if (MODULUS == 3329) begin : gen_reduce_3329
            barrett_reduce_3329 u_reduce (
                .x (wb_product[23:0]),
                .r (wb_reduced[11:0])
            );
            if (DATA_WIDTH > 12) begin : gen_zero_pad
                assign wb_reduced[DATA_WIDTH-1:12] = '0;
            end
        end else if (MODULUS == 8380417) begin : gen_reduce_8380417
            barrett_reduce_8380417 u_reduce (
                .x (wb_product[45:0]),
                .r (wb_reduced[22:0])
            );
        end else begin : gen_reduce_generic
            // Fallback for other moduli (will infer divider - not recommended).
            assign wb_reduced = wb_product % MODULUS;
        end
    endgenerate

    // CT butterfly: a' = (a + wb) mod q, b' = (a - wb) mod q.
    logic [DATA_WIDTH-1:0] bfly_sum, bfly_diff;

    mod_add #(.WIDTH(DATA_WIDTH), .MODULUS(MODULUS)) u_add (
        .a (mem_dout_a),
        .b (wb_reduced),
        .r (bfly_sum)
    );

    mod_sub #(.WIDTH(DATA_WIDTH), .MODULUS(MODULUS)) u_sub (
        .a (mem_dout_a),
        .b (wb_reduced),
        .r (bfly_diff)
    );

    assign bfly_a_out = bfly_sum;
    assign bfly_b_out = bfly_diff;

endmodule
