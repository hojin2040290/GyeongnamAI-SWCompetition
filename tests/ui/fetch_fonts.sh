#!/bin/bash
# 기기 화면 캡처(device_shots.js)용 앱 글꼴(IBM Plex Sans KR)을 tests/ui/.fonts에 받아 둔다.
# 캡처용 Chromium은 이 컨테이너의 프록시를 거치지 않아 Google 글꼴을 못 받고 우분투 기본 글꼴로 그린다 (실제 휴대폰과 다르게 투박해 보임).
# 그래서 curl(프록시를 거침)로 미리 받아 두고, 캡처할 때 글꼴 요청에 이 파일을 넘겨 준다.
set -eu
F="$(cd "$(dirname "$0")" && pwd)/.fonts"; mkdir -p "$F"; cd "$F"
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
curl -s -A "$UA" 'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap' -o plex.css
: > map.txt
grep -o 'https://fonts.gstatic.com[^)]*' plex.css | sort -u | while read -r u; do
  n="$(echo "$u" | md5sum | cut -c1-12).woff2"; [ -s "$n" ] || curl -s "$u" -o "$n"; echo "$u $n" >> map.txt
done
echo "글꼴 $(wc -l < map.txt)개를 받았어요: $F"
