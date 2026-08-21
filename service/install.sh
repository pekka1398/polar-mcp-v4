#!/usr/bin/env bash
# 安裝 / 更新 polarflow 每日同步排程。可重複執行。
set -euo pipefail

UNITS=(polar-daily-sync.service polar-daily-sync.timer)
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.config/systemd/user"

mkdir -p "$DEST"
for u in "${UNITS[@]}"; do
  install -m 644 "$SRC_DIR/$u" "$DEST/$u"
done

systemctl --user daemon-reload
systemctl --user enable --now polar-daily-sync.timer

# 不開 linger 的話，你一登出這台機器,計時器就會被砍掉 -- 每天固定時間要觸發的
# 排程，這樣等於沒裝。
if [ "$(loginctl show-user "$USER" -p Linger --value)" != "yes" ]; then
  echo "⚠️  尚未啟用 linger，登出後排程會停止。請執行："
  echo "    sudo loginctl enable-linger $USER"
fi

echo
systemctl --user --no-pager status polar-daily-sync.timer | head -10
echo
echo "下次觸發時間：  systemctl --user list-timers polar-daily-sync.timer"
echo "手動立刻跑一次：systemctl --user start polar-daily-sync.service"
echo "看執行紀錄：    journalctl --user -u polar-daily-sync -f"
echo "停止排程：      systemctl --user disable --now polar-daily-sync.timer"
