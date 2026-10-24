"""旧Mac日本語フォントCD(HFS)のISOから、Windowsで使えるTTFを作る一括ツール。

  image.iso
    -> unhfs(HFSExplorer付属)で抽出(リソースフォーク付き / AppleDouble)
    -> リソースフォークからフォント本体(sfnt)を取り出す
    -> 化けたファイル名を直す(MacRoman誤読 → Shift_JIS)
    -> Windows用に修正(Unicode cmap・フォント名・OS/2・縦切れ対策)
    -> 出力フォルダに .ttf を保存

使い方:
  python classicmac_font2win.py image.iso -o C:\\work\\mac_fonts
  python classicmac_font2win.py --extracted C:\\work\\mac -o C:\\work\\mac_fonts   (抽出済みなら unhfs を省略)

事前に必要なもの: Python 3、pip install fonttools、HFSExplorer(Javaが必要)
"""
import argparse, os, re, shutil, struct, subprocess, sys
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.ttLib.tables._n_a_m_e import NameRecord
from fontTools.ttLib.tables.O_S_2f_2 import Panose


def decode_code(code):
    b = bytes([code]) if code < 0x100 else code.to_bytes(2, 'big')
    for enc in ('shift_jis', 'cp932', 'shift_jisx0213'):
        try:
            s = b.decode(enc)
        except Exception:
            continue
        if len(s) == 1:
            return ord(s)
    return None


def load_cmap_tolerant(font):
    """cmap内のサブテーブルの長さ表記が実際より大きい(壊れた)フォントでも読めるようにする。
    先にグリフ名を仮のもの(glyph00001…)に固定しておく(cmapから名前を作る処理が壊れた表を読んで失敗するため)"""
    n = font['maxp'].numGlyphs
    font.setGlyphOrder(['.notdef'] + ['glyph%05d' % i for i in range(1, n)])
    raw = bytearray(font.reader['cmap'])
    cnt = struct.unpack('>H', raw[2:4])[0]
    offs = sorted(struct.unpack('>I', raw[8 + 8 * i:12 + 8 * i])[0] for i in range(cnt))
    for off in offs:
        nxt = [o for o in offs if o > off]
        end = nxt[0] if nxt else len(raw)
        fmt, ln = struct.unpack('>HH', raw[off:off + 4])
        if fmt in (0, 2, 4, 6) and ln > end - off:
            raw[off + 2:off + 4] = struct.pack('>H', end - off)
    cm = newTable('cmap')
    cm.decompile(bytes(raw), font)
    font['cmap'] = cm


def build_unicode_map(font):
    """Mac用cmapから {Unicode: グリフ名} を作る"""
    mac = {(t.platformID, t.platEncID): t for t in font['cmap'].tables}
    uni = {}
    if (1, 1) in mac:                       # Mac日本語
        for code, gname in sorted(mac[(1, 1)].cmap.items()):
            if code < 0x20 or code == 0x7F:
                continue
            u = decode_code(code)
            if u is not None and u <= 0xFFFF and u not in uni:
                uni[u] = gname
    elif (1, 0) in mac:                     # MacRoman(数字フォントなど)
        for code, gname in sorted(mac[(1, 0)].cmap.items()):
            if code < 0x20 or code == 0x7F:
                continue
            u = ord(bytes([code]).decode('mac_roman'))
            if u not in uni:
                uni[u] = gname
    return uni


def read_names(font):
    """名前を {nameID: 文字列} で返す。日本語Mac → MacRoman → 何でも、の順に探す"""
    names = {}
    recs = font['name'].names
    for key in ((1, 1, 0), (1, 0, 0)):
        for r in recs:
            if (r.platformID, r.platEncID, r.langID) == key and r.nameID not in names:
                try:
                    names[r.nameID] = r.toUnicode()
                except Exception:
                    pass
    for r in recs:
        if r.nameID not in names:
            try:
                names[r.nameID] = r.toUnicode()
            except Exception:
                pass
    return names


