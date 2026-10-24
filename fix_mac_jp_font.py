"""旧Mac日本語TrueType(sfntリソース由来)を、Windowsで使える形に直す。
 - Mac用cmapから Unicode cmap (3,1) を作る(日本語は Shift_JIS 系、欧文のみのフォントは MacRoman)
 - OS/2テーブルが壊れている/無い場合は作り直す(縦切れ対策の行の高さも調整)
 - Windows用のフォント名(英語/日本語)を追加する
 - 読めない文字化けのファイル名は、フォント内部の名前に置き換える
使い方: python fix_mac_jp_font.py 入力フォルダ 出力フォルダ
(事前に: pip install fonttools)
"""
import os, re, sys
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


def clean_stem(stem, ps):
    """化けたままのファイル名なら直す。直せなければ内部の名前(ps)にする"""
    m = re.fullmatch(r'(.*?)(_\d+)?', stem)
    base, tail = m.group(1), m.group(2) or ''
    try:
        raw = base.encode('mac_roman')          # 変換できない=すでに日本語で正常
    except UnicodeEncodeError:
        return stem
    if all(c < 0x80 for c in raw):
        return stem                              # 英数字のみ
    try:
        return raw.decode('cp932') + tail        # 化けていたので直す
    except UnicodeDecodeError:
        return ps + tail                         # 直せない → 内部の名前


if __name__ == '__main__':
    src_dir, dst_dir = sys.argv[1], sys.argv[2]
    os.makedirs(dst_dir, exist_ok=True)
    ok = ng = 0
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(('.ttf', '.otf')):
            continue
        stem = os.path.splitext(f)[0]
        tmp = os.path.join(dst_dir, f + '.tmp')
        try:
            ps = fix(os.path.join(src_dir, f), tmp)
            out = clean_stem(stem, ps) + '.ttf'
            out = re.sub(r'[<>:"/\\|?*]', '_', out)
            os.replace(tmp, os.path.join(dst_dir, out))
            ok += 1
            print('OK ', f, '' if out == f else f'-> {out}')
        except Exception as e:
            ng += 1
            if os.path.exists(tmp):
                os.remove(tmp)
            print('NG ', f, repr(e))
    print(f'成功 {ok} 個 / 失敗 {ng} 個')