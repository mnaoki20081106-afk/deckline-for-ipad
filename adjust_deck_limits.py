#!/usr/bin/env python3
"""
Deckline風: 締切までに新規カードを終わらせるよう、
デッキの「1日の新規カード数」を自動計算してAnkiWebに反映するスクリプト。

2つのモードに対応:
  - even_decks: デッキごとに個別の締切を持ち、並行して"満遍なく"進める
  - sequential_groups: 複数デッキで1つの締切を共有し、"順番に"1つずつ終わらせる

設定は config.json (settings.html から生成/編集される想定):
{
  "even_decks": [
    {"name": "英単語", "deadline": "2026-09-01", "min_per_day": 0, "max_per_day": 9999}
  ],
  "sequential_groups": [
    {"deadline": "2026-09-01", "decks": ["資格試験::第1章", "資格試験::第2章"]}
  ]
}

【重要な注意】
- Ankiが公式にドキュメント化している機能ではなく、`anki` パッケージの
  内部API(col.sync_login / col.sync_collection / full_upload_or_download)を
  直接呼び出しています。Ankiのバージョンアップで壊れる可能性があります。
- 初回はGitHub Actionsの手動実行(workflow_dispatch)でログを確認し、
  想定通り動くか確認してから、スケジュール実行を有効にしてください。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from anki.collection import Collection

COLLECTION_PATH = os.environ.get("ANKI_COLLECTION_PATH", "./collection.anki2")
CONFIG_PATH = os.environ.get("ANKI_CONFIG_PATH", "./config.json")
DECKS_OUTPUT_PATH = os.environ.get("ANKI_DECKS_OUTPUT_PATH", "./decks.json")


def log(msg: str) -> None:
    print(f"[deckline] {msg}", flush=True)


def load_config() -> dict:
    if not Path(CONFIG_PATH).exists():
        return {"even_decks": [], "sequential_groups": []}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    # 旧フォーマット("decks"キー)との互換
    if "decks" in cfg and "even_decks" not in cfg:
        cfg["even_decks"] = cfg.pop("decks")

    cfg.setdefault("even_decks", [])
    cfg.setdefault("sequential_groups", [])
    return cfg


def _wipe_local_collection(path: str) -> None:
    """ローカルのコレクションファイルとSQLite付随ファイルを削除し、まっさらな状態に戻す"""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(path + suffix)
        if p.exists():
            p.unlink()


def initial_login_and_sync(username: str, password: str, path: str, attempts: int = 4) -> tuple[Collection, object]:
    """
    ログイン→同期を行い、使える状態のコレクションを返す。

    初回のフル同期(FULL_DOWNLOAD)は "HttpError: missing original size" で
    失敗することがある(AnkiDroid側でも同様の既知の初回同期不具合が報告されている)。
    単純な再試行では直らなかったため、失敗のたびにローカルのコレクションを
    削除して作り直し、ログインからやり直す(=デスクトップ版を閉じて再起動する
    のに近い状態)ことで回復を試みる。
    """
    last_err = None
    for i in range(1, attempts + 1):
        if i > 1:
            # 失敗した場合のみ、まっさらな状態に作り直して再ログインする
            _wipe_local_collection(path)
        col = Collection(path)
        try:
            log(f"AnkiWebにログイン中... (試行{i}/{attempts})")
            auth = col.sync_login(username=username, password=password, endpoint=None)

            log("同期(取得)を実行中...")
            out = col.sync_collection(auth, True)  # メディア同期も有効化

            if out.required == out.NO_CHANGES:
                log("同期完了(差分なし、または通常マージ済み)")
                return col, auth

            server_usn = out.server_media_usn if hasattr(out, "server_media_usn") else None

            if out.required == out.FULL_UPLOAD:
                log("ローカル側が最新のため FULL_UPLOAD を実行します")
                upload = True
            else:
                # FULL_DOWNLOAD、またはユーザー確認が本来必要な競合状態。
                # 無人実行では安全側に倒し、サーバーのデータを消さないよう download を選ぶ。
                log("FULL_DOWNLOAD を実行します(競合状態の場合は安全のためdownloadを選択)")
                upload = False

            col.close_for_full_sync()
            col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=upload)

            col = Collection(path)
            return col, auth

        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"同期に失敗しました(試行{i}/{attempts}): {e}")
            try:
                col.close()
            except Exception:  # noqa: BLE001
                pass
            if i < attempts:
                wait = 10 * i
                log(f"{wait}秒待ってから、コレクションを作り直して再試行します...")
                time.sleep(wait)

    raise last_err


def push_changes(col: Collection, auth, attempts: int = 3) -> None:
    """
    ローカルでの変更をAnkiWebに送信する。
    こちらは既に実データ(今回の変更)を含むため、初回同期のような
    「作り直し」はせず、通常の同期呼び出しのみ軽くリトライする。
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            out = col.sync_collection(auth, True)
            if out.required == out.NO_CHANGES:
                log("同期完了(変更を送信しました)")
                return

            server_usn = out.server_media_usn if hasattr(out, "server_media_usn") else None
            upload = out.required == out.FULL_UPLOAD
            col.close_for_full_sync()
            col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=upload)
            log("同期完了(フル同期で反映しました)")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"変更の送信に失敗しました(試行{i}/{attempts}): {e}")
            if i < attempts:
                time.sleep(10 * i)
    raise last_err


