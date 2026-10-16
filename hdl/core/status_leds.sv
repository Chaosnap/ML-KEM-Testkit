// status_leds.sv - Board status LEDs that stay still while the core computes
//
// Four green LEDs and up to four RGB LEDs, driven from the core's STATUS
// bits and OP_MODE and the UART bridge's reply events.
//
//   led[0]  activity    lit for 0.1 s after every operation (an operation
//                       itself is ~0.13 ms: too short to see)
//   led[1]  done        last operation finished without error
//   led[2]  error       last operation ended with an error
//   led[3]  heartbeat   1 Hz blink: bitstream loaded, clock running
//
//   RGB 0   power       red while the reset button is held or the core clock
//                       is not locked, dim green when running
//   RGB 1   host link   blue flash per reply frame, red (0.5 s) per rejected
//                       frame (bad CRC / length / command)
//   RGB 2   completion  green flash per 0x05 completion notice
//   RGB 3   operation   the last operation, dim: green Decaps, blue Encaps,
//                       magenta KeyGen; red when it failed
//
//   N_RGB = 2 (Arty Z7, LD4/LD5): RGB 0 as above; RGB 1 is the host link and
//   also flashes green per completion notice.
//
// Side-channel rule: nothing in this module switches while the core is
// busy. Every register, counter and output holds its value from the
// synchronised STATUS.busy (two clocks after the operation starts, long
// before the first BMUL window) until the operation ends, so LED activity
// never lands inside a power trace. Events that arrive meanwhile are not
// shown (the host never sends frames during an operation anyway).
//
// The RGB LEDs are dimmed to 1/8 duty at 1 kHz: Digilent's RGB LEDs are
// glaring at full drive. Flashes use full brightness.

module status_leds #(
    parameter int CLK_FREQ = 100_000_000,
    parameter int N_RGB    = 4            // 4 (Arty A7) or 2 (Arty Z7)
) (
    input  logic             clk,
    input  logic             rst_n,

    input  logic             in_reset,     // async: reset held or core clock not locked
    input  logic             core_busy,    // async, core clock domain (STATUS bits)
    input  logic             core_done,
    input  logic             core_error,
    input  logic [1:0]       core_op,      // async, quasi-static (OP_MODE)
    input  logic             ev_ok,        // clk domain, one-cycle pulses from the bridge
    input  logic             ev_err,
    input  logic             ev_run,

    output logic [3:0]       led,
    output logic [N_RGB-1:0] rgb_r,
    output logic [N_RGB-1:0] rgb_g,
    output logic [N_RGB-1:0] rgb_b
);
    localparam int TICK_DIV = CLK_FREQ / 8000;   // 8 kHz tick: PWM phase; 8 ticks = 1 ms
    localparam int ACT_MS = 100, FLASH_MS = 40, ERR_MS = 500, BEAT_MS = 500;

    // Core status into this clock domain.
    (* ASYNC_REG = "TRUE" *) logic [4:0] s1, s2;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin s1 <= '0; s2 <= '0; end
        else begin s1 <= {core_op, core_error, core_done, core_busy}; s2 <= s1; end
    end
    logic busy, done, error;
    logic [1:0] op;
    assign {op, error, done, busy} = s2;

    logic run;                      // everything below only updates while the core is idle
    assign run = !busy;

    logic        busy_q;
    logic [$clog2(TICK_DIV)-1:0] div;
    logic [2:0]  ph;                // PWM phase; ms tick when it wraps
    logic        ms;
    logic [9:0]  act, f_ok, f_err, f_run, beat;
    logic        beat_on, have_op;
    logic [1:0]  op_last;
    assign ms = (div == '0) && (ph == 3'd7);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy_q <= 1'b0; div <= '0; ph <= '0;
            act <= '0; f_ok <= '0; f_err <= '0; f_run <= '0; beat <= '0; beat_on <= 1'b0;
            have_op <= 1'b0; op_last <= '0;
        end else if (run) begin
            busy_q <= busy;
            div <= (div == $bits(div)'(TICK_DIV - 1)) ? '0 : div + 1'b1;
            if (div == '0) ph <= ph + 3'd1;
            if (busy_q) begin            // an operation just ended
                act     <= 10'(ACT_MS);
                have_op <= 1'b1;
                op_last <= op;           // OP_MODE is stable during an operation
            end else if (ms && act != '0) act <= act - 10'd1;
            if (ev_ok)  f_ok  <= 10'(FLASH_MS); else if (ms && f_ok  != '0) f_ok  <= f_ok  - 10'd1;
            if (ev_err) f_err <= 10'(ERR_MS);   else if (ms && f_err != '0) f_err <= f_err - 10'd1;
            if (ev_run) f_run <= 10'(FLASH_MS); else if (ms && f_run != '0) f_run <= f_run - 10'd1;
            if (ms) begin
                if (beat == 10'(BEAT_MS - 1)) begin beat <= '0; beat_on <= !beat_on; end
                else beat <= beat + 10'd1;
            end
        end else
            busy_q <= busy;              // the one flop that follows the operation start
    end

    // Outputs: registered, held while the core is busy.
    logic dim;
    assign dim = (ph == 3'd0);
    logic [3:0] led_n;
    logic [3:0] r_n, g_n, b_n;      // RGB 0-3 before the N_RGB mapping
    always_comb begin
        led_n = {beat_on, error, done && !error, act != '0};
        r_n = '0; g_n = '0; b_n = '0;
        g_n[0] = dim;                                        // power (red: see below)
        b_n[1] = f_ok != '0;   r_n[1] = f_err != '0;         // host link
        g_n[2] = f_run != '0;                                // completion notice
        if (have_op) begin                                   // last operation
            if (error)                r_n[3] = dim;          // STATUS.error is sticky until the next START
            else if (op_last == 2'd2) g_n[3] = dim;          // Decaps
            else if (op_last == 2'd1) b_n[3] = dim;          // Encaps
            else begin                r_n[3] = dim; b_n[3] = dim; end   // KeyGen
        end
        if (N_RGB < 4) g_n[1] = g_n[1] | g_n[2];            // Z7: completion on the link LED
    end

    /* verilator lint_off UNUSEDSIGNAL */   // RGB 2-3 unused when N_RGB = 2
    logic [3:0] r_q, g_q, b_q;
    /* verilator lint_on UNUSEDSIGNAL */
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            led <= '0; r_q <= '0; g_q <= '0; b_q <= '0;
        end else if (run) begin
            led <= led_n; r_q <= r_n; g_q <= g_n; b_q <= b_n;
        end
    end

    // RGB 0 red follows the reset request directly (the registers are in reset then).
    always_comb begin
        rgb_r = r_q[N_RGB-1:0];
        rgb_g = g_q[N_RGB-1:0];
        rgb_b = b_q[N_RGB-1:0];
        rgb_r[0] = in_reset;
        rgb_g[0] = g_q[0] && !in_reset;
    end
endmodule