def make_os2(font, old_raw):
    if old_raw is not None:                   # 壊れた(短い)OS/2は v0 として読み直す
        raw = bytearray(old_raw)
        if len(raw) < 86:
            raw[0:2] = b'\x00\x00'
        os2 = newTable('OS/2')
        os2.decompile(bytes(raw), font)
    else:                                      # OS/2 が無い → 新規作成
        hmtx = font['hmtx'].metrics
        widths = [w for w, _ in hmtx.values() if w > 0]
        os2 = newTable('OS/2')
        os2.xAvgCharWidth = int(sum(widths) / len(widths)) if widths else 500
        os2.usWeightClass, os2.usWidthClass, os2.fsType = 400, 5, 0
        os2.ySubscriptXSize, os2.ySubscriptYSize = 650, 600
        os2.ySubscriptXOffset, os2.ySubscriptYOffset = 0, 75
        os2.ySuperscriptXSize, os2.ySuperscriptYSize = 650, 600
        os2.ySuperscriptXOffset, os2.ySuperscriptYOffset = 0, 350
        os2.yStrikeoutSize, os2.yStrikeoutPosition, os2.sFamilyClass = 50, 300, 0
        os2.panose = Panose()
        for k in ('bFamilyType', 'bSerifStyle', 'bWeight', 'bProportion', 'bContrast',
                  'bStrokeVariation', 'bArmStyle', 'bLetterForm', 'bMidline', 'bXHeight'):
            setattr(os2.panose, k, 0)
        os2.ulUnicodeRange1 = os2.ulUnicodeRange2 = os2.ulUnicodeRange3 = os2.ulUnicodeRange4 = 0
        os2.achVendID = 'NONE'
        os2.fsSelection = 0x40
    os2.version = 4
    cp = (1 << 0) | ((1 << 17) if font['maxp'].numGlyphs > 1000 else 0)  # Latin1 (+日本語)
    os2.ulCodePageRange1, os2.ulCodePageRange2 = cp, 0
    os2.sxHeight = os2.sCapHeight = os2.usDefaultChar = os2.usMaxContext = 0
    os2.usBreakChar = 32
    # 縦切れ対策: Windowsは字形を usWinAscent/usWinDescent の範囲で切るので、
    # 実際の字形の高さ(head)と行間(hhea)を含む値にする
    head, hhea = font['head'], font['hhea']
    os2.usWinAscent = max(head.yMax, hhea.ascent, 0)
    os2.usWinDescent = max(-head.yMin, -hhea.descent, 0)
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = hhea.ascent, hhea.descent, hhea.lineGap
    os2.fsSelection |= 1 << 7                 # USE_TYPO_METRICS
    return os2


def fix(src, dst):
    font = TTFont(src, lazy=True)
    old_raw = font.reader['OS/2'] if 'OS/2' in font.reader.tables else None
    load_cmap_tolerant(font)
    font['OS/2'] = os2 = make_os2(font, old_raw)

    uni = build_unicode_map(font)
    if not uni:
        raise ValueError('変換できる文字コード表がありません')
    new_tables = []
    for pid, eid in ((3, 1), (0, 3)):
        t = CmapSubtable.newSubtable(4)
        t.platformID, t.platEncID, t.language = pid, eid, 0
        t.cmap = dict(uni)
        new_tables.append(t)
    keep = [t for t in font['cmap'].tables if t.platformID == 1]  # Mac用は残す
    font['cmap'].tables = keep + [new_tables[1], new_tables[0]]
    os2.usFirstCharIndex = min(min(uni), 0xFFFF)
    os2.usLastCharIndex = min(max(uni), 0xFFFF)
    os2.recalcUnicodeRanges(font)

    # --- name : Windows用の名前を追加
    name = font['name']
    mn = read_names(font)
    base = os.path.splitext(os.path.basename(src))[0]
    ps = re.sub(r'[^A-Za-z0-9\-]', '', mn.get(6, '')) or \
         re.sub(r'[^A-Za-z0-9\-]', '', mn.get(1, '')) or \
         re.sub(r'[^A-Za-z0-9\-]', '', base) or 'Font'
    family = mn.get(1, ps).strip() or ps
    full = mn.get(4, family).strip() or family
    en_family = family if re.fullmatch(r'[\x20-\x7E]+', family) else ps
    en_full = full if re.fullmatch(r'[\x20-\x7E]+', full) else ps
    name.names = [r for r in name.names if r.platformID != 3]

    def add(nid, text, lang):
        r = NameRecord()
        r.nameID, r.platformID, r.platEncID, r.langID = nid, 3, 1, lang
        r.string = text.encode('utf_16_be')
        name.names.append(r)
    for lang, fam, fl in ((0x409, en_family, en_full), (0x411, family, full)):
        add(1, fam, lang)
        add(2, 'Regular', lang)
        add(3, mn.get(3, ps) if lang == 0x411 else ps + ';' + ps, lang)
        add(4, fl, lang)
        add(5, mn.get(5, 'Version 1.0'), lang)
        add(6, ps, lang)
        if 0 in mn:
            add(0, mn[0], lang)
    name.names.sort(key=lambda r: (r.platformID, r.platEncID, r.langID, r.nameID))

    font['post'].formatType = 3.0    # グリフ名は不要
    font.save(dst)
    font.close()
    return ps




