"""로컬 미리보기 서버. 같은 Wi-Fi의 휴대폰에서도 접속할 수 있다.

Windows 기본 http.server는 .webp를 application/octet-stream으로 보내서
일부 브라우저(특히 iOS Safari)가 이미지를 못 띄울 수 있어 형식을 지정한다.

  python serve.py [포트=8765] [폴더=app]
"""
import functools
import http.server
import sys
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
ROOT = Path(__file__).parent / (sys.argv[2] if len(sys.argv) > 2 else "app")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".webp": "image/webp",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".js": "text/javascript",
    }

    def end_headers(self):
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet, noimageindex")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


handler = functools.partial(Handler, directory=str(ROOT))
http.server.ThreadingHTTPServer(("", PORT), handler).serve_forever()
