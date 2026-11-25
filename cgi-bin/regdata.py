#!/usr/bin/env python3
import cgi
import sys
import codecs

sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
print('Content-type: text/html; charset=UTF-8')

form = cgi.FieldStorage()
value = form.getfirst('val')

print(f'''
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Result</title>
</head>
<body>
<p>Your data is
{value}
.</p>
</body>
</html>
''')

