import os, sys

root = sys.argv[1]  # 抽出したフォルダ
for d, dirs, files in os.walk(root, topdown=False):
    for n in files + dirs:
        try:
            new = n.encode('mac_roman').decode('cp932')
        except Exception:
            continue  # 変換できない名前はそのまま
        if new != n:
            os.rename(os.path.join(d, n), os.path.join(d, new))
            print(n, '->', new)