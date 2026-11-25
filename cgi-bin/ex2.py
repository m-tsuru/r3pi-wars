#!/usr/bin/env python3
import cgi
import sys
import codecs
import html
from pathlib import Path

sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
print('Content-type: text/html; charset=UTF-8')

# CGIのリクエストボディのサイズを制限
cgi.maxlen = 1024

form = cgi.FieldStorage()
value = form.getfirst('val')

# 文字列のエスケープ
display_value = html.escape(value)

# クエリをぶった斬る制限
if len(display_value) > 50:
  display_value = display_value[0:50]

data_file = Path(__file__).resolve().parent / "data.txt"
with Path("log.txt").open("a", encoding="utf-8") as f:
  f.write(f"{display_value}\n")

print(f'''
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Result</title>
</head>
<body>
<p>Your data is
{display_value}
.</p>
</body>
</html>
''')

