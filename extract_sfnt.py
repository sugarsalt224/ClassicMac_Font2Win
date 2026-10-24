import os, sys, struct

def get_rsrc(data):
    """(リソースフォーク or None, 判定メモ) を返す"""
    if len(data) == 0:
        return None, '空(0バイト)'
    if data[:4] == b'\x00\x05\x16\x07':  # AppleDouble
        n = struct.unpack('>H', data[24:26])[0]
        for i in range(n):
            eid, off, ln = struct.unpack('>III', data[26+i*12:38+i*12])
            if eid == 2:
                return data[off:off+ln], 'AppleDouble'
        return None, 'AppleDouble(リソースフォークなし)'
    # MacBinary: 厳しめに判定
    if len(data) >= 128 and data[0] == 0 and data[74] == 0 and data[82] == 0 and 1 <= data[1] <= 63:
        dlen, rlen = struct.unpack('>II', data[83:91])
        start = 128 + ((dlen + 127) // 128) * 128
        if rlen and start + rlen <= len(data) + 127:
            return data[start:start+rlen], 'MacBinary'
    return None, '形式不明 先頭=' + data[:16].hex()

def sfnts(r):
    doff, moff, dlen, mlen = struct.unpack('>IIII', r[:16])
    m = r[moff:moff+mlen]
    tl = struct.unpack('>H', m[24:26])[0]
    tcount = struct.unpack('>H', m[tl:tl+2])[0] + 1
    for i in range(tcount):
        t, c, ro = struct.unpack('>4sHH', m[tl+2+i*8:tl+2+i*8+8])
        if t != b'sfnt':
            continue
        for j in range(c + 1):
            p = tl + ro + j * 12
            rid = struct.unpack('>h', m[p:p+2])[0]
            o = struct.unpack('>I', b'\x00' + m[p+5:p+8])[0]
            ln = struct.unpack('>I', r[doff+o:doff+o+4])[0]
            yield rid, r[doff+o+4:doff+o+4+ln]

src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
ok = skipped = 0
for d, _, files in os.walk(src):
    for f in files:
        data = open(os.path.join(d, f), 'rb').read()
        r, note = get_rsrc(data)
        if not r:
            if not f.startswith('._') and len(data) == 0:
                continue  # 本体側の0KBファイルは無視
            print('対象外:', f.encode('unicode_escape').decode(), f'({len(data)}バイト)', note)
            skipped += 1
            continue
        try:
            found = False
            for rid, font in sfnts(r):
                base = f[2:] if f.startswith('._') else f
                ext = '.otf' if font[:4] == b'OTTO' else '.ttf'
                out = os.path.join(dst, f"{base}_{rid}{ext}")
                open(out, 'wb').write(font)
                ok += 1
                found = True
            if not found:
                print('sfntなし:', f.encode('unicode_escape').decode(), note)
                skipped += 1
        except Exception as e:
            print('失敗:', f.encode('unicode_escape').decode(), f'({len(data)}バイト)', note, repr(e))
            skipped += 1
print(f'出力 {ok} 個 / スキップ {skipped} 個')