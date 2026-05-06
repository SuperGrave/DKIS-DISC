"""ワーカー起動時に特定ユーザーへ Push（任意）。"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

from .supabase_store import list_boot_notification_recipient_ids

# JST の「時」（0〜23）ごとに 2 通り ×24 = 48 種。ひきこもり／インドア寄りの挨拶。
_BOOT_GREETINGS_BY_HOUR: tuple[tuple[str, str], ...] = (
    (
        "あっ……寝てました、というよりまだ起きてました。ゲームのデイリーが終わらなくて。深夜ですね。",
        "日付変わったあたりですね。カップ麺食べました、スープおいしかったです。この時間だけ無罪ですね。",
    ),
    (
        "一時台……「寝る」と言いながら動画のおすすめを見てました。おつかれさまです。",
        "あっ、寝てました……いやウソです、ソシャゲの周回してました。夜は長いですね。",
    ),
    (
        "二時ですね。外は静かで、部屋にはキーボードのカチャカチャだけです、ひきこもりBGM。",
        "夜食にポテチ開けました、おいしかったです。袋の音だけが現実ですね。",
    ),
    (
        "三時台……鳥が鳴く前の暗さ、カーテン隙間から見える街灯だけが頼りです。",
        "あっ、寝てました。二度寝ではなく三度寝コースでした、ダメな朝じゃなくてダメな深夜ですね。",
    ),
    (
        "四時……もうすぐ朝ですね。昨日のお皿、まだ流しにあります（あるある）。",
        "早朝のニュースつけようか迷って、結局配信のアーカイブでした。インドア確定ですね。",
    ),
    (
        "五時台ですね。おにぎり食べました、コンビニのやつおいしかったです。まだ薄暗くて安心します。",
        "寝坊というより徹夜というより、「ちょっとだけ」の連続ですね。おはようの気持ちだけは真面目です。",
    ),
    (
        "六時……朝ですね。日光が眩しくてカーテン閉めました。勝ちました（遮光）。",
        "あっ、寝てました。アラームは友情終了音として無視しました、ごめんなさい。",
    ),
    (
        "七時台、おはようの時間ですね。食パンだけ焼いてバター薄め、インドアの朝ごはんです。",
        "コーヒー淹れたので脳がようやく起きました。外は通勤ラッシュ、窓の外だけ見てニヤニヤしてました。",
    ),
    (
        "八時ですね。まだパジャマです。在宅ムーブという名の引きこもりです。",
        "ゲームのログボ回収してました。昼までもうちょいですね、現実はノーカウントで。",
    ),
    (
        "九時台……ブラウザのタブが増殖してました。整理するほどの体力はありません。",
        "お腹すいたのでチキンナゲット温めました、おいしかったです。昼まで持つか不安ですね。",
    ),
    (
        "十時……そろそろお昼が見えてきました。コンビニ行くかウバーか、今日も永恒の選択ですね。",
        "あっ、動画見てました。おすすめアルゴリズムに敗北しました、おいしかったです（視聴体験が）。",
    ),
    (
        "十一時台ですね。冷蔵庫を開け閉めして意思決定をサボってました。",
        "ゲームしてました。ガチャは引かずに我慢したので精神的勝利です、昼ですね。",
    ),
    (
        "お昼ですね。今日の弁当おいしかったです、ソースちょい辛めで良かったです。",
        "ゲームしながらご飯食べてました。片手ずつという堕落、それでもおいしかったですね。",
    ),
    (
        "昼過ぎ……眠気が勝ちそうなのでソファで横になりました。これは休息です（断言）。",
        "昼カレー食べました、ルー濃くておいしかったです。インドア的幸福が峰值ですね。",
    ),
    (
        "二時台ですね。エアコン効いた部屋から外の眩しさ見てニヤニヤしてました。",
        "おやつにチョコ食べました、おいしかったです。カロリーは部屋に閉じ込めました（心理的）。",
    ),
    (
        "三時……ティータイムという名の糖分摂取ですね。お茶は正直どうでもいいです。",
        "ソシャゲのイベント周回してました。時間が溶けました、おいしかったです（報酬が）。",
    ),
    (
        "四時台ですね。そろそろ夕焼けかもしれない時間、カーテン閉めてますけど雰囲気だけ味わってます。",
        "あっ、寝てました……昼寝が長引きました。ごめんなさい、重力に敗北しました。",
    ),
    (
        "五時……小腹すいたので唐揚げ頼みました、サクサクでおいしかったです。デリバリー様様ですね。",
        "夕方ですね。お湯沸かしてうどんにしました、シンプルにおいしかったです。外は無視。",
    ),
    (
        "六時台、そろそろ晩ごはんですね。炊きたてのご飯おいしかったです、気が早いですが勝ち。",
        "配信見ながら食べてました。話題はコメント欄にお任せして、ご飯だけは真面目に味わいました。",
    ),
    (
        "七時……夜の部屋、間接照明だけにしました。落ち着きますね、インドア演出。",
        "自炊チャレンジしました、味は謎でしたが effort はおいしかったです（精神論）。",
    ),
    (
        "八時台ですね。お風呂沸かす番です、ダラダラ延長戦。勝ち筋は湯船です。",
        "ゲームのイベント締切が今夜でして……終わらせてました。夜は短く感じますね（錯覚）。",
    ),
    (
        "九時……お風呂入ってホカホカ、これがインドアのご褒美ですね。",
        "晩ごはんの卵焼きおいしかったです、甘めでしたねこの時間帯。おつかれさまです。",
    ),
    (
        "十時台ですね。そろそろ寝ようか迷ってスマホいじってました、古典的敗北です。",
        "あっ、寝てました（嘘）、漫画読んでました。ページが進むほど夜が進みますね。",
    ),
    (
        "十一時……明日の自分にツケを回して今日はこれくらいで、健全な逃避ですね。",
        "夜食にアイス食べました、おいしかったです。もう寝ます……たぶん……だいたい。",
    ),
)


def _boot_greeting_hour_jst() -> int:
    return datetime.now(ZoneInfo("Asia/Tokyo")).hour


def pick_worker_boot_greeting_text() -> str:
    """JST の現在時に応じた 48 種から 1 通を選ぶ。"""
    h = _boot_greeting_hour_jst()
    a, b = _BOOT_GREETINGS_BY_HOUR[h]
    return random.choice((a, b))


def _skip_stored_ids_for_boot_push() -> bool:
    return (os.environ.get("LINE_BOOT_GREETING_SKIP_STORED_IDS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _merged_boot_recipient_ids() -> list[str]:
    env_raw = (os.environ.get("LINE_BOOT_GREETING_USER_IDS") or "").strip()
    env_ids = [x.strip() for x in env_raw.split(",") if x.strip()]
    extra = [] if _skip_stored_ids_for_boot_push() else list_boot_notification_recipient_ids()
    seen: set[str] = set()
    merged: list[str] = []
    for uid in env_ids + extra:
        if uid in seen:
            continue
        seen.add(uid)
        merged.append(uid)
    return merged


def maybe_send_worker_boot_greetings(configuration: object, logger: logging.Logger) -> None:
    """起動時に定型 Push。
    - ``LINE_BOOT_GREETING_USER_IDS`` …カンマ区切りで明示（運用者向け・任意）
    - Supabase の ``known_line_users.notify_on_restart=true`` …ユーザーが **SET-SETTING** でオプトインした Id のみ（任意・上限あり）

    LINE が過去ユーザ一覧を返すことはないため **Webhook での記録が前提**。``notify_worker_restart`` をオンにしたユーザーだけ DB 経由で届きます。環境変数の Id はそのままマージされます。
    """
    uids = _merged_boot_recipient_ids()
    if not uids:
        return
    text = pick_worker_boot_greeting_text()
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            for uid in uids:
                api.push_message(PushMessageRequest(to=uid, messages=[TextMessage(text=text)]))
        logger.info("LINE boot greeting sent to %d recipient(s)", len(uids))
    except Exception:
        logger.exception("LINE boot greeting push failed")
