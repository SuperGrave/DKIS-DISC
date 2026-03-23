from threading import Thread
import random
import string
import time
from flask import Blueprint, request, jsonify, send_file, send_from_directory, session, Response


def create_api_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("api_routes", __name__)
    app = deps["app"]
    login_required = deps["login_required"]
    role_required = deps["role_required"]
    FRONT_INDEX_PATH = deps["FRONT_INDEX_PATH"]
    MAX_RETRIES = deps["MAX_RETRIES"]
    TTS_CACHE_DIR = deps["TTS_CACHE_DIR"]
    YELLOW_BRIGHT = deps["YELLOW_BRIGHT"]
    RESET = deps["RESET"]
    get_setting = deps["get_setting"]
    get_current_settings = deps["get_current_settings"]
    update_settings = deps["update_settings"]
    increment_stat = deps["increment_stat"]
    send_event = deps["send_event"]
    handle_chat_only_input = deps["handle_chat_only_input"]
    handle_user_input_v2 = deps["handle_user_input_v2"]
    set_webhook_url = deps["set_webhook_url"]
    get_next_global_retry_id = deps.get("get_next_global_retry_id", lambda: 0)
    get_next_conversation_retry_id = deps.get("get_next_conversation_retry_id")
    set_current_location = deps["set_current_location"]
    get_current_location = deps["get_current_location"]
    latlon_to_address = deps["latlon_to_address"]
    muniCd_dict = deps["muniCd_dict"]
    enqueue_utterance = deps["enqueue_utterance"]

    def emit_retry_like_log(content: str, raw_result: dict | None = None, raw_result_source: str = "PLAY-YOUTUBE"):
        payload = {
            "retry_index": 1,
            "retry_total": 1,
            "retry_content": content,
            "global_retry_id": get_next_global_retry_id() if callable(get_next_global_retry_id) else 0,
            "conversation_retry_id": get_next_conversation_retry_id() if callable(get_next_conversation_retry_id) else 0,
        }
        if raw_result is not None:
            payload["raw_result"] = raw_result
            payload["raw_result_source"] = raw_result_source
        send_event("retry_input", payload)

    def run_youtube_selection_auto_retry(youtube_state: dict, candidates: list[dict], selected: dict, selected_index: int, *, selection_source: str, user_id: str | None = None, client_id: str | None = None):
        from core.context_provider import set_last_proc_result
        from core.input_builder import build_input_segments

        title = selected.get("title", "選択した動画")
        total = len(candidates or [])
        status = f"候補 {selected_index}『{title}』を再生。"
        retry_text = f"ユーザーは{total}個の候補から『{title}』を選択しました。動画の再生を開始しています。"
        raw_result = {
            "query": youtube_state.get("source_query", ""),
            "mode": youtube_state.get("selection_mode", "user_select"),
            "status": status,
            "selected": selected,
            "selected_index": selected_index,
            "selection_source": selection_source,
            "candidates": candidates,
        }

        set_last_proc_result(status)
        retry_payload = build_input_segments("main", retry_text, is_retry=True)
        increment_stat("retry_inputs")
        send_event("retry_input", {
            "retry_index": 1,
            "retry_total": 1,
            "retry_content": retry_payload["text"],
            "global_retry_id": get_next_global_retry_id() if callable(get_next_global_retry_id) else 0,
            "conversation_retry_id": get_next_conversation_retry_id() if callable(get_next_conversation_retry_id) else 0,
            "raw_result": raw_result,
            "raw_result_source": "PLAY-YOUTUBE",
        })
        handle_user_input_v2(
            retry_text,
            is_retry=True,
            retry_count=1,
            max_retries=MAX_RETRIES,
            user_id=user_id,
            client_id=client_id,
        )

    def handle_pending_youtube_selection_input(selection_text: str, user_id: str | None = None, client_id: str | None = None):
        from core.youtube_state import get_youtube_state, activate_youtube_candidate, clear_youtube_state

        youtube_state = get_youtube_state()
        if not youtube_state.get("pending_selection"):
            return False

        stripped = (selection_text or "").strip()
        candidates = youtube_state.get("candidates") or []
        total = len(candidates)
        if total <= 0:
            clear_youtube_state()
            return True

        cancel_words = {"キャンセル", "中止", "やめる", "やめて", "取り消し", "なし"}
        if stripped in cancel_words:
            clear_youtube_state()
            status = "YouTube候補の選択をキャンセルしました。"
            emit_retry_like_log(
                f"RI：YouTube候補選択をキャンセル\n結果: {status}\nLP：{status}",
                {
                    "query": youtube_state.get("source_query", ""),
                    "mode": youtube_state.get("selection_mode", "user_select"),
                    "status": status,
                    "canceled": True,
                    "candidates": candidates,
                },
            )
            enqueue_utterance("(困)YouTubeの候補選択をキャンセルしました。", turn_id="youtube-select-cancel", emotion="(困)")
            return True

        if not stripped.isdigit():
            enqueue_utterance(
                f"(困){total}件の候補が出ています。{1}から{total}の数字で選ぶか、「キャンセル」と言ってください。",
                turn_id="youtube-select-guide",
                emotion="(困)",
            )
            return True

        selected_index = int(stripped)
        if selected_index < 1 or selected_index > total:
            enqueue_utterance(
                f"(困)選べるのは1から{total}までです。もう一度番号を言ってください。",
                turn_id="youtube-select-invalid",
                emotion="(困)",
            )
            return True

        selected = activate_youtube_candidate(
            selected_index,
            selection_source="voice",
            reason=f"候補 {selected_index} をユーザーが選択。",
            autoplay=True,
            muted=False,
        )
        if not selected:
            enqueue_utterance("(困)候補の再生準備に失敗しました。別の番号でもう一度試してください。", turn_id="youtube-select-error", emotion="(困)")
            return True

        if not get_setting("youtube.send_selection_to_main_loop", True):
            return True

        run_youtube_selection_auto_retry(
            youtube_state,
            candidates,
            selected,
            selected_index,
            selection_source="voice",
            user_id=user_id,
            client_id=client_id,
        )
        return True

    @bp.route("/", methods=["GET"], endpoint="index")
    @login_required
    def index_route():
        app.logger.info(f"[Access] ユーザー {session.get('user_id')} がメインページにアクセス")
        return send_file(FRONT_INDEX_PATH)

    @bp.route("/callback", methods=["POST"], endpoint="callback")
    @login_required
    def callback_route():
        data = request.json or {}
        user_text = data.get("text", "")
        webhook = data.get("webhook")
        client_id = data.get("client_id")
        user_id = session.get("user_id")

        print(f"\nユーザーの入力： {user_text}")
        increment_stat("user_inputs")
        if webhook:
            set_webhook_url(webhook)

        def background_process():
            if handle_pending_youtube_selection_input(user_text, user_id=user_id, client_id=client_id):
                return

            ai_loop_mode = get_setting("control.ai_loop_mode", "main")
            if ai_loop_mode == "chat_only":
                handle_chat_only_input(user_text, user_id=user_id, client_id=client_id)
                return

            text, should_retry, summary, processing_time, raw_result, raw_result_source = handle_user_input_v2(
                user_text,
                max_retries=MAX_RETRIES,
                user_id=user_id,
                client_id=client_id,
            )
            retry_count = 0
            while should_retry and retry_count < MAX_RETRIES:
                retry_count += 1
                print(f"\n≪RETRY発動 → 再度処理を開始 #{retry_count}/{MAX_RETRIES}≫\n")
                increment_stat("retry_inputs")

                next_input = summary if summary else text
                try:
                    retry_payload = {
                        "retry_index": retry_count,
                        "retry_total": MAX_RETRIES,
                        "retry_content": f"RI：{next_input}",
                    }
                    if raw_result is not None:
                        retry_payload["raw_result"] = raw_result
                    if raw_result_source is not None:
                        retry_payload["raw_result_source"] = raw_result_source
                    if get_next_global_retry_id and callable(get_next_global_retry_id):
                        retry_payload["global_retry_id"] = get_next_global_retry_id()
                    if get_next_conversation_retry_id and callable(get_next_conversation_retry_id):
                        retry_payload["conversation_retry_id"] = get_next_conversation_retry_id()
                    send_event("retry_input", retry_payload)
                except Exception as _e:
                    app.logger.debug(f"[SSE] retry_input notify failed: {_e}")
                text, should_retry, summary, processing_time, raw_result, raw_result_source = handle_user_input_v2(
                    next_input, is_retry=True, retry_count=retry_count, max_retries=MAX_RETRIES
                )

        Thread(target=background_process, daemon=True).start()
        return jsonify({"reply": "processing"})

    @bp.route("/set_voice_mode", methods=["POST"], endpoint="set_voice_mode")
    @role_required("operator")
    def set_voice_mode_route():
        data = request.get_json(force=True) or {}
        enabled = data.get("enabled", True)

        from core.voicevox_handler import set_mute, set_unmute

        if enabled:
            set_unmute()
            app.logger.info("[TTS] 音声合成: ON")
        else:
            set_mute()
            app.logger.info("[TTS] 音声合成: OFF")

        try:
            current_settings = get_current_settings()
            new_settings = current_settings.copy()
            tts_settings = (new_settings.get("tts") or {}).copy()
            tts_settings["enabled"] = bool(enabled)
            new_settings["tts"] = tts_settings
            success = update_settings(new_settings)
            if not success:
                app.logger.error("[TTS] 設定更新に失敗しました（tts.enabled）")
        except Exception as e:
            app.logger.error(f"[TTS] 設定更新エラー（tts.enabled）: {e}")

        send_event("tts_enabled_changed", {"enabled": enabled})
        return jsonify({"ok": True, "enabled": enabled})

    @bp.route("/voicevox/user_dict", methods=["GET"], endpoint="voicevox_user_dict_list")
    @role_required("operator")
    def voicevox_user_dict_list_route():
        from core.voicevox_handler import list_voicevox_dict_words

        words = list_voicevox_dict_words()
        return jsonify({"ok": True, "words": words})

    @bp.route("/voicevox/user_dict", methods=["POST"], endpoint="voicevox_user_dict_add")
    @role_required("operator")
    def voicevox_user_dict_add_route():
        from core.voicevox_handler import add_voicevox_dict_word

        data = request.get_json(force=True) or {}
        surface = (data.get("surface") or "").strip()
        pronunciation = (data.get("pronunciation") or "").strip()
        accent_type = data.get("accent_type", 1)
        priority = data.get("priority", 5)

        if not surface or not pronunciation:
            return jsonify({"ok": False, "error": "surface と pronunciation は必須です。"}), 400

        try:
            word = add_voicevox_dict_word(surface, pronunciation, accent_type, priority)
            return jsonify({"ok": True, "word": word})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/voicevox/user_dict/<word_uuid>", methods=["DELETE"], endpoint="voicevox_user_dict_delete")
    @role_required("operator")
    def voicevox_user_dict_delete_route(word_uuid):
        from core.voicevox_handler import delete_voicevox_dict_word

        try:
            delete_voicevox_dict_word(word_uuid)
            return jsonify({"ok": True})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/set_location", methods=["POST"], endpoint="set_location")
    @login_required
    def set_location_route():
        from core.utils import normalize_location_text

        data = request.get_json(force=True) or {}
        print(YELLOW_BRIGHT + f"[GPS]位置情報受信         " + RESET + str(data))

        if "location" in data and data["location"]:
            lat = data.get("lat")
            lon = data.get("lon")
            location_text = normalize_location_text(data["location"])
            if lat is not None and lon is not None:
                set_current_location(location_text, lat=float(lat), lon=float(lon))
            else:
                set_current_location(location_text)
            print(YELLOW_BRIGHT + "[GPS]位置情報手動入力     " + RESET + get_current_location())
            return jsonify({"ok": True, "location": get_current_location()})

        if "lat" in data and "lon" in data:
            try:
                lat = float(data["lat"])
                lon = float(data["lon"])
                address, elapsed = latlon_to_address(lat, lon, muniCd_dict)
                municipality = normalize_location_text(address)
                set_current_location(municipality, lat=lat, lon=lon)
            except Exception as e:
                print(YELLOW_BRIGHT + f"[GPS]位置情報処理エラー:{e}" + RESET)
                municipality = "不明（GPSエラー）"
                try:
                    set_current_location(municipality, lat=lat, lon=lon)
                except Exception:
                    set_current_location(municipality)
            return jsonify({"ok": True, "location": municipality})

        return jsonify({"ok": False, "error": "no 'location' or 'lat/lon'"}), 400

    @bp.route("/audio/<path:filename>", methods=["GET"], endpoint="serve_audio")
    @login_required
    def serve_audio_route(filename):
        if ".." in filename or filename.startswith("/"):
            return jsonify({"error": "Invalid filename"}), 400
        return send_from_directory(TTS_CACHE_DIR, filename)

    @bp.route("/tap_event", methods=["POST"], endpoint="tap_event")
    @login_required
    def tap_event_route():
        try:
            data = request.get_json(force=True) or {}
            kind = data.get("kind")
            if kind not in ("five", "ten"):
                return jsonify({"ok": False, "error": "invalid kind"}), 400

            if kind == "five":
                text = "(照)ちょ、ちょっと…！そんなに触らないでください…！マスター、困ります…！"
                enqueue_utterance(text, turn_id="tap-event", emotion="(照)")
                return jsonify({"ok": True})

            # kind == "ten": RETRYでメインループに伝えて恥ずかしがらせる
            from core.context_provider import set_last_proc_result
            from core.input_builder import build_input_segments

            retry_text = "マスターがきりたんを10回突っつきました。恥ずかしがって反応してください。"
            raw_result = {"event": "kiritan_tap_10", "message": retry_text}
            set_last_proc_result("マスターがきりたんを10回突っついた。")
            retry_payload = build_input_segments("main", retry_text, is_retry=True)
            increment_stat("retry_inputs")
            send_event("retry_input", {
                "retry_index": 1,
                "retry_total": 1,
                "retry_content": retry_payload["text"],
                "global_retry_id": get_next_global_retry_id() if callable(get_next_global_retry_id) else 0,
                "conversation_retry_id": get_next_conversation_retry_id() if callable(get_next_conversation_retry_id) else 0,
                "raw_result": raw_result,
                "raw_result_source": "TAP-EVENT",
            })
            handle_user_input_v2(
                retry_text,
                is_retry=True,
                retry_count=1,
                max_retries=MAX_RETRIES,
                user_id=session.get("user_id"),
                client_id=data.get("client_id"),
            )
            return jsonify({"ok": True})
        except Exception as e:
            app.logger.exception("/tap_event error")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/spotify_reset", methods=["POST"], endpoint="spotify_reset")
    @login_required
    def spotify_reset_route():
        from core.spotify_handler import play_silent_track

        try:
            if play_silent_track():
                return jsonify({"ok": True, "message": "接続をリセットしました"})
            return jsonify({"ok": False, "error": "再生に失敗しました"}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/youtube_close", methods=["POST"], endpoint="youtube_close")
    @login_required
    def youtube_close_route():
        from core.youtube_state import clear_youtube_state

        try:
            clear_youtube_state()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/youtube_select", methods=["POST"], endpoint="youtube_select")
    @login_required
    def youtube_select_route():
        from core.youtube_state import get_youtube_state, activate_youtube_candidate

        try:
            data = request.get_json(force=True) or {}
            selected_index = int(data.get("selected_index", 0))
            youtube_state = get_youtube_state()
            candidates = youtube_state.get("candidates") or []
            selected = activate_youtube_candidate(
                selected_index,
                selection_source="tap",
                reason=f"候補 {selected_index} をユーザーがタップで選択。",
                autoplay=True,
                muted=False,
            )
            if not selected:
                return jsonify({"ok": False, "error": "候補の選択に失敗しました"}), 400

            if get_setting("youtube.send_selection_to_main_loop", True):
                run_youtube_selection_auto_retry(
                    youtube_state,
                    candidates,
                    selected,
                    selected_index,
                    selection_source="tap",
                    user_id=session.get("user_id"),
                    client_id=data.get("client_id"),
                )
            return jsonify({"ok": True, "selected": selected})
        except Exception as e:
            app.logger.exception("/youtube_select error")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/speedtest/ping", methods=["GET"], endpoint="api_speedtest_ping")
    @login_required
    def api_speedtest_ping_route():
        return jsonify({"ok": True, "timestamp": time.time()})

    @bp.route("/api/speedtest/download", methods=["GET"], endpoint="api_speedtest_download")
    @login_required
    def api_speedtest_download_route():
        size_kb = request.args.get("size", default=1024, type=int)
        if size_kb > 10240:
            size_kb = 10240
        data_size = size_kb * 1024
        random_data = "".join(random.choices(string.ascii_letters + string.digits, k=data_size))
        app.logger.info(f"[SpeedTest] Download test: {size_kb}KB")
        return Response(random_data, mimetype="text/plain")

    return bp

