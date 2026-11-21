// push constant 17
@17
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 17
@17
D=A
@SP
A=M
M=D
@SP
M=M+1
// eq
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JEQ_1
D;JEQ
D=0
@END_JEQ_1
0;JMP
(IF_JEQ_1)
D=-1
(END_JEQ_1)
@SP
A=M
A=A-1
M=D
// push constant 17
@17
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 16
@16
D=A
@SP
A=M
M=D
@SP
M=M+1
// eq
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JEQ_2
D;JEQ
D=0
@END_JEQ_2
0;JMP
(IF_JEQ_2)
D=-1
(END_JEQ_2)
@SP
A=M
A=A-1
M=D
// push constant 16
@16
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 17
@17
D=A
@SP
A=M
M=D
@SP
M=M+1
// eq
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JEQ_3
D;JEQ
D=0
@END_JEQ_3
0;JMP
(IF_JEQ_3)
D=-1
(END_JEQ_3)
@SP
A=M
A=A-1
M=D
// push constant 892
@892
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 891
@891
D=A
@SP
A=M
M=D
@SP
M=M+1
// lt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JLT_4
D;JLT
D=0
@END_JLT_4
0;JMP
(IF_JLT_4)
D=-1
(END_JLT_4)
@SP
A=M
A=A-1
M=D
// push constant 891
@891
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 892
@892
D=A
@SP
A=M
M=D
@SP
M=M+1
// lt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JLT_5
D;JLT
D=0
@END_JLT_5
0;JMP
(IF_JLT_5)
D=-1
(END_JLT_5)
@SP
A=M
A=A-1
M=D
// push constant 891
@891
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 891
@891
D=A
@SP
A=M
M=D
@SP
M=M+1
// lt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JLT_6
D;JLT
D=0
@END_JLT_6
0;JMP
(IF_JLT_6)
D=-1
(END_JLT_6)
@SP
A=M
A=A-1
M=D
// push constant 32767
@32767
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 32766
@32766
D=A
@SP
A=M
M=D
@SP
M=M+1
// gt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JGT_7
D;JGT
D=0
@END_JGT_7
0;JMP
(IF_JGT_7)
D=-1
(END_JGT_7)
@SP
A=M
A=A-1
M=D
// push constant 32766
@32766
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 32767
@32767
D=A
@SP
A=M
M=D
@SP
M=M+1
// gt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JGT_8
D;JGT
D=0
@END_JGT_8
0;JMP
(IF_JGT_8)
D=-1
(END_JGT_8)
@SP
A=M
A=A-1
M=D
// push constant 32766
@32766
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 32766
@32766
D=A
@SP
A=M
M=D
@SP
M=M+1
// gt
@SP
AM=M-1
D=M
A=A-1
D=M-D
@IF_JGT_9
D;JGT
D=0
@END_JGT_9
0;JMP
(IF_JGT_9)
D=-1
(END_JGT_9)
@SP
A=M
A=A-1
M=D
// push constant 57
@57
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 31
@31
D=A
@SP
A=M
M=D
@SP
M=M+1
// push constant 53
@53
D=A
@SP
A=M
M=D
@SP
M=M+1
// add
@SP
AM=M-1
D=M
A=A-1
M=D+M
// push constant 112
@112
D=A
@SP
A=M
M=D
@SP
M=M+1
// sub
@SP
AM=M-1
D=M
A=A-1
M=M-D
// neg
@SP
A=M
A=A-1
D=M
M=-D
// and
@SP
AM=M-1
D=M
A=A-1
M=D&M
// push constant 82
@82
D=A
@SP
A=M
M=D
@SP
M=M+1
// or
@SP
AM=M-1
D=M
A=A-1
M=D|M
// not
@SP
A=M
A=A-1
D=M
M=!D
