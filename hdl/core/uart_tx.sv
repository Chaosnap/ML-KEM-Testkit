// uart_tx.sv - UART transmitter with configurable baud rate
//
// Standard 8N1 UART transmitter. Accepts a byte and valid pulse,
// serializes it with start and stop bits at the configured baud rate.

module uart_tx #(
    parameter int CLK_FREQ  = 100_000_000,
    parameter int BAUD_RATE = 115200
) (
    input  logic       clk,
    input  logic       rst_n,
    output logic       tx,        // Serial output.
    input  logic [7:0] data,      // Byte to transmit.
    input  logic       valid,     // Pulse to start transmission.
    output logic       ready      // High when ready to accept new byte.
);

    localparam int CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;

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

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            clk_cnt   <= '0;
            bit_idx   <= '0;
            shift_reg <= '0;
            tx        <= 1'b1;  // Idle high.
            ready     <= 1'b1;
        end else begin
            case (state)
                S_IDLE: begin
                    tx    <= 1'b1;
                    ready <= 1'b1;
                    if (valid) begin
                        shift_reg <= data;
                        state     <= S_START;
                        clk_cnt   <= '0;
                        ready     <= 1'b0;
                    end
                end

                S_START: begin
                    tx <= 1'b0;  // Start bit.
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        clk_cnt <= '0;
                        bit_idx <= '0;
                        state   <= S_DATA;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end

                S_DATA: begin
                    tx <= shift_reg[bit_idx];  // LSB first.
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        clk_cnt <= '0;
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
                    tx <= 1'b1;  // Stop bit.
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
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
