#!/usr/bin/python
# vim: set ts=4 sw=4 tw=0 et fenc=utf8 pm=:
# VOC: i-1430527570.4954-t1 421036605 1625859953  66% 0.008 219 L:no LCW(0,001111,100000000000000000000 E1) 101110110101010100101101111000111111111001011111001011010001000010010001101110011010011001111111011101111100011001001001000111001101001011001011000101111111101110110011111000000001110010001110101101001010011001101001010111101100011100110011110010110110101010110001010000100100101011010010100100100011010110101001

import sys
import matplotlib.pyplot as plt
import os


def filter_voice(t_start=None, t_stop=None, f_min=None, f_max=None):
    time_array = []
    frequency_array = []
    line_array = []
    quality_array = []
    f = open(sys.argv[1])

    for line in f:
        line = line.strip()
        if 'VOC: ' in line and not "LCW(0,001111,100000000000000000000" in line:
            line_split = line.split()
            oknok = 0
            if line_split[1] == 'VOC:':
                oknok = int(line_split[0][len(line_split[0]) - 1])
                line_split = line_split[1:]
            oknok = ['red', 'orange', 'green'][oknok]
            # ts_base = int(line[1].split('-')[1].split('.')[0])
            ts_base = 0
            ts = ts_base + float(line_split[2]) / 1000.
            f = int(line_split[3]) / 1000.
            '''
            ts line_split[2] time
            f line_split[3] frequency
            '''
            if ((not t_start or t_start <= ts) and
                    (not t_stop or ts <= t_stop) and
                    (not f_min or f_min <= f) and
                    (not f_max or f <= f_max)):
                time_array.append(ts)
                frequency_array.append(f)
                quality_array.append(oknok)
                line_array.append(line)
            '''
            a line of the file
            '''
    return time_array, frequency_array, quality_array, line_array


def filter_sync(t_start=None, t_stop=None, f_min=None, f_max=None):
    time_array = []
    frequency_array = []
    line_array = []
    quality_array = []
    f = open(sys.argv[1])

    for line in f:
        line = line.strip()
        if 'ISY: ' in line and not "LCW(0,001111,100000000000000000000" in line:
            line_split = line.split()
            oknok = 0
            if line_split[1] == 'ISY:':
                oknok = int(line_split[0][len(line_split[0]) - 1])
                line_split = line_split[1:]
            oknok = ['red', 'orange', 'green'][oknok]
            # ts_base = int(line[1].split('-')[1].split('.')[0])
            ts_base = 0
            ts = ts_base + float(line_split[2]) / 1000.
            f = int(line_split[3]) / 1000.
            '''
            ts line_split[2] time
            f line_split[3] frequency
            '''
            if ((not t_start or t_start <= ts) and
                    (not t_stop or ts <= t_stop) and
                    (not f_min or f_min <= f) and
                    (not f_max or f <= f_max)):
                time_array.append(ts)
                frequency_array.append(f)
                quality_array.append(oknok)
                line_array.append(line)
            '''
            a line of the file
            '''
    return time_array, frequency_array, quality_array, line_array


def filter_data(t_start=None, t_stop=None, f_min=None, f_max=None):
    time_array = []
    frequency_array = []
    line_array = []
    quality_array = []
    f = open(sys.argv[1])

    for line in f:
        line = line.strip()
        if 'IDA: ' in line and not "LCW(0,001111,100000000000000000000" in line:
            line_split = line.split()
            oknok = 0
            if line_split[1] == 'IDA:':
                oknok = int(line_split[0][len(line_split[0]) - 1])
                line_split = line_split[1:]
            oknok = ['red', 'orange', 'green'][oknok]
            # ts_base = int(line[1].split('-')[1].split('.')[0])
            ts_base = 0
            ts = ts_base + float(line_split[2]) / 1000.
            f = int(line_split[3]) / 1000.
            '''
            ts line_split[2] time
            f line_split[3] frequency
            '''
            if ((not t_start or t_start <= ts) and
                    (not t_stop or ts <= t_stop) and
                    (not f_min or f_min <= f) and
                    (not f_max or f <= f_max)):
                time_array.append(ts)
                frequency_array.append(f)
                quality_array.append(oknok)
                line_array.append(line)
            '''
            a line of the file
            '''
    return time_array, frequency_array, quality_array, line_array


def main():
    fig = plt.figure()
    ax = fig.add_subplot(111)

    voice_time_array, voice_frequency_array, voice_quality_array, _ = filter_voice()
    ax.scatter(x=voice_time_array, y=voice_frequency_array, c='red', s=50, label='voice')

    sync_time_array, sync_frequency_array, sync_quality_array, _ = filter_sync()
    ax.scatter(x=sync_time_array, y=sync_frequency_array, c='green', s=25, label='sync')

    data_time_array, data_frequency_array, data_quality_array, _ = filter_data()
    ax.scatter(x=data_time_array, y=data_frequency_array, c='orange', s=10, label='data')

    plt.legend()

    t_start = None
    t_stop = None
    f_min = None
    f_max = None

    plt.title(
        "Click once left and once rigth to define an area. The script will try to play iridium using the play-iridium-ambe shell script.")
    plt.show()


if __name__ == "__main__":
    main()
