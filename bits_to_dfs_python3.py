#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: set ts=4 sw=4 tw=0 et pm=:
import fileinput
import sys

"""
VOC: i-1443338945.6543-t1 033399141 1625872817  81% 0.027 179 L:no LCW(0,001111,100000000000000000000 E1) 01111001000100010010010011011011011001111    011000010000100001110101111011110010010111011001010001011101010001100000000110010100000110111110010101110101001111010100111001000110100110001110110    1010101010010010001000001110011000001001001010011110011100110100111110001101110010110101010110011101011100011101011000000000 descr_extra:

"""


def turn_symbols(byte):
    """
    this definition is not used in this file
    """
    out = 0
    if byte & 0x01:
        out |= 0x02
    if byte & 0x02:
        out |= 0x01
    if byte & 0x04:
        out |= 0x08
    if byte & 0x08:
        out |= 0x04

    if byte & 0x10:
        out |= 0x20
    if byte & 0x20:
        out |= 0x10
    if byte & 0x40:
        out |= 0x80
    if byte & 0x80:
        out |= 0x40

    return out


def chunks(l, n):
    """ Yield successive n-sized chunks from l.
    """
    for i in range(0, len(l), n):
        yield l[i:i + n]


infile = sys.argv[1]
outfile = open(sys.argv[2], 'wb')

data = ''

for line in fileinput.input(infile):
    line = line.split()
    if line[0] == 'VOC:':
        if int(line[6]) < 179:
            continue
        data = line[10]
        if (data[0] == "["):
            for pos in range(1, len(data), 3):
                '''
                Python自带的 int() 函数，其实就有两个参数，我们既可以传一个参数，又可以传两个参数：
                >>> int('123')
                123
                >>> int('123', 8)
                83
                int()函数的第二个参数是转换进制，如果不传，默认是十进制 (base=10)，如果传了，就用传入的参数。
                '''
                print(data[pos:pos + 2])
                byte = int(data[pos:pos + 2], 16)
                print(byte)
                byte = int('{:08b}'.format(byte)[::-1], 2)
                print(byte)
                '''
                str.format()方法会返回一个新的字符串
                在新的字符串中，原字符串的替换字段（大括号中的数字是预留的替换字段）被format方法中的参数代替。
                '''
                # outfile.write(chr(byte))
                outfile.write(bytes([byte]))
                '''
                python2 uses ASCII as its character encoding
                python3 uses Unicode as its character encoding
                chr() return different value in python2 and python3 
                '''
        else:
            for bits in chunks(data, 8):
                byte = int(bits[::-1], 2)
                outfile.write(chr(byte))