# ---------------------------------------------------------------- リソースフォーク読み取り
def read_appledouble_rsrc(data):
    if data[:4] != b'\x00\x05\x16\x07' or len(data) < 26:
        return None
    n = struct.unpack('>H', data[24:26])[0]
    for i in range(n):
        eid, off, ln = struct.unpack('>III', data[26 + i * 12:38 + i * 12])
        if eid == 2:
            return data[off:off + ln]
    return None


def iter_sfnt(r):
    doff, moff, dlen, mlen = struct.unpack('>IIII', r[:16])
    m = r[moff:moff + mlen]
    tl = struct.unpack('>H', m[24:26])[0]
    tcount = struct.unpack('>H', m[tl:tl + 2])[0] + 1
    for i in range(tcount):
        t, c, ro = struct.unpack('>4sHH', m[tl + 2 + i * 8:tl + 2 + i * 8 + 8])
        if t != b'sfnt':
            continue
        for j in range(c + 1):
            p = tl + ro + j * 12
            rid = struct.unpack('>h', m[p:p + 2])[0]
            o = struct.unpack('>I', b'\x00' + m[p + 5:p + 8])[0]
            ln = struct.unpack('>I', r[doff + o:doff + o + 4])[0]
            yield rid, r[doff + o + 4:doff + o + 4 + ln]


# ---------------------------------------------------------------- ファイル名の修復
def clean_name(name, ps):
    """unhfsが出した化けた名前を直す。直せなければ内部の名前(ps)にする"""
    name = re.sub('[\uf000-\uf0ff]', '_', name)      # -sfm-substitutions の置換文字
    try:
        raw = name.encode('mac_roman')               # 変換できない = すでに日本語で正常
    except UnicodeEncodeError:
        return name
    if all(c < 0x80 for c in raw):
        return name                                  # 英数字のみ
    try:
        return raw.decode('cp932')                   # 化けていたので直す
    except UnicodeDecodeError:
        return ps                                    # 直せない → 内部の名前


def safe_filename(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', s).strip(' .') or 'font'


# ---------------------------------------------------------------- unhfs
def find_unhfs(user_path):
    cands = [user_path] if user_path else []
    cands += [r'C:\Program Files (x86)\HFSExplorer\bin\unhfs.bat',
              r'C:\Program Files\HFSExplorer\bin\unhfs.bat',
              shutil.which('unhfs.bat') or '', shutil.which('unhfs') or '']
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def run_unhfs(unhfs, iso, outdir, partition):
    os.makedirs(outdir, exist_ok=True)
    cmd = [unhfs, '-o', outdir, '-resforks', 'APPLEDOUBLE',
           '-sfm-substitutions', '-auto-rename', '-v']
    if partition is not None:
        cmd += ['-partition', str(partition)]
    cmd.append(iso)
    print('[1/3] unhfs で抽出中...(CDの大きさによっては数分かかります)')
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f'unhfs が失敗しました(終了コード {r.returncode})。Javaが入っているか、ISOのパスを確認してください。')


