
iridium_access = "001100000011000011110011"  # Actually 0x789h in BPSK
uplink_access = "110011000011110011111100"  # BPSK: 0xc4b
bitstream_raw = "001100000011000011110011"

access = []
map = [0, 1, 3, 2]
# back into bpsk symbols
for x in range(0, len(iridium_access) - 1, 2):
    access.append(map[int(bitstream_raw[x + 0]) * 2 + int(bitstream_raw[x + 1])])

print(access)

# undo differential decoding
for c in range(1, len(access) - 1):
    access[c] = (access[c - 1] + access[c]) % 4

print(access)