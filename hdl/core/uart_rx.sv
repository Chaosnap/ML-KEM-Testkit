// uart_rx.sv - UART receiver with configurable baud rate
//
// Standard 8N1 UART receiver. Oversamples the RX line at 16x the baud
// rate for reliable bit sampling. Outputs received bytes with a valid pulse.

module uart_rx #(
    parameter int CLK_FREQ  = 100_000_000,  // System clock frequency in Hz.
    parameter int BAUD_RATE = 115200         // UART baud rate.
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       rx,        // Serial input.
    output logic [7:0] data,      // Received byte.
    output logic       valid      // Pulse when data is valid.
);

    localparam int CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;
    localparam int HALF_BIT     = CLKS_PER_BIT / 2;

    typedef enum logic [2:0] {
        S_IDLE,
        S_START,
        S_DATA,
        S_STOP
    } state_t;

    state_t state;
    logic [$clog2(CLKS_PER_BIT)-1:0] clk_cnt;
    logic [2:0] bit_idx;
    logic [7:0] shift_reg;

    // Synchronize RX input (2-stage synchronizer).
    logic rx_sync1, rx_sync2;
    always_ff @(posedge clk) begin
        rx_sync1 <= rx;
        rx_sync2 <= rx_sync1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            clk_cnt   <= '0;
            bit_idx   <= '0;
            shift_reg <= '0;
            data      <= '0;
            valid     <= 1'b0;
        end else begin
            valid <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (!rx_sync2) begin  // Start bit detected (falling edge).
                        state   <= S_START;
                        clk_cnt <= '0;
                    end
                end

                S_START: begin
                    // Sample at midpoint of start bit.
                    if (clk_cnt == HALF_BIT) begin
                        if (!rx_sync2) begin  // Confirm start bit is still low.
                            clk_cnt <= '0;
                            bit_idx <= '0;
                            state   <= S_DATA;
                        end else begin
                            state <= S_IDLE;  // False start.
                        end
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end

                S_DATA: begin
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        clk_cnt <= '0;
                        shift_reg[bit_idx] <= rx_sync2;  // LSB first.
                        if (bit_idx == 3'd7) begin
                            state <= S_STOP;
                        end else begin
                            bit_idx <= bit_idx + 1;
                        end
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end

                S_STOP: begin
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        data  <= shift_reg;
                        valid <= 1'b1;
                        state <= S_IDLE;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
