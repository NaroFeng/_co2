// This file is part of www.nand2tetris.org
// and the book "The Elements of Computing Systems"
// by Nisan and Schocken, MIT Press.
// File name: projects/04/Fill.asm

// Runs an infinite loop that listens to the keyboard input.
// When a key is pressed (any key), the program blackens the screen,
// i.e. writes "black" in every pixel;
// the screen should remain fully black as long as the key is pressed. 
// When no key is pressed, the program clears the screen, i.e. writes
// "white" in every pixel;
// the screen should remain fully clear as long as no key is pressed.

(LISTEN)
    // Read the current keyboard state
    @KBD
    D=M

    // If KBD > 0, set target color to black (-1 = 0xFFFF, all bits on)
    @SET_BLACK
    D;JGT

    // Otherwise, set target color to white (0 = 0x0000, all bits off)
    D=0
    @SET_COLOR
    0;JMP

(SET_BLACK)
    D=-1

(SET_COLOR)
    // Store selected color pattern in R0
    @R0
    M=D

    // Initialize address pointer to SCREEN (RAM[16384])
    @SCREEN
    D=A
    @ptr
    M=D

(FILL_LOOP)
    // If ptr == KBD (24576), we reached the end of screen memory
    @ptr
    D=M
    @KBD
    D=D-A
    @LISTEN
    D;JEQ     // Loop back to check keyboard input again

    // Write color from R0 to current pixel word (*ptr)
    @R0
    D=M
    @ptr
    A=M
    M=D

    // Increment pointer to next word
    @ptr
    M=M+1

    // Continue screen fill loop
    @FILL_LOOP
    0;JMP