// mlkem_word_fifo.sv - Six live words in an eight-word banked LUT RAM.
//
// The sequencer can append one word and remove zero, one or two words in
// the same cycle. Even/odd banks each need only one asynchronous read port:
// consecutive head words always reside in different banks. No payload reset
// or shifts: only the pointers/count are reset. Logical capacity stays six,
// matching the controller's reservations for three outstanding BRAM reads.
//
// Timing: the bank read addresses, the head-select bit and the fill flags
// (nonempty, two) are registers computed from the next read pointer / count,
// not decoded from rd_ptr / count after the clock edge. The heads and flags
// feed the sponge, unpack and pop logic in the same cycle; at 206 MHz the
// address adder and the count compares in front of them were the worst
// paths of the core. Every output is still the same function of the FIFO
// state in every cycle -- only where the logic sits changed.
module mlkem_word_fifo (
    input  logic        clk,
    input  logic        clr,
    input  logic        push,
    input  logic [31:0] din,
    input  logic [1:0]  pop_count,
    output logic [31:0] head0,
    output logic [31:0] head1,
    output logic [2:0]  count,
    output logic        nonempty,       // count != 0
    output logic        two             // count >= 2
);
    (* ram_style = "distributed" *) logic [31:0] even_mem [0:3];
    (* ram_style = "distributed" *) logic [31:0] odd_mem [0:3];
    logic [2:0] rd_ptr, wr_ptr, rd_next, count_next;
    logic [1:0] even_addr, odd_addr;    // bank read addresses for rd_ptr (registered)
    logic       head_odd;               // rd_ptr[0]: head0 is in the odd bank (registered)
    logic [31:0] even_head, odd_head;

    assign rd_next    = rd_ptr + {1'b0, pop_count};
    assign count_next = count - {1'b0, pop_count} + {2'b0, push};

    assign even_head = even_mem[even_addr];
    assign odd_head  = odd_mem[odd_addr];
    assign head0 = head_odd ? odd_head : even_head;
    assign head1 = head_odd ? even_head : odd_head;

    always_ff @(posedge clk) begin
        if (!clr && push) begin
            if (wr_ptr[0]) odd_mem[wr_ptr[2:1]] <= din;
            else even_mem[wr_ptr[2:1]] <= din;
        end
    end
    always_ff @(posedge clk) begin
        if (clr) begin
            rd_ptr    <= '0;
            wr_ptr    <= '0;
            count     <= '0;
            even_addr <= '0;
            odd_addr  <= '0;
            head_odd  <= 1'b0;
            nonempty  <= 1'b0;
            two       <= 1'b0;
        end else begin
            rd_ptr    <= rd_next;
            even_addr <= rd_next[2:1] + {1'b0, rd_next[0]};
            odd_addr  <= rd_next[2:1];
            head_odd  <= rd_next[0];
            if (push) wr_ptr <= wr_ptr + 3'd1;
            count     <= count_next;
            nonempty  <= count_next != 3'd0;
            two       <= count_next >= 3'd2;
        end
    end
endmodule
