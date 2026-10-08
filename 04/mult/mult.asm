// This file is part of www.nand2tetris.org
// and the book "The Elements of Computing Systems"
// by Nisan and Schocken, MIT Press.
// File name: projects/04/Mult.asm

// Multiplies R0 and R1 and stores the result in R2.
// (R0, R1, R2 refer to RAM[0], RAM[1], and RAM[2], respectively.)

    // Initialize R2 = 0
    @R2
    M=0

    // Copy multiplier R1 to counter variable i
    @R1
    D=M
    @i
    M=D

(LOOP)
    // If i <= 0, multiplication is complete
    @i
    D=M
    @END
    D;JLE

    // Add multiplicand R0 to accumulator R2
    @R0
    D=M
    @R2
    M=D+M

    // Decrement counter i
    @i
    M=M-1

    // Repeat loop
    @LOOP
    0;JMP

(END)
    // Infinite loop to terminate program safely
    @END
    0;JMP