#!/usr/bin/env python
# vim: set ts=4 sw=4 tw=0 et pm=:
from __future__ import print_function
import sys
import re
import struct
from bch import ndivide, nrepair, bch_repair
import crcmod
import rs
import rs6
import fileinput
import getopt
import types
import copy
import datetime
import collections

izip = zip
# from itertools import zip
from math import sqrt, atan2, pi

options, remainder = getopt.getopt(sys.argv[1:], 'vgi:o:pes', [
    'verbose',
    'good',
    'uw-ec',
    'harder',
    'confidence=',
    'input=',
    'output=',
    'perfect',
    'errorfree',
    'interesting',
    'satclass',
    'plot=',
    'filter=',
    'voice-dump=',
    'format=',
    'errorfile=',
    'errorstats',
    'forcetype=',
    'globaltime',
    'channelize',
])

iridium_access = "001100000011000011110011"  # Actually 0x789h in BPSK
uplink_access = "110011000011110011111100"  # BPSK: 0xc4b
UW_DOWNLINK = [0, 2, 2, 2, 2, 0, 0, 0, 2, 0, 0, 2]
UW_UPLINK = [2, 2, 0, 0, 0, 2, 0, 0, 2, 0, 2, 2]
iridium_lead_out = "100101111010110110110011001111"
header_messaging = "00110011111100110011001111110011"  # 0x9669 in BPSK
header_time_location = "11" + "0" * 94
messaging_bch_poly = 1897
ringalert_bch_poly = 1207
acch_bch_poly = 3545  # 1207 also works?
hdr_poly = 29  # IBC header
base_freq = 1616e6
channel_width = 41667

verbose = False
perfect = False
errorfree = False
interesting = False
good = False
uwec = False
harder = False
dosatclass = False
input = "raw"
output = "line"
ofmt = None
linefilter = {'type': 'All', 'attr': None, 'check': None}
plotargs = ["time", "frequency"]
vdumpfile = None
errorfile = None
errorstats = None
forcetype = None
globaltime = False
channelize = False

for opt, arg in options:
    if opt in ('-v', '--verbose'):
        verbose = True
    elif opt in ('-g', '--good'):
        good = True
        min_confidence = 90
    elif opt in ('--uw-ec'):
        uwec = True
    elif opt in ('--harder'):
        harder = True
    elif opt in ('--confidence'):
        good = True
        min_confidence = int(arg)
    elif opt in ('--interesting'):
        interesting = True
    elif opt in ('-p', '--perfect'):
        perfect = True
    elif opt in ('-e', '--errorfree'):
        errorfree = True
    elif opt in ('-s', '--satclass'):
        dosatclass = True
    elif opt in ('--plot'):
        plotargs = arg.split(',')
    elif opt in '--filter':
        linefilter['type'] = arg
        if ',' in linefilter['type']:
            (linefilter['type'], linefilter['check']) = linefilter['type'].split(',', 2)
        if '+' in linefilter['type']:
            (linefilter['type'], linefilter['attr']) = linefilter['type'].split('+')
    elif opt in ('--voice-dump'):
        vdumpfile = arg
    elif opt in ('-i', '--input'):
        input = arg
    elif opt in ('-o', '--output'):
        output = arg
    elif opt in ('--errorfile'):
        errorfile = arg
    elif opt in ('--errorstats'):
        errorstats = {}
    elif opt in ('--forcetype'):
        forcetype = arg
    elif opt in ('--channelize'):
        channelize = True
    elif opt in ('--format'):
        ofmt = arg.split(',');
    elif opt in ('--globaltime'):
        globaltime = True;
    else:
        raise Exception("unknown argument?")

if input == "dump" or output == "dump":
    import cPickle as pickle

    dumpfile = "pickle.dump"

if dosatclass:
    import satclass

    satclass.init()

if vdumpfile != None:
    vdumpfile = open(vdumpfile, "wb")

if errorfile != None:
    errorfile = open(errorfile, "w")


class ParserError(Exception):
    pass


class Zulu(datetime.tzinfo):
    def utcoffset(self, dt):
        return datetime.timedelta(0)

    def dst(self, dt):
        return datetime.timedelta(0)

    def tzname(self, dt):
        return "Z"


Z = Zulu()
tswarning = False
tsoffset = 0
maxts = 0


def fmt_iritime(iritime):
    # Different Iridium epochs that we know about:
    # 2014-05-11T14:23:55Z : 1399818235 current one
    # 2007-03-08T03:50:21Z : 1173325821
    # 1996-06-01T00:00:11Z :  833587211 the original one
    uxtime = float(iritime) * 90 / 1000 + 1399818235
    strtime = datetime.datetime.fromtimestamp(uxtime, tz=Z).strftime(
        "%Y-%m-%dT%H:%M:%S.{:02.0f}Z".format((uxtime % 1) * 100))
    return (uxtime, strtime)


class Message(object):
    def __init__(self, line):
        self.parse_error = False
        self.error = False
        self.error_msg = []
        self.lineno = fileinput.lineno()
        p = re.compile(
            '(RAW|RWA): ([^ ]*) ([\d.]+) (\d+) A:(\w+) [IL]:(\w+) +(\d+)% ([\d.]+|inf|nan) +(\d+) ([\[\]<> 01]+)(.*)')
        m = p.match(line)
        if (errorfile != None):
            self.line = line
        if (not m):
            self._new_error("Couldn't parse: " + line)
            self.parse_error = True
            return
        self.swapped = (m.group(1) == "RAW")
        self.filename = m.group(2)
        if self.filename == "/dev/stdin":
            self.filename = "-"
        self.timestamp = float(m.group(3))
        self.frequency = int(m.group(4))

        if channelize:
            fbase = self.frequency - base_freq
            self.fchan = int(fbase / channel_width)
            self.foff = fbase % channel_width
            self.freq_print = "%3d|%05d" % (self.fchan, self.foff)
        else:
            self.freq_print = "%010d" % (self.frequency)

        #        self.access_ok=(m.group(5)=="OK")
        #        self.leadout_ok=(m.group(6)=="OK")
        self.confidence = int(m.group(7))
        self.level = float(m.group(8))
        #        self.raw_length=m.group(9)
        self.bitstream_raw = (re.sub("[\[\]<> ]", "", m.group(10)))  # raw bitstring with correct symbols
        if self.swapped:
            self.bitstream_raw = symbol_reverse(self.bitstream_raw)
        self.symbols = len(self.bitstream_raw) / 2
        if m.group(11):
            self.extra_data = m.group(11)
            self._new_error("There is crap at the end in extra_data")
        # Make a "global" timestamp
        global tswarning, tsoffset, maxts
        mm = re.match("(\d\d)-(\d\d)-(20\d\d)T(\d\d)-(\d\d)-(\d\d)-[sr]1", self.filename)
        if mm:
            month, day, year, hour, minute, second = map(int, mm.groups())
            ts = datetime.datetime(year, month, day, hour, minute, second)
            ts = (ts - datetime.datetime(1970, 1, 1)).total_seconds()
            ts += float(self.timestamp) / 1000
            self.globaltime = ts
            return
        mm = re.match("i-(\d+(?:\.\d+)?)-[vbsrtl]1.([a-z])([a-z])", self.filename)
        if mm:
            b26 = (ord(mm.group(2)) - ord('a')) * 26 + ord(mm.group(3)) - ord('a')
            self.b26 = b26
            ts = float(mm.group(1)) + float(self.timestamp) / 1000 + b26 * 600
            self.globaltime = ts
            return
        mm = re.match("i-(\d+(?:\.\d+)?)-[vbsrtl]1(?:-o[+-]\d+)?$", self.filename)
        if mm:
            ts = float(mm.group(1)) + float(self.timestamp) / 1000
            self.globaltime = ts
            return
        if not tswarning:
            print("Warning: no timestamp found in filename", file=sys.stderr)
            tswarning = True
        ts = tsoffset + float(self.timestamp) / 1000
        if ts < maxts:
            tsoffset = maxts
            ts = tsoffset + float(self.timestamp) / 1000
        maxts = ts
        self.globaltime = ts

    def upgrade(self):
        if self.error: return self
        if (self.bitstream_raw.startswith(iridium_access)):
            self.uplink = 0
        elif (self.bitstream_raw.startswith(uplink_access)):
            self.uplink = 1
        else:
            if uwec:
                access = []
                map = [0, 1, 3, 2]
                # back into bpsk symbols
                for x in xrange(0, len(iridium_access) - 1, 2):
                    access.append(map[int(self.bitstream_raw[x + 0]) * 2 + int(self.bitstream_raw[x + 1])])
                # undo differential decoding
                for c in xrange(1, len(access) - 1):
                    access[c] = (access[c - 1] + access[c]) % 4

                if bitdiff(access, UW_DOWNLINK) < 4:
                    self.uplink = 0
                    self.ec_uw = bitdiff(access, UW_DOWNLINK)
                elif bitdiff(access, UW_UPLINK) < 4:
                    self.uplink = 1
                    self.ec_uw = bitdiff(access, UW_UPLINK)
                else:
                    self._new_error("Access code distance too big: %d/%d " % (
                        bitdiff(access, UW_DOWNLINK), bitdiff(access, UW_UPLINK)))
            if ("uplink" not in self.__dict__):
                self._new_error("Access code missing")
                return self
        try:
            return IridiumMessage(self).upgrade()
        except ParserError as e:
            self._new_error(str(e))
            return self

    def _new_error(self, msg):
        self.error = True
        msg = str(type(self).__name__) + ": " + msg
        if not self.error_msg or self.error_msg[-1] != msg:
            self.error_msg.append(msg)

    def _pretty_header(self):
        flags = ""
        if uwec:
            if ("ec_uw" in self.__dict__):
                flags += "-UW:%d" % self.ec_uw
            else:
                flags += "-UW:0"

        if harder:
            if ("ec_lcw" in self.__dict__):
                flags += "-LCW:%s" % self.ec_lcw
            else:
                flags += "-LCW:0"

        if not perfect:
            if ("fixederrs" in self.__dict__ and self.fixederrs > 0):
                flags += "-FIX:%d" % self.fixederrs
            else:
                flags += "-FIX:0"
        if globaltime:
            hdr = "j%s %16.6f" % (flags, self.globaltime)
        else:
            hdr = "%s %014.4f" % (self.filename, self.timestamp)
        return "%s %s %3d%% %7.3f" % (hdr, self.freq_print, self.confidence, self.level)

    def _pretty_trailer(self):
        return ""

    def pretty(self):
        if self.parse_error:
            return "ERR: "
        str = "RAW: " + self._pretty_header()
        #        str+= " "+self.bitstream_raw
        bs = self.bitstream_raw
        if (bs.startswith(iridium_access)):
            str += " <%s>" % iridium_access
            bs = bs[len(iridium_access):]
        elif (bs.startswith(uplink_access)):
            str += " <U%s>" % uplink_access
            bs = bs[len(uplink_access):]
        str += " " + " ".join(slice(bs, 16))
        if ("extra_data" in self.__dict__):
            str += " " + self.extra_data
        str += self._pretty_trailer()
        return str


