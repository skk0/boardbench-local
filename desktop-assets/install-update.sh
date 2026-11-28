#!/bin/sh
set -eu
stage=$BB_STAGE
app=$BB_TARGET
log="$BB_DATA/update-install.log"
exec >>"$log" 2>&1
date
for i in $(seq 1 120); do
  kill -0 "$BB_PID" 2>/dev/null || break
  sleep 1
done
if kill -0 "$BB_PID" 2>/dev/null; then echo '原进程仍在运行，取消更新。'; exit 1; fi
relaunch() { /usr/bin/open -n "$app" --args --port "$BB_PORT" --data-dir "$BB_DATA"; }
mv "$app" "$stage/previous.app"
if mv "$stage/BoardBench.app" "$app"; then
  /usr/bin/open -W -n "$app" --args --port "$BB_PORT" --data-dir "$BB_DATA" --update-receipt "$stage/started" &
  opener=$!
  for i in $(seq 1 120); do
    if [ -s "$stage/started" ]; then
      echo '新版窗口已打开。'
      rm -rf "$stage"
      exit 0
    fi
    kill -0 "$opener" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$opener" 2>/dev/null; then echo "尚未确认启动；旧版保存在 $stage/previous.app"; exit 1; fi
  mv "$app" "$stage/failed.app"
fi
mv "$stage/previous.app" "$app"
echo '更新失败，已还原。'
relaunch
