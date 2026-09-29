"""Пост-обработка: у 2D barChart ровно два axId (pptxgenjs пишет третий, необъявленный)."""
import re, shutil, sys, zipfile
src = sys.argv[1]
tmp = src + ".tmp"
with zipfile.ZipFile(src) as z, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as w:
    for n in z.namelist():
        data = z.read(n)
        if re.match(r"ppt/charts/chart\d+\.xml$", n):
            c = data.decode()
            declared = set(re.findall(r'<c:(?:catAx|valAx|serAx|dateAx)>\s*<c:axId val="(\d+)"/>', c))
            c = re.sub(r'<c:axId val="(\d+)"/>', lambda m: m.group(0) if m.group(1) in declared else "", c)
            data = c.encode()
        w.writestr(z.getinfo(n), data)
shutil.move(tmp, src)
print("fixed", src)