class IridiumMessage(Message):
    def __init__(self, msg):
        self.__dict__ = msg.__dict__
        if (self.uplink):
            data = self.bitstream_raw[len(uplink_access):]
        else:
            data = self.bitstream_raw[len(iridium_access):]

        # Try to detect packet type.
        # XXX: will not detect packets with correctable bit errors at the beginning
        if "msgtype" not in self.__dict__:
            if data[:32] == header_messaging:
                self.msgtype = "MS"

        if "msgtype" not in self.__dict__ and linefilter['type'] == "IridiumMSMessage":
            self._new_error("filtered message")
            return

        if "msgtype" not in self.__dict__:
            if data[:96] == header_time_location:
                self.msgtype = "TL"

        if "msgtype" not in self.__dict__ and linefilter['type'] == "IridiumSTLMessage":
            self._new_error("filtered message")
            return

        if "msgtype" not in self.__dict__:
            hdrlen = 6
            blocklen = 64
            if len(data) > hdrlen + blocklen:
                if ndivide(hdr_poly, data[:hdrlen]) == 0:
                    (o_bc1, o_bc2) = de_interleave(data[hdrlen:hdrlen + blocklen])
                    if ndivide(ringalert_bch_poly, o_bc1[:31]) == 0:
                        if ndivide(ringalert_bch_poly, o_bc2[:31]) == 0:
                            self.msgtype = "BC"

        if "msgtype" not in self.__dict__ and linefilter['type'] == "IridiumBCMessage":
            self._new_error("filtered message")
            return

        if "msgtype" not in self.__dict__:
            if len(data) > 64:  # XXX: heuristic based on LCW / first BCH block, can we do better?
                (o_lcw1, o_lcw2, o_lcw3) = de_interleave_lcw(data[:46])
                if ndivide(29, o_lcw1) == 0:
                    if ndivide(41, o_lcw3) == 0:
                        (e2, lcw2, bch) = bch_repair(465, o_lcw2 + '0')  # One bit missing, so we guess
                        if (e2 == 1):  # Maybe the other one...
                            (e2, lcw2, bch) = bch_repair(465, o_lcw2 + '1')
                        if e2 == 0:
                            self.msgtype = "LW"

        if "msgtype" not in self.__dict__ and linefilter['type'] == "IridiumLCWMessage":
            self._new_error("filtered message")
            return

        if "msgtype" not in self.__dict__:
            firstlen = 3 * 32
            if len(data) >= 3 * 32:
                (o_ra1, o_ra2, o_ra3) = de_interleave3(data[:firstlen])
                if ndivide(ringalert_bch_poly, o_ra1[:31]) == 0:
                    if ndivide(ringalert_bch_poly, o_ra2[:31]) == 0:
                        if ndivide(ringalert_bch_poly, o_ra3[:31]) == 0:
                            self.msgtype = "RA"

        if "msgtype" not in self.__dict__ and linefilter['type'] == "IridiumRAMessage":
            self._new_error("filtered message")
            return

        if "msgtype" not in self.__dict__:
            if harder:
                # try IBC
                if len(data) >= 70:
                    hdrlen = 6
                    blocklen = 64
                    (e1, _, _) = bch_repair(hdr_poly, data[:hdrlen])
                    (o_bc1, o_bc2) = de_interleave(data[hdrlen:hdrlen + blocklen])
                    (e2, d2, b2) = bch_repair(ringalert_bch_poly, o_bc1[:31])
                    (e3, d3, b3) = bch_repair(ringalert_bch_poly, o_bc2[:31])
                    if e1 >= 0 and e2 >= 0 and e3 >= 0:
                        if ((d2 + b2 + o_bc1[31]).count('1') % 2) == 0:
                            if ((d3 + b3 + o_bc2[31]).count('1') % 2) == 0:
                                self.msgtype = "BC"

                # try for LCW
                if len(data) >= 64:
                    (o_lcw1, o_lcw2, o_lcw3) = de_interleave_lcw(data[:46])
                    (e1, lcw1, bch) = bch_repair(29, o_lcw1)  # BCH(7,3)
                    (e2a, lcw2, bch) = bch_repair(465, o_lcw2 + '0')  # BCH(13,16)
                    (e2b, lcw2, bch) = bch_repair(465, o_lcw2 + '1')
                    (e3, lcw3, bch) = bch_repair(41, o_lcw3)  # BCH(26,21)

                    e2 = e2a
                    if (e2b >= 0 and e2b < e2a) or (e2a < 0):
                        e2 = e2b

                    if e1 >= 0 and e2 >= 0 and e3 >= 0:
                        self.msgtype = "LW"
                        self.ec_lcw = (e1 + e2 + e3)

        if "msgtype" not in self.__dict__:
            if len(data) < 64:
                raise ParserError("Iridium message too short")

        if forcetype:
            self.msgtype = forcetype.partition(':')[0]

        if "msgtype" not in self.__dict__:
            raise ParserError("unknown Iridium message type")

        if self.msgtype == "MS":
            hdrlen = 32
            self.header = data[:hdrlen]
            self.descrambled = []
            (blocks, self.descramble_extra) = slice_extra(data[hdrlen:], 64)
            for x in blocks:
                self.descrambled += de_interleave(x)
        elif self.msgtype == "TL":
            hdrlen = 96
            self.header = data[:hdrlen]
            self.descrambled = data[hdrlen:hdrlen + (256 * 3)]
            self.descramble_extra = data[hdrlen + (256 * 3):]
        elif self.msgtype == "RA":
            firstlen = 3 * 32
            if len(data) < firstlen:
                self._new_error("No data to descramble")
            self.header = ""
            self.descrambled = de_interleave3(data[:firstlen])
            (blocks, self.descramble_extra) = slice_extra(data[firstlen:], 64)
            for x in blocks:
                self.descrambled += de_interleave(x)
        elif self.msgtype == "BC":
            hdrlen = 6
            self.header = data[:hdrlen]
            (e, d, bch) = bch_repair(hdr_poly, self.header)

            self.bc_type = int(d, 2)
            if e == 0:
                self.header = "bc:%d" % self.bc_type
            else:
                self.header = "%s/E%d" % (self.header, e)
                self._new_error("IBC header error")
            self.descrambled = []

            (blocks, self.descramble_extra) = slice_extra(data[hdrlen:], 64)
            for x in blocks:
                self.descrambled += de_interleave(x)
        elif self.msgtype == "LW":
            lcwlen = 46
            (o_lcw1, o_lcw2, o_lcw3) = de_interleave_lcw(data[:lcwlen])
            (e1, self.lcw1, bch) = bch_repair(29, o_lcw1)
            (e2a, lcw2a, bch) = bch_repair(465, o_lcw2 + '0')  # One bit error expected
            (e2b, lcw2b, bch) = bch_repair(465, o_lcw2 + '1')  # Other bit flip?
            if e2b < 0:
                e2 = e2a
                self.lcw2 = lcw2a
            elif e2a < 0:
                e2 = e2b
                self.lcw2 = lcw2b
            elif e2a < e2b:
                e2 = e2a
                self.lcw2 = lcw2a
            else:
                e2 = e2b
                self.lcw2 = lcw2b
            (e3, self.lcw3, bch) = bch_repair(41, o_lcw3)
            self.ft = int(self.lcw1, 2)  # Frame type
            if forcetype and ':' in forcetype:
                self.ft = int(forcetype.partition(':')[2])
            if e1 < 0 or e2 < 0 or e3 < 0:
                self._new_error("LCW decode failed")
                self.header = "LCW(%s %s/%02d E%d,%s %sx/%03d E%d,%s %s/%02d E%d)" % (
                    o_lcw1[:3], o_lcw1[3:], ndivide(29, o_lcw1), e1, o_lcw2[:6], o_lcw2[6:], ndivide(465, o_lcw2 + '0'),
                    e2,
                    o_lcw3[:21], o_lcw3[21:], ndivide(41, o_lcw3), e3)
            else:
                # LCW:=xx[type] yyyy[code]
                # 0: maint
                #    6: geoloc
                #    f: "no text"
                #    c: maint [lqi[x1c,2], power[[x19,3]]
                #    789abde: reserved
                #    0: sync [status[xa,1], dtoa[xc,10], dfoa[x16,8]]
                #    3: maint [lqi[xa,2], power[xc,3], fine dtoa[xf,7], fine dfoa[x16,3]]
                #    245: reserved
                #    1: switch [dtoa[xc,10], dfoa[x16,8]]
                # 1: acchl
                #    1: acchl
                #    *: reserved
                # 2: handoff
                #    c: handoff cand.
                #    f: "no text"
                #    3: handoff resp. [cand[%c[0=P,1=S::0xb,1]], denied[0xc,1], ref[xd,1], slot[xf,2]+1, subband up[x11,5], subband down[x16,5], access[x1b,3]+1]
                #    *: reserved
                # 3: reserved
                #                self.header="LCW(%d,%s,%s E%d)"%(self.ft,self.lcw2,self.lcw3,e1+e2+e3)
                self.lcw_ft = int(self.lcw2[:2], 2)
                self.lcw_code = int(self.lcw2[2:], 2)
                lcw3bits = self.lcw3
                if self.lcw_ft == 0:
                    ty = "maint"
                    if self.lcw_code == 6:
                        code = "geoloc"
                    elif self.lcw_code == 15:
                        code = "<silent>"
                    elif self.lcw_code == 12:
                        code = "maint[1]"
                        code += "[lqi:%d,power:%d]" % (int(self.lcw3[19:21], 2), int(self.lcw3[16:19], 2))
                        lcw3bits = "%s" % (self.lcw3[:16])
                    elif self.lcw_code == 0:
                        code = "sync"
                        code += "[status:%d,dtoa:%d,dfoa:%d]" % (
                            int(self.lcw3[1:2], 2), int(self.lcw3[3:13], 2), int(self.lcw3[13:21], 2))
                        lcw3bits = "%s|%s" % (self.lcw3[0], self.lcw3[2])
                    elif self.lcw_code == 3:
                        code = "maint[2]"
                        code += "[lqi:%d,power:%d,f_dtoa:%d,f_dfoa:%d]" % (
                            int(self.lcw3[1:3], 2), int(self.lcw3[3:6], 2), int(self.lcw3[6:13], 2),
                            int(self.lcw3[13:20], 2))
                        lcw3bits = "%s|%s" % (self.lcw3[0], self.lcw3[20:])
                    elif self.lcw_code == 1:
                        code = "switch"
                        code += "[dtoa:%d,dfoa:%d]" % (int(self.lcw3[3:13], 2), int(self.lcw3[13:21], 2))
                        lcw3bits = "%s" % (self.lcw3[0:3])
                    else:
                        code = "rsrvd(%d)" % (self.lcw_code)
                elif self.lcw_ft == 1:
                    ty = "acchl"
                    if self.lcw_code == 1:
                        code = "acchl"
                    else:
                        code = "rsrvd(%d)" % (self.lcw_code)
                elif self.lcw_ft == 2:
                    ty = "hndof"
                    if self.lcw_code == 12:
                        code = "handoff_cand"
                        lcw3bits = "%03x,%03x,%s" % (int(self.lcw3[:11], 2), int(self.lcw3[11:], 2), lcw3bits)
                    elif self.lcw_code == 3:
                        code = "handoff_resp"
                        code += "[cand:%s,denied:%d,ref:%d,slot:%d,sband_up:%d,sband_dn:%d,access:%d]" % (
                            ['P', 'S'][int(self.lcw3[2:3], 2)], int(self.lcw3[3:4], 2), int(self.lcw3[4:5], 2),
                            1 + int(self.lcw3[6:8], 2), int(self.lcw3[8:13], 2), int(self.lcw3[13:18], 2),
                            1 + int(self.lcw3[18:21], 2))
                        lcw3bits = "%s" % (self.lcw3[0:2])
                    elif self.lcw_code == 15:
                        code = "<silent>"
                    else:
                        code = "rsrvd(%d)" % (self.lcw_code)
                elif self.lcw_ft == 3:
                    ty = "rsrvd"
                    code = "<%d>" % (self.lcw_code)
                self.header = "LCW(%d,T:%s,C:%s,%s E%d)" % (self.ft, ty, code, lcw3bits, e1 + e2 + e3)
                self.header = "%-110s " % self.header
            self.descrambled = []
            self.payload_r = []
            self.payload_f = []
            data = data[lcwlen:]

            if self.ft <= 3 and len(data) < 312:
                self._new_error("Not enough data in data packet")
            if self.ft == 0:  # Voice - Mission data - voice
                self.msgtype = "VO"
                for x in slice(data[:312], 8):
                    self.descrambled += [x]
                    self.payload_f += [int(x, 2)]
                    self.payload_r += [int(x[::-1], 2)]
                self.payload_6 = [int(x, 2) for x in slice(data[:312], 6)]
                self.descramble_extra = data[312:]
            elif self.ft == 1:  # IP via PPP - Mission data - data
                self.msgtype = "IP"
                for x in slice(data[:312], 8):
                    self.descrambled += [x[::-1]]
                    self.payload_f += [int(x, 2)]
                    self.payload_r += [int(x[::-1], 2)]
                self.descramble_extra = data[312:]
            elif self.ft == 2:  # DAta (SBD) - Mission control data - ISU/SV
                self.msgtype = "DA"
                self.descramble_extra = data[124 * 2 + 64:]
                data = data[:124 * 2 + 64]
                blocks = slice(data, 124)
                end = blocks.pop()
                for x in blocks:
                    (b1, b2) = de_interleave(x)
                    (b1, b2, b3, b4) = slice(b1 + b2, 31)
                    self.descrambled += [b4, b2, b3, b1]
                (b1, b2) = de_interleave(end)
                self.descrambled += [b2[1:], b1[1:]]  # Throw away the extra bit
            elif self.ft == 7:  # Synchronisation
                self.msgtype = "SY"
                self.descrambled = data[:312]
                self.sync = [int(x, 2) for x in slice(self.descrambled, 8)]
                self.descramble_extra = data[312:]
            elif self.ft == 3:  # Mission control data - inband sig
                self.msgtype = "U3"
                self.descrambled = data[:312]
                self.payload6 = [int(x, 2) for x in slice(self.descrambled, 6)]
                self.payload8 = [int(x, 2) for x in slice(self.descrambled, 8)]
                self.descramble_extra = data[312:]
            elif self.ft == 6:  # "PT=,"
                self.msgtype = "U6"
                self.descrambled = data[:312]
                self.descramble_extra = data[312:]
            else:  # Need to check what other ft are
                self.msgtype = "U%d" % self.ft
                self.descrambled = data[:312]
                self.descramble_extra = data[312:]
        else:
            raise Exception("Illegal Iridium frame type")

        self.lead_out_ok = self.descramble_extra.startswith(iridium_lead_out)
        if self.msgtype != "VO" and self.msgtype != "IP" and len(self.descrambled) == 0:
            self._new_error("No data to descramble")

    def upgrade(self):
        if self.error: return self
        try:
            if self.msgtype == "VO":
                return IridiumVOMessage(self).upgrade()
            elif self.msgtype == "IP":
                return IridiumIPMessage(self).upgrade()
            elif self.msgtype == "SY":
                return IridiumSYMessage(self).upgrade()
            elif self.msgtype == "U3":
                return IridiumLCW3Message(self).upgrade()
            elif self.msgtype == "TL":
                return IridiumSTLMessage(self).upgrade()
            elif self.msgtype.startswith("U"):
                return self  # XXX: probably need to descramble/BCH it
            return IridiumECCMessage(self).upgrade()
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        str = super(IridiumMessage, self)._pretty_header()
        str += " %03d" % (self.symbols - len(iridium_access) / 2)
        #        str+=" L:"+("no","OK")[self.lead_out_ok]
        if (self.uplink):
            str += " UL"
        else:
            str += " DL"
        if self.header:
            str += " " + self.header
        return str

    def _pretty_trailer(self):
        str = super(IridiumMessage, self)._pretty_trailer()
        if ("descramble_extra" in self.__dict__) and self.descramble_extra != "":
            str += " descr_extra:" + re.sub(iridium_lead_out, "[" + iridium_lead_out + "]", self.descramble_extra)
        return str

    def pretty(self):
        sstr = "IRI: " + self._pretty_header()
        sstr += " %2s" % self.msgtype
        if self.descrambled != "":
            sstr += " ["
            sstr += ".".join(["%02x" % int("0" + x, 2) for x in slice("".join(self.descrambled), 8)])
            sstr += "]"
        sstr += self._pretty_trailer()
        return sstr