# ---------------------------------------------------------------- メイン処理
def main():
    ap = argparse.ArgumentParser(description='Mac日本語フォントCD(ISO) → Windows用TTF')
    ap.add_argument('iso', nargs='?', help='ISOイメージ(--extracted を使うなら不要)')
    ap.add_argument('-o', '--out', required=True, help='完成したTTFの出力フォルダ')
    ap.add_argument('--extracted', help='unhfsで抽出済みのフォルダ(指定すると抽出を省略)')
    ap.add_argument('--unhfs', help='unhfs.bat の場所(通常は自動で探します)')
    ap.add_argument('--partition', type=int, help='HFSパーティション番号(0から)')
    ap.add_argument('--clean', action='store_true', help='終了後に作業用フォルダを削除する')
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    work = os.path.join(out, '_work')
    os.makedirs(out, exist_ok=True)

    if a.extracted:
        ext = os.path.abspath(a.extracted)
        print('[1/3] 抽出済みフォルダを使用:', ext)
    else:
        if not a.iso:
            ap.error('ISOファイルか --extracted を指定してください')
        unhfs = find_unhfs(a.unhfs)
        if not unhfs:
            raise SystemExit('unhfs.bat が見つかりません。--unhfs で場所を指定してください。')
        ext = os.path.join(work, 'extracted')
        run_unhfs(unhfs, os.path.abspath(a.iso), ext, a.partition)

    print('[2/3] リソースフォークからフォントを取り出して変換中...')
    rawdir = os.path.join(work, 'raw')
    os.makedirs(rawdir, exist_ok=True)
    used, ok, failed, nofont = set(), 0, [], 0
    for d, _, files in os.walk(ext):
        for f in sorted(files):
            if not f.startswith('._'):
                continue
            try:
                with open(os.path.join(d, f), 'rb') as fh:
                    r = read_appledouble_rsrc(fh.read())
                if not r or len(r) < 30:
                    continue
                fonts = list(iter_sfnt(r))
            except Exception as e:
                failed.append((f, 'リソースを読めません: ' + repr(e)))
                continue
            if not fonts:
                nofont += 1                      # ビットマップフォント・アイコン等
                continue
            for rid, data in fonts:
                tmp_in = os.path.join(rawdir, 'font_%d.ttf' % (ok + len(failed)))
                tmp_out = tmp_in + '.fixed'
                try:
                    with open(tmp_in, 'wb') as fh:
                        fh.write(data)
                    ps = fix(tmp_in, tmp_out)
                    stem = safe_filename(clean_name(f[2:], ps))
                    ext_ = '.otf' if data[:4] == b'OTTO' else '.ttf'
                    final = '%s_%d%s' % (stem, rid, ext_)
                    k = 2
                    while final.lower() in used:     # 同名があれば連番
                        final = '%s_%d_%d%s' % (stem, rid, k, ext_)
                        k += 1
                    used.add(final.lower())
                    shutil.move(tmp_out, os.path.join(out, final))
                    ok += 1
                    print('  OK ', final)
                except Exception as e:
                    failed.append((f, repr(e)))
                    print('  NG ', f, repr(e))
                finally:
                    for p in (tmp_in, tmp_out):
                        if os.path.exists(p):
                            os.remove(p)

    print('[3/3] 完了')
    print(f'  フォント出力: {ok} 個   フォント無し(ビットマップ等): {nofont} 個   失敗: {len(failed)} 個')
    print('  出力先:', out)
    if failed:
        log = os.path.join(out, 'errors.txt')
        with open(log, 'w', encoding='utf-8') as fh:
            for name, msg in failed:
                fh.write(f'{name}\t{msg}\n')
        print('  失敗の一覧:', log)
    if a.clean:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    main()