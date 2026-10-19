# -*- coding: utf-8 -*-
"""把 image_app.py 里的内联 PAGE 字符串换成「从 ui/image_page.html 读取」。"""
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "image_app.py")
src = open(P, encoding="utf-8").read()

start = src.index('PAGE = r"""')
end = src.index('def main():')
# 找到 PAGE 字符串的结尾三引号（位于 def main 之前）
tail = src[start:end]
close = tail.rindex('"""')
new_block = '''PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_page.html")


def load_page():
    """页面是独立 HTML 文件：改界面不用动 Python，也不怕转义炸。"""
    try:
        with open(PAGE_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("<!doctype html><meta charset='utf-8'><h1>页面读取失败</h1>"
                "<pre>%s\\n应当存在：%s</pre>" % (e, PAGE_FILE))


'''

src2 = src[:start] + new_block + src[start + close + 3:]
# handler 里改成 load_page()
src2 = src2.replace('self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")',
                    'self._send(200, load_page().encode("utf-8"), "text/html; charset=utf-8")')
open(P, "w", encoding="utf-8", newline="\n").write(src2)
print("rewrote", P, len(src2), "bytes")
