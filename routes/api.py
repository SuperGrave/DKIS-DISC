from threading import Thread
import random
import string
import time
from flask import Blueprint, request, jsonify, send_file, session, Response


def create_api_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("api_routes", __name__)
    app = deps["app"]
    login_required = deps["login_required"]
    role_required = deps["role_required"]
    FRONT_INDEX_PATH = deps["FRONT_INDEX_PATH"]
    MAX_RETRIES = deps["MAX_RETRIES"]
    YELLOW_BRIGHT = deps["YELLOW_BRIGHT"]
    RESET = deps["RESET"]
    get_setting = deps["get_setting"]
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