def days_left_until(deadline_str: str) -> int:
    deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
    today = date.today()
    return max((deadline - today).days, 1)


def count_new_cards(col: Collection, deck_name: str) -> int | None:
    deck = col.decks.by_name(deck_name)
    if deck is None:
        return None
    return len(col.find_cards(f'deck:"{deck_name}" is:new'))


def set_new_per_day(col: Collection, deck_name: str, value: int) -> int | None:
    deck = col.decks.by_name(deck_name)
    if deck is None:
        return None
    did = deck["id"]
    conf = col.decks.config_dict_for_deck_id(did)
    old_value = conf.get("new", {}).get("perDay")
    conf.setdefault("new", {})["perDay"] = value
    col.decks.update_config(conf)
    return old_value


def process_even_decks(col: Collection, even_decks: list[dict]) -> bool:
    """デッキごとに個別の締切で、並行して満遍なく進めるモード"""
    changed = False
    for deck_cfg in even_decks:
        name = deck_cfg["name"]
        min_per_day = deck_cfg.get("min_per_day", 0)
        max_per_day = deck_cfg.get("max_per_day", 9999)

        remaining = count_new_cards(col, name)
        if remaining is None:
            log(f"[均等モード]デッキが見つかりません: {name} (スキップ)")
            continue

        days_left = days_left_until(deck_cfg["deadline"])
        new_per_day = math.ceil(remaining / days_left) if remaining > 0 else 0
        new_per_day = max(min_per_day, min(max_per_day, new_per_day))

        old_value = set_new_per_day(col, name, new_per_day)
        log(
            f"[均等モード]「{name}」: 残り{remaining}枚 / 残り{days_left}日 "
            f"→ 1日の新規カード数を {old_value} → {new_per_day} に更新"
        )
        changed = True
    return changed


def process_sequential_groups(col: Collection, groups: list[dict]) -> bool:
    """複数デッキで締切を共有し、1つずつ順番に終わらせるモード"""
    changed = False
    for group in groups:
        deck_names = group["decks"]
        days_left = days_left_until(group["deadline"])

        remaining_counts: dict[str, int] = {}
        total_remaining = 0
        for name in deck_names:
            cnt = count_new_cards(col, name)
            if cnt is None:
                log(f"[順番モード]デッキが見つかりません: {name} (グループ内でスキップ)")
                continue
            remaining_counts[name] = cnt
            total_remaining += cnt

        daily_budget = math.ceil(total_remaining / days_left) if total_remaining > 0 else 0
        budget_left = daily_budget

        log(
            f"[順番モード]グループ(締切{group['deadline']}): "
            f"合計残り{total_remaining}枚 / 残り{days_left}日 → 今日の総予算{daily_budget}枚"
        )

        for name in deck_names:
            if name not in remaining_counts:
                continue
            cnt = remaining_counts[name]
            assign = max(0, min(cnt, budget_left))
            budget_left -= assign

            old_value = set_new_per_day(col, name, assign)
            log(f"  └「{name}」: 残り{cnt}枚 → 今日の新規カード数 {old_value} → {assign}")
            changed = True
    return changed


def export_deck_list(col: Collection) -> None:
    """設定画面(settings.html)がデッキ一覧を読み込めるようにJSON出力"""
    names = sorted(name for name, _id in col.decks.all_names_and_ids())
    # "Default"のような空デッキも含めて出しておく(ユーザーが選ばなければ無害)
    with open(DECKS_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"decks": names, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    log(f"デッキ一覧を書き出しました: {DECKS_OUTPUT_PATH} ({len(names)}件)")


def main() -> int:
    username = os.environ.get("ANKIWEB_USERNAME")
    password = os.environ.get("ANKIWEB_PASSWORD")

    if not username or not password:
        log("エラー: 環境変数 ANKIWEB_USERNAME / ANKIWEB_PASSWORD が設定されていません")
        return 1

    config = load_config()
    even_decks = config.get("even_decks", [])
    sequential_groups = config.get("sequential_groups", [])

    Path(COLLECTION_PATH).parent.mkdir(parents=True, exist_ok=True)

    col, auth = initial_login_and_sync(username, password, COLLECTION_PATH)
    try:
        export_deck_list(col)

        changed = False
        if even_decks:
            changed |= process_even_decks(col, even_decks)
        if sequential_groups:
            changed |= process_sequential_groups(col, sequential_groups)

        if not even_decks and not sequential_groups:
            log("config.json に設定がありません(even_decks / sequential_groups が空)")

        if changed:
            log("変更をAnkiWebに反映(同期・送信)中...")
            push_changes(col, auth)
        else:
            log("変更なし。反映はスキップします。")

    finally:
        col.close()

    log("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