class IridiumSYMessage(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__

    def upgrade(self):
        return self

    def _pretty_header(self):
        return super(IridiumSYMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumSYMessage, self)._pretty_trailer()

    def pretty(self):
        str = "ISY: " + self._pretty_header()
        errs = 0
        for x in self.sync:
            if x != 0xaa:
                errs += 1  # Maybe count bit errors
        if errs == 0:
            str += " Sync=OK"
        else:
            str += " Sync=no, errs=%d" % errs
        str += self._pretty_trailer()
        return str


class IridiumSTLMessage(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        self.header = "<11>"

    def upgrade(self):
        return self

    def _pretty_header(self):
        return super(IridiumSTLMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumSTLMessage, self)._pretty_trailer()

    def pretty(self):
        str = "ITL: " + self._pretty_header()
        str += " [" + ".".join(["%02x" % int("0" + x, 2) for x in slice("".join(self.descrambled[:256]), 8)]) + "]"
        str += " [" + ".".join(["%02x" % int("0" + x, 2) for x in slice("".join(self.descrambled[256:512]), 8)]) + "]"
        str += " [" + ".".join(["%02x" % int("0" + x, 2) for x in slice("".join(self.descrambled[512:]), 8)]) + "]"
        str += self._pretty_trailer()
        return str


class IridiumLCW3Message(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__

        self.utype = "IU3"
        self.rs8p = False
        self.rs6p = False
        self.fixederrs = 0

        (ok, msg, csum) = rs.rs_fix(self.payload8)
        self.rs8 = ok
        if ok:
            self.utype = "I38"
            if bytearray(self.payload8) == msg + csum:
                self.rs8p = True
            else:
                self.fixederrs += 1
            self.rs8m = msg
            self.rs8c = csum
            self.csum = checksum_16(self.rs8m[0:-3], self.rs8m[-2:])
            self.oddbyte = self.rs8m[-3]
        else:
            (ok, msg, csum) = rs6.rs_fix(self.payload6)
            self.rs6 = ok
            if ok:
                self.utype = "I36"
                if bytearray(self.payload6) == msg + csum:
                    self.rs6p = True
                else:
                    self.fixederrs += 1
                self.rs6m = msg
                self.rs6c = csum

    def upgrade(self):
        return self

    def _pretty_header(self):
        return super(IridiumLCW3Message, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumLCW3Message, self)._pretty_trailer()

    def pretty(self):
        str = self.utype + ": " + self._pretty_header()
        if self.utype == 'I38':
            if self.rs8p:
                str += " RS8=OK"
            else:
                str += " RS8=ok"
            if self.oddbyte != 0:
                str += " ODD:%d" % self.oddbyte
            if self.csum == 0:
                str += " CS=OK"
                self.rs8m = self.rs8m[0:-3]
                remove_zeros(self.rs8m)
            else:
                str += " CS=no"
            str += " ["
            str += " ".join(["%02x" % (x) for x in self.rs8m])
            str += "]"
        elif self.utype == 'I36':
            if self.rs6p:
                str += " RS6=OK"
            else:
                str += " RS6=ok"
            str += " {%02d}" % self.rs6m[0]
            str += " ["

            num = None
            v = "".join(["{0:06b}".format(x) for x in self.rs6m[1:]])
            if self.rs6m[0] == 0:
                str += v[:2] + "| " + group(v[2:], 10)
            elif self.rs6m[0] == 6:
                str += v[:2] + "| " + group(v[2:], 24)
                num = [int(x, 2) for x in slice(v[2:], 24)]
                remove_zeros(num)
            elif self.rs6m[0] == 32 or self.rs6m[0] == 34:
                str += v[:2] + "| " + group(v[2:], 24)
                num = [int(x, 2) for x in slice(v[2:-4], 24)]
                if int(v[-4:], 2) != 0:
                    num += [int(v[-4:], 2)]
                while len(num) > 0 and num[-1] == 0x7ffff:
                    num = num[:-1]
            else:
                str += group(v, 6)
            str += "]"

            if num is not None:
                str += " <" + " ".join(["%06x" % x for x in num]) + ">"
        else:
            str += " RS=no"
            str += " ["
            str += group(self.descrambled, 8)
            str += "]"
        str += self._pretty_trailer()
        return str


class IridiumVOMessage(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        self.crcval = iip_crc24("".join([chr(x) for x in self.payload_r]))
        if self.crcval == 0:
            self.vtype = "VDA"
            return
        else:
            (ok, msg, csum) = rs6.rs_fix(self.payload_6)
            self.rs6p = False
            self.rs6 = ok
            if ok:
                self.vtype = "VO6"
                if bytearray(self.payload_6) == msg + csum:
                    self.rs6p = True
                self.rs6m = msg
                self.rs6c = csum
                return
            #            if rs_check(self.payload_f):
            (ok, msg, rsc) = rs.rs_fix(self.payload_f)
            if ok:
                self.vtype = "VOD"
                self.vdata = msg
            else:
                self.vtype = "VOC"
                self.vdata = self.payload_f

    def upgrade(self):
        if self.vtype == "VDA":
            new = IridiumIPMessage(self).upgrade()
            new.itype = "VDA"
            return new
        return self

    def _pretty_header(self):
        return super(IridiumVOMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumVOMessage, self)._pretty_trailer()

    def pretty(self):
        str = self.vtype + ": " + self._pretty_header()
        if self.vtype == "VDA":
            raise ParserError("VDA handled in IIP(canthappen)")
        elif self.vtype == "VO6":
            v = "".join(["{0:06b}".format(x) for x in self.rs6m])
            if self.rs6p:
                str += " RS=OK"
            else:
                str += " RS=ok"
            str += " " + group(v, 6)
        else:
            str += " [" + ".".join(["%02x" % x for x in self.vdata]) + "]"
        str += self._pretty_trailer()
        return str


# Poly from GSM 04.64 / check value (reversed) is 0xC91B6
iip_crc24 = crcmod.mkCrcFun(poly=0x1BBA1B5, initCrc=0xffffff ^ 0x0c91b6, rev=True, xorOut=0x0c91b6)


class IridiumIPMessage(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__

        self.crcval = iip_crc24("".join([chr(x) for x in self.payload_r]))
        if self.crcval == 0:
            self.itype = "IIP"
            self.ip_hdr = self.payload_r[0]
            #  106099 01: ACK / IDLE
            #  458499 04: Data
            #    2476 0b:
            #     504 0f:
            #     173 14:
            #    1477 11:
            self.ip_seq = self.payload_r[1]
            self.ip_ack = self.payload_r[2]
            self.ip_cs = self.payload_r[3]
            self.ip_cs_ok = self.ip_hdr + self.ip_seq + self.ip_ack + self.ip_cs
            while (self.ip_cs_ok > 255):
                self.ip_cs_ok -= 255
            self.ip_len = self.payload_r[4]
            if self.ip_len > 31:
                # self._new_error("Invalid ip_len")
                pass
            self.ip_data = self.payload_r[5:31 + 5]  # XXX: only len bytes?
            self.ip_cksum, = struct.unpack(">L", bytearray([0] + self.payload_r[31 + 5:]))
        else:
            (ok, msg, rsc) = rs.rs_fix(self.payload_f)
            if ok:
                self.iiqcsum = checksum_16(msg[0:-3], msg[-2:])
                self.oddbyte = msg[-3]
                if self.iiqcsum == 0:
                    self.itype = "IIR"
                    self.idata = msg[0:-2]
                else:
                    self.itype = "IIQ"
                    self.idata = msg
            else:
                self.itype = "IIU"

    def upgrade(self):
        return self

    def _pretty_header(self):
        return super(IridiumIPMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumIPMessage, self)._pretty_trailer()

    def pretty(self):
        s = self.itype + ": " + self._pretty_header()
        if self.itype == "IIP" or self.itype == "VDA":
            s += " type:%02x seq=%03d ack=%03d cs=%03d/%s len=%03d" % (
                self.ip_hdr, self.ip_seq, self.ip_ack, self.ip_cs, ["no", "OK"][(self.ip_cs_ok == 255)], self.ip_len)
            s += " [" + ".".join(["%02x" % x for x in self.ip_data]) + "]"
            s += " %06x/%06x" % (self.ip_cksum, self.crcval)
            s += " FCS:OK"
            ip_data = ' IP: '
            for c in self.ip_data:
                if (c >= 32 and c < 127):
                    ip_data += chr(c)
                else:
                    ip_data += "."
            s += ip_data
        elif self.itype == "IIQ":
            s += " [" + " ".join(["%02x" % x for x in self.idata]) + "]"
            s += " C=%04x" % self.iiqcsum
            if (self.iiqcsum == self.idata[-2] * 256 + self.idata[-1]):
                s += " OK"
        elif self.itype == "IIR":
            s += " [" + " ".join(["%02x" % x for x in self.idata]) + "]"
        else:
            s += " [" + " ".join(["%s" % x for x in self.descrambled]) + "]"

        s += self._pretty_trailer()
        return s


class IridiumECCMessage(IridiumMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        if self.msgtype == "MS":
            self.poly = messaging_bch_poly
        elif self.msgtype == "RA":
            self.poly = ringalert_bch_poly
        elif self.msgtype == "BC":
            self.poly = ringalert_bch_poly
        elif self.msgtype == "DA":
            self.poly = acch_bch_poly
        else:
            raise ParserError("unknown Iridium message type(canthappen)")
        self.bitstream_messaging = ""
        self.bitstream_bch = ""
        self.oddbits = ""
        self.fixederrs = 0
        parity = None
        for block in self.descrambled:
            if len(block) == 32:  # contains parity bit
                parity = block[31:]
                block = block[:31]
            if len(block) != 31:
                raise ParserError("unknown BCH block len:%d" % len(block))

            (errs, data, bch) = bch_repair(self.poly, block)
            if (errs < 0):
                if len(self.bitstream_bch) == 0: self._new_error("BCH decode failed")
                break

            if parity:
                if ((data + bch + parity).count('1') % 2) == 1:
                    if len(self.bitstream_bch) == 0: self._new_error("Parity error")
                    break

            if errs > 0:
                self.fixederrs += 1

            self.bitstream_bch += data
            self.bitstream_messaging += data[1:]
            self.oddbits += data[0]
        if len(self.bitstream_bch) == 0:
            self._new_error("No data to descramble")

    def upgrade(self):
        if self.error: return self
        try:
            if self.msgtype == "MS":
                return IridiumMSMessage(self).upgrade()
            elif self.msgtype == "RA":
                return IridiumRAMessage(self).upgrade()
            elif self.msgtype == "BC":
                return IridiumBCMessage(self).upgrade()
            elif self.msgtype == "DA":
                return IridiumLCWMessage(self).upgrade()
            else:
                self._new_error("Unknown message type")
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        str = super(IridiumECCMessage, self)._pretty_header()
        return str

    def _pretty_trailer(self):
        return super(IridiumECCMessage, self)._pretty_trailer()

    def pretty(self):
        str = "IME: " + self._pretty_header() + " " + self.msgtype + " "
        for block in xrange(len(self.descrambled)):
            b = self.descrambled[block]
            if len(b) == 31:
                (errs, foo) = nrepair(self.poly, b)
                res = ndivide(self.poly, b)
                parity = (foo).count('1') % 2
                str += "{%s %s/%04d E%s P%d}" % (b[:21], b[21:31], res, ("0", "1", "2", "-")[errs], parity)
            elif len(b) == 32:
                (errs, foo) = nrepair(self.poly, b[:31])
                res = ndivide(self.poly, b[:31])
                parity = (foo + b[31]).count('1') % 2
                str += "{%s %s %s/%04d E%s P%d}" % (b[:21], b[21:31], b[31], res, ("0", "1", "2", "-")[errs], parity)
            else:
                str += "length=%d?" % len(b)
        str += self._pretty_trailer()
        return str


ida_crc16 = crcmod.predefined.mkPredefinedCrcFun("crc-ccitt-false")


class IridiumLCWMessage(IridiumECCMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        # Decode stuff from self.bitstream_bch
        self.flags1 = self.bitstream_bch[:4]
        self.flag1b = self.bitstream_bch[4:5]
        self.da_ctr = int(self.bitstream_bch[5:8], 2)
        self.flags2 = self.bitstream_bch[8:11]
        self.da_len = int(self.bitstream_bch[11:16], 2)
        self.flags3 = int(self.bitstream_bch[16:17], 2)
        self.zero1 = int(self.bitstream_bch[17:20], 2)
        if self.zero1 != 0:
            self._new_error("zero1 not 0")

        if len(self.bitstream_bch) < 9 * 20 + 16:
            raise ParserError("Not enough data in data packet")

        if self.da_len > 0:
            self.da_crc = int(self.bitstream_bch[9 * 20:9 * 20 + 16], 2)
            self.da_ta = [int(x, 2) for x in slice(self.bitstream_bch[20:9 * 20], 8)]
            crcstream = self.bitstream_bch[:20] + "0" * 12 + self.bitstream_bch[20:-4]
            the_crc = ida_crc16("".join([chr(int(x, 2)) for x in slice(crcstream, 8)]))
            self.the_crc = the_crc
            self.crc_ok = (the_crc == 0)
        else:
            self.crc_ok = False
            self.da_ta = [int(x, 2) for x in slice(self.bitstream_bch[20:11 * 20], 8)]

        self.zero2 = int(self.bitstream_bch[9 * 20 + 16:], 2)
        if self.zero2 != 0:
            self._new_error("zero2 not 0")

        sbd = self.bitstream_bch[1 * 20:9 * 20]
        self.data = []
        for x in slice(sbd, 8):
            self.data += [int(x, 2)]

    def upgrade(self):
        if self.error: return self
        try:
            return self
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        return super(IridiumLCWMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumLCWMessage, self)._pretty_trailer()

    def pretty(self):
        str = "IDA: " + self._pretty_header()
        str += " " + self.bitstream_bch[:3]
        str += " cont=" + self.bitstream_bch[3:4]
        str += " " + self.bitstream_bch[4:5]
        str += " ctr=" + self.bitstream_bch[5:8]
        str += " " + self.bitstream_bch[8:11]
        str += " len=%02d" % self.da_len
        str += " 0:" + self.bitstream_bch[16:20]
        str += " ["
        if self.da_len > 0:
            if all([x == 0 for x in self.da_ta[self.da_len + 1:]]):
                mstr = ".".join(["%02x" % x for x in self.da_ta[:self.da_len]])
            else:  # rest is not zero as it should be
                mstr = ".".join(["%02x" % x for x in self.da_ta])
                if self.da_len > 0 and self.da_len < 20:
                    mstr = mstr[:3 * self.da_len - 1] + '!' + mstr[3 * self.da_len:]
        else:
            mstr = ".".join(["%02x" % x for x in self.da_ta])
        str += "%-60s" % (mstr + "]")

        if self.da_len > 0:
            str += " %04x" % int(self.bitstream_bch[9 * 20:9 * 20 + 16], 2)
            str += "/%04x" % self.the_crc
            if self.crc_ok:
                str += " CRC:OK"
            else:
                str += " CRC:no"
            str += " " + self.bitstream_bch[9 * 20 + 16:]
        else:
            str += "  ---   "
            str += " " + self.bitstream_bch[9 * 20 + 16:]

        if self.da_len > 0:
            sbd = self.bitstream_bch[1 * 20:9 * 20]

            str += ' SBD: '
            for x in slice(sbd, 8):
                c = int(x, 2)
                if (c >= 32 and c < 127):
                    str += chr(c)
                else:
                    str += "."

        str += self._pretty_trailer()
        return str


class IridiumBCMessage(IridiumECCMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        blocks, _ = slice_extra(self.bitstream_bch, 42)

        self.readable = ''
        self.trailer = ''

        if len(blocks) > 4:  # IBC is exactly 4 "blocks" long
            blocks = blocks[:4]
            self.trailer = ' {LONG}'
        elif len(blocks) < 4:
            self.trailer = ' {SHORT}'

        if blocks and self.bc_type == 0:
            data = blocks.pop(0)

            self.sv_id = int(data[0:7], 2)
            self.beam_id = int(data[7:13], 2)
            self.unknown01 = data[13:14]
            self.slot = int(data[14:15], 2)  # previously: timeslot
            self.sv_blocking = int(data[15:16], 2)  # aka: Acq
            self.acqu_classes = data[16:32]
            self.acqu_subband = int(data[32:37], 2)
            self.acqu_channels = int(data[37:40], 2)
            self.unknown02 = data[40:42]

            self.readable += 'sat:%03d cell:%02d %s slot:%d sv_blkn:%d aq_cl:%s aq_sb:%02d aq_ch:%d %s' % (
                self.sv_id, self.beam_id, self.unknown01, self.slot, self.sv_blocking, self.acqu_classes,
                self.acqu_subband,
                self.acqu_channels, self.unknown02)

        if blocks and self.bc_type == 0:
            data = blocks.pop(0)

            self.type = int(data[0:6], 2)
            if self.type == 0:
                self.unknown11 = data[6:36]
                self.max_uplink_pwr = int(data[36:42], 2)
                self.readable += ' %s max_uplink_pwr:%02d' % (self.unknown11, self.max_uplink_pwr)
            elif self.type == 1:
                self.unknown21 = data[6:10]
                self.iri_time = int(data[10:42], 2)
                (self.iri_time_ux, self.iri_time_str) = fmt_iritime(self.iri_time)
                self.iri_time_diff = self.iri_time_ux - self.globaltime
                self.readable += ' %s time:%s' % (self.unknown21, self.iri_time_str)
            elif self.type == 2:
                self.unknown31 = data[6:10]
                self.tmsi_expiry = int(data[10:43], 2)
                (self.tmsi_expiry_ux, tmsi_expiry_str) = fmt_iritime(self.tmsi_expiry)
                self.readable += ' %s tmsi_expiry:%s' % (self.unknown31, tmsi_expiry_str)
            elif self.type == 4:
                if data != "000100000000100001110000110000110011110000":
                    self.readable += ' type:%02d %s' % (self.type, data)
            else:  # Unknown Type
                self.readable += ' type:%02d %s' % (self.type, data)
        #                raise ParserError("unknown BC Type %s"%self.type)

        for data in blocks:  # Parse assignments (if any)
            if (data != '111000000000000000000000000000000000000000'):
                # Channel Assignment
                unknown1 = data[0:3]
                random_id = int(data[3:11], 2)
                timeslot = 1 + int(data[11:13], 2)
                uplink_subband = int(data[13:18], 2)
                downlink_subband = int(data[18:23], 2)
                access = 1 + int(data[23:26], 2)
                dtoa = int(data[26:34], 2)
                dfoa = int(data[34:40], 2)
                unknown4 = data[40:42]
                self.readable += ' [%s Rid:%03d ts:%d ul_sb:%02d dl_sb:%02d access:%d dtoa:%03d dfoa:%02d %s]' % (
                    unknown1, random_id, timeslot, uplink_subband, downlink_subband, access, dtoa, dfoa, unknown4)
            else:
                self.readable += ' []'

    def upgrade(self):
        if self.error: return self
        try:
            return self
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        return super(IridiumBCMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return self.trailer + super(IridiumBCMessage, self)._pretty_trailer()

    def pretty(self):
        str = "IBC: " + self._pretty_header() + ' ' + self.readable
        str += self._pretty_trailer()
        return str


class IridiumRAMessage(IridiumECCMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        # Decode stuff from self.bitstream_bch
        # 3 blocks (63 bits) fixed "header".
        if len(self.bitstream_bch) < 63:
            raise ParserError("RA content too short")
        self.ra_sat = int(self.bitstream_bch[0:7], 2)  # sv_id
        self.ra_cell = int(self.bitstream_bch[7:13], 2)  # beam_id
        self.ra_pos_x = int(self.bitstream_bch[14:25], 2) - int(self.bitstream_bch[13]) * (1 << 11)
        self.ra_pos_y = int(self.bitstream_bch[26:37], 2) - int(self.bitstream_bch[25]) * (1 << 11)
        self.ra_pos_z = int(self.bitstream_bch[38:49], 2) - int(self.bitstream_bch[37]) * (1 << 11)
        self.ra_int = int(self.bitstream_bch[49:56], 2)  # 90ms interval of RA (within same sat/cell)
        self.ra_ts = int(self.bitstream_bch[56:57], 2)  # Broadcast slot 1 or 4
        self.ra_eip = int(self.bitstream_bch[57:58], 2)  # EPI ?
        self.ra_bc_sb = int(self.bitstream_bch[58:63], 2)  # BCH downlink sub-band

        self.ra_lat = atan2(self.ra_pos_z, sqrt(self.ra_pos_x ** 2 + self.ra_pos_y ** 2)) * 180 / pi
        self.ra_lon = atan2(self.ra_pos_y, self.ra_pos_x) * 180 / pi
        self.ra_alt = sqrt(self.ra_pos_x ** 2 + self.ra_pos_y ** 2 + self.ra_pos_z ** 2) * 4
        self.ra_msg = False
        ra_msg = self.bitstream_bch[63:]

        # Up to 12 PAGEs(@ 42 bits) for max 432 symbol frame
        # PAGEs end with an all-1 page.
        # Frame may be longer, in that case it is padded with "FILL" pattern
        # (not necessarily to an exact PAGE size)
        self.paging = []
        page_end = None
        page_sane = True
        while len(ra_msg) >= 42:
            paging = {
                'tmsi': int(ra_msg[0:32], 2),
                'zero1': int(ra_msg[32:34], 2),
                'msc_id': int(ra_msg[34:39], 2),
                'zero2': int(ra_msg[39:42], 2),
                'raw': ra_msg[:42],
            }
            if ra_msg[:42] == "111111111111111111111111111111111111111111":
                paging['str'] = "NONE"
                if page_end is None:
                    page_end = len(self.paging)
                else:
                    page_sane = False
            elif ra_msg[:42] == "101000100111001110111010101000100010111000":
                paging['str'] = "FILL"
                if page_end is None:
                    page_sane = False
            else:
                paging['str'] = "tmsi:%08x" % paging['tmsi']
                if paging['zero1'] != 0: paging['str'] += " 0:%d" % paging['zero1']
                paging['str'] += " msc_id:%02d" % paging['msc_id']
                if paging['zero2'] != 0: paging['str'] += " 0:%d" % paging['zero2']
                if page_end is not None:
                    page_sane = False
            self.paging.append(paging)
            ra_msg = ra_msg[42:]

        if page_sane == False:
            self.page_len = None
            self.ra_extra = ra_msg
        else:
            self.page_len = page_end
            self.ra_extra = ""
            self.descramble_extra = ""

    def upgrade(self):
        if self.error: return self
        try:
            return self
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        return super(IridiumRAMessage, self)._pretty_header()

    def _pretty_trailer(self):
        return super(IridiumRAMessage, self)._pretty_trailer()

    def pretty(self):
        str = "IRA: " + self._pretty_header()
        str += " sat:%03d" % self.ra_sat
        str += " beam:%02d" % self.ra_cell
        #        str+= " aps=(%04d,%04d,%04d)"%(self.ra_pos_x,self.ra_pos_y,self.ra_pos_z)
        str += " pos=(%+06.2f/%+07.2f)" % (self.ra_lat, self.ra_lon)
        str += " alt=%03d" % (self.ra_alt - 6378 + 23)  # Maybe try WGS84 geoid? :-)
        str += " RAI:%02d" % self.ra_int
        str += " ?%d%d" % (self.ra_ts, self.ra_eip)
        str += " bc_sb:%02d" % self.ra_bc_sb

        if self.page_len is None:
            for p in self.paging:
                str += " PAGE(%s)" % p['str']
            if "NONE" in [x['str'] for x in self.paging]:
                str += " CORRUPT"
            else:
                str += " INCOMPLETE"
        else:
            for p in self.paging[:self.page_len]:
                str += " PAGE(%s)" % p['str']
            if len(self.paging) - self.page_len > 1:
                str += " FILL=%d" % (len(self.paging) - self.page_len - 1)

        if self.ra_extra:
            str += " +%s" % " ".join(slice(self.ra_extra, 21))

        str += self._pretty_trailer()
        return str


class IridiumMSMessage(IridiumECCMessage):
    def __init__(self, imsg):
        self.__dict__ = imsg.__dict__
        rest = self.bitstream_messaging

        if len(rest) < 32:
            raise ParserError("Not enough data received")

        self.zero1 = rest[0:4]
        if self.zero1 != '0000':
            self._new_error("zero1 not 0000")

        self.block = int(rest[4:4 + 4], 2)  # Block number in the super frame
        self.frame = int(rest[8:8 + 6], 2)  # Current frame number (OR: Current cell number)
        self.bch_blocks = int(rest[14:18], 2)  # Number of BCH blocks in this message
        self.unknown1 = rest[18]  # ?
        self.secondary = int(rest[19])  # Something like secondary SV
        self.ctr1 = int(rest[19] + self.oddbits[1] + rest[20:32], 2)

        if (self.oddbits[0] == "1"):
            self.group = "A"
            self.agroup = 0
        else:
            self.group = int(rest[18:20], 2)
            self.agroup = 1 + self.group
        self.tdiff = ((self.block * 5 + self.agroup) * 48 + self.frame) * 90

        if self.bch_blocks < 2:
            raise ParserError("Not enough BCH blocks in header")

        if len(self.bitstream_messaging) < self.bch_blocks * 40:
            self._new_error("Incorrect amount of data received.")
            self._new_error("Need %d, got %d" % (self.bch_blocks * 40, len(self.bitstream_messaging)))

        rest = self.bitstream_messaging[:self.bch_blocks * 40]
        self.oddbits = self.oddbits[:self.bch_blocks * 2]

        # If oddbits ends in 1, this is an all-1 block -- remove it
        self.msg_trailer = ""
        if (self.oddbits[-1] == "1"):
            self.msg_trailer = rest[-20:]
            if (self.msg_trailer != "1" * 20):
                self._new_error("trailer exists, but not all-1")
            rest = rest[0:-20]
            # If oddbits still ends in 1, probably also an all-1 block
            if (self.oddbits[-2] == "1"):
                self.msg_trailer = rest[-20:] + self.msg_trailer
                if (self.msg_trailer != "1" * 40):
                    self._new_error("second trailer exists, but not all-1")
                rest = rest[0:-20]
        # If oddbits starts with 1, there is a 80-bit "pre" message
        if self.oddbits[0] == "1":
            self.msg_pre = rest[20:100]
            rest = rest[100:]
        else:
            self.msg_pre = ""
            rest = rest[20:]
        # If enough  bits are left, there will be a pager message
        if len(rest) > 20:
            self.msg_ric = int(rest[0:22][::-1], 2)
            self.msg_format = int(rest[22:27], 2)
            self.msg_data = rest[27:]

    def upgrade(self):
        if self.error: return self
        try:
            if ("msg_format" in self.__dict__):
                if (self.msg_format == 5):
                    return IridiumMessagingAscii(self).upgrade()
                elif (self.msg_format == 3):
                    return IridiumMessagingUnknown(self).upgrade()
                else:
                    self._new_error("unknown msg_format")
        except ParserError as e:
            self._new_error(str(e))
            return self
        return self

    def _pretty_header(self):
        str = super(IridiumMSMessage, self)._pretty_header()
        str += " odd:%-26s" % (self.oddbits)
        str += " %1d:%s:%02d" % (self.block, self.group, self.frame)
        if (self.oddbits == "1011"):
            str += " %s sec:%d %-83s" % (self.unknown1, self.secondary, group(self.msg_pre, 20))
        elif (self.group == "A"):
            str += " %s c=%05d           %s %-62s" % (
                self.unknown1, self.ctr1, self.msg_pre[12:20], group(self.msg_pre[20:], 20))
        else:
            str += "         %-83s" % (group(self.msg_pre, 20))
        if ("msg_format" in self.__dict__):
            str += " ric:%07d fmt:%02d" % (self.msg_ric, self.msg_format)
        return str

    def _pretty_trailer(self):
        return super(IridiumMSMessage, self)._pretty_trailer()

    def pretty(self):
        str = "IMS: " + self._pretty_header()
        if ("msg_format" in self.__dict__):
            str += " " + group(self.msg_data, 20)
        str += self._pretty_trailer()
        return str


class IridiumMessagingAscii(IridiumMSMessage):
    def __init__(self, immsg):
        self.__dict__ = immsg.__dict__
        rest = self.msg_data
        self.msg_seq = int(rest[0:6], 2)  # 0-61 (62/63 seem unused)
        self.msg_zero1 = int(rest[6:10], 2)
        if (self.msg_zero1 != 0):
            self._new_error("zero1 is not all-zero")
        self.msg_unknown1 = rest[10:20]
        self.msg_len_bit = rest[20]
        rest = rest[21:]
        if (self.msg_len_bit == "1"):
            lfl = int(rest[0:4], 2)
            self.msg_len_field_len = lfl
            if (lfl == 0):
                raise ParserError("len_field_len unexpectedly 0")
            self.msg_ctr = int(rest[4:4 + lfl], 2)
            self.msg_ctr_max = int(rest[4 + lfl:4 + lfl * 2], 2)
            rest = rest[4 + lfl * 2:]
            if (lfl < 1 or lfl > 2):
                self._new_error("len_field_len not 1 or 2")
        else:
            self.msg_len = 0
            self.msg_ctr = 0
            self.msg_ctr_max = 0
        self.msg_zero2 = rest[0]
        if (self.msg_zero2 != "0"):
            self._new_error("zero2 is not zero")
        self.msg_checksum = int(rest[1:8], 2)
        self.msg_msgdata = rest[8:]
        m = re.compile('(\d{7})').findall(self.msg_msgdata)
        self.msg_ascii = ""
        end = 0
        for (group) in m:
            character = int(group, 2)
            if (character == 3):
                end = 1
            elif (end == 1):
                self._new_error("ETX inside ascii")
            if (character < 32 or character == 127):
                self.msg_ascii += "[%d]" % character
            else:
                self.msg_ascii += chr(character)
        if len(self.msg_msgdata) % 7:
            self.msg_rest = self.msg_msgdata[-(len(self.msg_msgdata) % 7):]
        else:
            self.msg_rest = ""
        # TODO: maybe checksum checks

    def upgrade(self):
        if self.error: return self
        return self

    def _pretty_header(self):
        str = super(IridiumMessagingAscii, self)._pretty_header()
        str += " seq:%02d %10s %1d/%1d" % (self.msg_seq, self.msg_unknown1, self.msg_ctr, self.msg_ctr_max)
        (full, rest) = slice_extra(self.msg_msgdata, 8)
        msgx = "".join(["%02x" % int(x, 2) for x in full])
        return str + " csum:%02x msg:%s.%s" % (self.msg_checksum, msgx, rest)

    def _pretty_trailer(self):
        return super(IridiumMessagingAscii, self)._pretty_trailer()

    def pretty(self):
        str = "MSG: " + self._pretty_header()
        str += " TXT: %-65s" % self.msg_ascii + " +%-6s" % self.msg_rest
        str += self._pretty_trailer()
        return str


class IridiumMessagingUnknown(IridiumMSMessage):
    def __init__(self, immsg):
        self.__dict__ = immsg.__dict__
        rest = self.msg_data
        self.msg_seq = int(rest[0:6], 2)
        self.msg_zero1 = int(rest[6:10], 2)
        if (self.msg_zero1 != 0):
            self._new_error("zero1 is not all-zero")
        self.msg_unknown1 = rest[10:20]
        rest = rest[20:]
        self.msg_unknown2 = rest[:1]
        self.msg_msgdata = rest[1:]

    def upgrade(self):
        if self.error: return self
        return self

    def _pretty_header(self):
        str = super(IridiumMessagingUnknown, self)._pretty_header()
        return str + " seq:%02d %10s %s" % (self.msg_seq, self.msg_unknown1, self.msg_unknown2)

    def _pretty_trailer(self):
        return super(IridiumMessagingUnknown, self)._pretty_trailer()

    def pretty(self):
        str = "MS3: " + self._pretty_header()
        str += " %-65s" % group(self.msg_msgdata, 4)
        str += self._pretty_trailer()
        return str


def grouped(iterable, n):
    "s -> (s0,s1,s2,...sn-1), (sn,sn+1,sn+2,...s2n-1), ..."
    return izip(*[iter(iterable)] * n)


def symbol_reverse(bits):
    r = ''
    for x in xrange(0, len(bits) - 1, 2):
        r += bits[x + 1] + bits[x + 0]
    return r


def de_interleave(group):
    #    symbols = [''.join(symbol) for symbol in grouped(group, 2)]
    symbols = [group[z + 1] + group[z] for z in xrange(0, len(group), 2)]
    even = ''.join([symbols[x] for x in range(len(symbols) - 2, -1, -2)])
    odd = ''.join([symbols[x] for x in range(len(symbols) - 1, -1, -2)])
    return (odd, even)


def de_interleave3(group):
    #    symbols = [''.join(symbol) for symbol in grouped(group, 2)]
    symbols = [group[z + 1] + group[z] for z in xrange(0, len(group), 2)]
    third = ''.join([symbols[x] for x in range(len(symbols) - 3, -1, -3)])
    second = ''.join([symbols[x] for x in range(len(symbols) - 2, -1, -3)])
    first = ''.join([symbols[x] for x in range(len(symbols) - 1, -1, -3)])
    return (first, second, third)


def de_interleave_lcw(bits):
    tbl = [40, 39, 36, 35, 32, 31, 28, 27, 24, 23, 20, 19, 16, 15, 12, 11, 8, 7, 4, 3,
           41,
           38, 37, 34, 33, 30, 29, 26, 25, 22, 21, 18, 17, 14, 13, 10, 9, 6, 5, 2, 1, 46, 45, 44, 43,
           42]
    lcw = [bits[x - 1:x] for x in tbl]
    return (''.join(lcw[:7]), ''.join(lcw[7:20]), ''.join(lcw[20:]))


def messagechecksum(msg):
    csum = 0
    for x in re.findall(".", msg):
        csum = (csum + ord(x)) % 128
    return (~csum) % 128


def checksum_16(msg, csum):
    csum = sum(struct.unpack("15H", msg + csum))
    csum = ((csum & 0xffff) + (csum >> 16))
    return csum ^ 0xffff


def remove_zeros(l):
    for ele in reversed(l):
        if not ele:
            del l[-1]
        else:
            break


def group(string, n):  # similar to grouped, but keeps rest at the end
    string = re.sub('(.{%d})' % n, '\\1 ', string)
    return string.rstrip()


def slice_extra(string, n):
    blocks = [string[x:x + n] for x in xrange(0, len(string) + 1, n)]
    extra = blocks.pop()
    return (blocks, extra)


def slice(string, n):
    return [string[x:x + n] for x in xrange(0, len(string), n)]


if output == "dump":
    file = open(dumpfile, "wb")

if output == "plot":
    import matplotlib.pyplot as plt

    xl = []
    yl = []
    cl = []
    sl = []

selected = []


def do_input(type):
    if type == "raw":
        for line in fileinput.input(remainder):
            if good:
                q = Message(line.strip())
                if q.confidence < min_confidence:
                    continue
                perline(q.upgrade())
            else:
                perline(Message(line.strip()).upgrade())
    elif type == "dump":
        file = open(dumpfile, "rb")
        try:
            while True:
                q = pickle.load(file)
                perline(q)
        except EOFError:
            pass
    else:
        print("Unknown input mode.", file=sys.stderr)
        exit(1)


def perline(q):
    if dosatclass == True:
        sat = satclass.classify(q.frequency, q.globaltime)
        q.satno = int(sat.name)
    if interesting:
        if type(q).__name__ == "IridiumMessage" or type(q).__name__ == "IridiumECCMessage" or type(
                q).__name__ == "IridiumBCMessage" or type(q).__name__ == "Message" or type(
            q).__name__ == "IridiumSYMessage" or type(q).__name__ == "IridiumMSMessage" or q.error:
            return
        del q.bitstream_raw
        if ("bitstream_messaging" in q.__dict__): del q.bitstream_messaging
        if ("descrambled" in q.__dict__): del q.descrambled
        del q.descramble_extra
    if q.error:
        if isinstance(errorstats, collections.Mapping):
            msg = q.error_msg[0]
            if (msg in errorstats):
                errorstats[msg] += 1
            else:
                errorstats[msg] = 1
        if errorfile != None:
            print(q.line + " ERR:" + ", ".join(q.error_msg), file=errorfile)
            return
    if perfect:
        if q.error or ("fixederrs" in q.__dict__ and q.fixederrs > 0):
            return
        q.descramble_extra = ""
    if errorfree:
        if q.error:
            return
        q.descramble_extra = ""
    if linefilter['type'] != "All" and type(q).__name__ != linefilter['type']:
        return
    if linefilter['attr'] and linefilter['attr'] not in q.__dict__:
        return
    if linefilter['check'] and not eval(linefilter['check']):
        return
    if vdumpfile != None and type(q).__name__ == "IridiumVOMessage":
        if len(q.voice) != 312:
            raise Exception("illegal Voice frame length")
        for bits in slice(q.voice, 8):
            byte = int(bits[::-1], 2)
            vdumpfile.write(chr(byte))
    if output == "err":
        if (q.error):
            selected.append(q)
    elif output == "msg":
        if type(q).__name__ == "IridiumMessagingAscii" and not q.error:
            selected.append(q)
    elif output == "sat":
        if not q.error and not q.oddbits == "1011":
            selected.append(q)
    elif output == "dump":
        pickle.dump(q, file, 1)
    elif output == "plot":
        selected.append(q)
    elif output == "line":
        if (q.error):
            print(q.pretty() + " ERR:" + ", ".join(q.error_msg))
        else:
            if not ofmt:
                print(q.pretty())
            else:
                q.globaltime = "%.6f" % (q.globaltime)
                if ("iri_time_diff" in q.__dict__): q.iri_time_diff = "%.6f" % (q.iri_time_diff)
                print(" ".join([str(q.__dict__[x]) for x in ofmt]))
    elif output == "rxstats":
        print("RX", "X", q.globaltime, q.frequency, "X", "X", q.confidence, q.level, q.symbols, q.error,
              type(q).__name__)
    else:
        print("Unknown output mode.", file=sys.stderr)
        exit(1)


def bitdiff(a, b):
    return len(filter((lambda x_y: x_y[0] != x_y[1]), izip(a, b)))


do_input(input)

if output == "sat":
    print("SATs:")
    sats = []
    for m in selected:
        f = m.frequency
        t = m.globaltime
        no = -1
        for s in xrange(len(sats)):
            fdiff = (sats[s][0] - f) / (t - sats[s][1])
            if f < sats[s][0] and fdiff < 250:
                no = s
        if no > -1:
            m.fdiff = (sats[no][0] - f) / (t - sats[no][1])
            sats[no][0] = f
            sats[no][1] = t
        else:
            no = len(sats)
            sats.append([f, t])
            m.fdiff = 0
        m.satno = no
    for s in xrange(len(sats)):
        print("Sat: %03d" % s)
        for m in selected:
            if m.satno == s: print(m.pretty())

if isinstance(errorstats, collections.Mapping):
    total = 0
    for (msg, count) in sorted(errorstats.iteritems()):
        total += count
        print("%7d: %s" % (count, msg), file=sys.stderr)
    print("%7d: %s" % (total, "Total"), file=sys.stderr)

if output == "err":
    print("### ")
    print("### Error listing:")
    print("### ")
    sort = {}
    for m in selected:
        msg = m.error_msg[0]
        if (msg in sort):
            sort[msg].append(m)
        else:
            sort[msg] = [m]
    for msg in sort:
        print(msg + ":")
        for m in sort[msg]:
            print("- " + m.pretty())

if output == "msg":
    buf = {}
    ricseq = {}
    wrapmargin = 10
    for m in selected:
        # msg_seq wraps around after 61, detect it, and fix it.
        if m.msg_ric in ricseq:
            if (m.msg_seq + wrapmargin) < ricseq[m.msg_ric][1]:  # seq wrapped around
                ricseq[m.msg_ric][0] += 62
            if (m.msg_seq + wrapmargin - 62) > ricseq[m.msg_ric][1]:  # "wrapped back" (out-of-order old message)
                ricseq[m.msg_ric][0] -= 62
        else:
            ricseq[m.msg_ric] = [0, 0]
        ricseq[m.msg_ric][1] = m.msg_seq
        id = "%07d[%03d]" % (m.msg_ric, (m.msg_seq + ricseq[m.msg_ric][0]))
        ts = m.globaltime
        if id in buf:
            if buf[id].msg_checksum != m.msg_checksum:
                print("Whoa! Checksum changed? Message %s (1: @%d checksum %d/2: @%d checksum %d)" % (
                    id, buf[id].globaltime, buf[id].msg_checksum, m.globaltime, m.msg_checksum))
                # "Wrap around" to not miss the changed packet.
                ricseq[m.msg_ric][0] += 62
                id = "%07d[%03d]" % (m.msg_ric, (m.msg_seq + ricseq[m.msg_ric][0]))
                m.msgs = ['[NOTYET]'] * 3
                buf[id] = m
        else:
            m.msgs = ['[NOTYET]'] * 3
            buf[id] = m
        buf[id].msgs[m.msg_ctr] = m.msg_ascii

    for b in sorted(buf, key=lambda x: buf[x].globaltime):
        msg = "".join(buf[b].msgs[:1 + buf[b].msg_ctr_max])
        msg = re.sub("(\[3\])+$", "", msg)  # XXX: should be done differently
        csum = messagechecksum(msg)
        str = "Message %s @%s (len:%d)" % (
            b, datetime.datetime.fromtimestamp(buf[b].globaltime).strftime("%Y-%m-%dT%H:%M:%S"), buf[b].msg_ctr_max)
        str += " %3d" % buf[b].msg_checksum
        str += (" fail", " OK  ")[buf[b].msg_checksum == csum]
        str += ": %s" % (msg)
        print(str)


def plotsats(plt, _s, _e):
    for ts in range(int(_s), int(_e), 10):
        for v in satclass.timelist(ts):
            plt.scatter(x=v[0], y=v[1], c=int(v[2]), alpha=0.3, edgecolor="none", vmin=10, vmax=90)


if output == "plot":
    name = "%s over %s" % (plotargs[1], plotargs[0])
    if len(plotargs) > 2:
        name += " with %s" % plotargs[2]
    filter_1 = ""
    if len(linefilter) > 0 and linefilter['type'] != "All":
        filter_1 += "type==%s" % linefilter['type']
        name = ("%s " % linefilter['type']) + name
    if linefilter['attr']:
        filter_1 += " containing %s" % linefilter['attr']
        name += " having %s" % linefilter['attr']
    if linefilter['check']:
        x = linefilter['check']
        if x.startswith("q."):
            x = x[2:]
        filter_1 += " and %s" % x
        name += " where %s" % x
    plt.suptitle(filter_1)
    plt.xlabel(plotargs[0])
    plt.ylabel(plotargs[1])
    if plotargs[0] == "time":
        plotargs[0] = "globaltime"

    for m in selected:
        xl.append(m.__dict__[plotargs[0]])
        yl.append(m.__dict__[plotargs[1]])
        if len(plotargs) > 2:
            cl.append(m.__dict__[plotargs[2]])

    if len(plotargs) > 2:
        plt.scatter(x=xl, y=yl, c=cl)
        plt.colorbar().set_label(plotargs[2])
    else:
        plt.scatter(x=xl, y=yl)

    mng = plt.get_current_fig_manager()
    mng.resize(*mng.window.maxsize())
    plt.savefig(re.sub('[/ ]', '_', name) + ".png")
    plt.show()


def objprint(q):
    for i in dir(q):
        attr = getattr(q, i)
        if i.startswith('_'):
            continue
        if isinstance(attr, types.MethodType):
            continue
        print("%s: %s" % (i, attr))
