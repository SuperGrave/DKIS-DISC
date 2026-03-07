import json
import queue
import time
from flask import Blueprint, request, jsonify, Response, session


def create_sse_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("sse_routes", __name__)
    app = deps["app"]
    login_required = deps["login_required"]
    send_event = deps["send_event"]
    send_clients_list = deps["send_clients_list"]
    send_system_status = deps["send_system_status"]
    _build_system_status_payload = deps["_build_system_status_payload"]
    increment_stat = deps["increment_stat"]
    event_queues = deps["event_queues"]
    client_info_list = deps["client_info_list"]
    event_queues_lock = deps["event_queues_lock"]

    @bp.route("/events", methods=["GET"], endpoint="stream_events")
    @login_required
    def stream_events_route():
        client_queue = queue.Queue(maxsize=100)
        client_ip = request.remote_addr
        client_port = request.environ.get("REMOTE_PORT", "unknown")
        user_agent = request.headers.get("User-Agent", "Unknown")

        def parse_user_agent(ua):
            import re

            if "Edg" in ua:
                browser = "Edge"
            elif "Chrome" in ua and "Edg" not in ua:
                browser = "Chrome"
            elif "Firefox" in ua:
                browser = "Firefox"
            elif "Safari" in ua and "Chrome" not in ua:
                browser = "Safari"
            else:
                browser = "Unknown"

            if "Windows NT 10.0" in ua:
                os = "Windows 10/11"
            elif "Windows NT 6.3" in ua:
                os = "Windows 8.1"
            elif "Windows NT 6.2" in ua:
                os = "Windows 8"
            elif "Windows NT 6.1" in ua:
                os = "Windows 7"
            elif "Mac OS X" in ua:
                if "iPhone" in ua or "iPad" in ua:
                    ios_match = re.search(r"OS (\d+)[_\.](\d+)", ua)
                    if ios_match:
                        os = f"iOS {ios_match.group(1)}.{ios_match.group(2)}"
                    else:
                        os = "iOS"
                else:
                    mac_match = re.search(r"Mac OS X (\d+)[_\.](\d+)", ua)
                    if mac_match:
                        os = f"macOS {mac_match.group(1)}.{mac_match.group(2)}"
                    else:
                        os = "macOS"
            elif "Android" in ua:
                android_match = re.search(r"Android (\d+(?:\.\d+)?)", ua)
                if android_match:
                    os = f"Android {android_match.group(1)}"
                else:
                    os = "Android"
            elif "Linux" in ua:
                os = "Linux"
            else:
                os = "Unknown"

            if "Mobile" in ua or "iPhone" in ua or "Android" in ua:
                if "iPad" in ua or "Tablet" in ua:
                    device_type = "タブレット"
                else:
                    device_type = "スマートフォン"
            else:
                device_type = "PC"
            return browser, os, device_type

        browser, os, device_type = parse_user_agent(user_agent)
        import random

        client_id = f"{time.time()}-{random.randint(1000, 9999)}"
        user_id = session.get("user_id", "ゲスト")

        app.logger.info(f"[SSE] セッション情報: user_id='{user_id}', session_id='{session.get('_id', 'N/A')}'")
        app.logger.info(f"[SSE] セッション全体: {dict(session)}")

        client_info = {
            "id": client_id,
            "ip": client_ip,
            "port": client_port,
            "browser": browser,
            "os": os,
            "device_type": device_type,
            "connected_at": __import__("datetime").datetime.now().strftime("%H:%M:%S"),
            "user_id": user_id,
        }
        client_info_str = f"{client_ip}:{client_port} ({device_type}/{os}/{browser}) - ユーザー: {user_id}"

        with event_queues_lock:
            event_queues.append(client_queue)
            client_info_list.append(client_info)
            connection_count = len(event_queues)

        app.logger.info(f"[SSE] ✅ クライアント接続: {client_info_str}（全{connection_count}接続）")

        try:
            welcome_payload = {"type": "client_id", "client_id": client_id}
            client_queue.put(("client_id", json.dumps(welcome_payload)), block=False)
        except Exception:
            pass

        try:
            from core.settings_manager import get_current_settings

            current_settings = get_current_settings()
            settings_payload = {"type": "settings_sync", "settings": current_settings}
            client_queue.put(("settings_sync", json.dumps(settings_payload)), block=False)
        except Exception as e:
            app.logger.warning(f"[Settings] 設定同期の送信に失敗: {e}")

        send_clients_list()
        send_system_status()

        def event_stream():
            try:
                while True:
                    try:
                        ev, payload = client_queue.get(timeout=10)
                        yield f"event: {ev}\n"
                        yield f"data: {payload}\n\n"
                    except queue.Empty:
                        yield ": keep-alive\n\n"
            finally:
                with event_queues_lock:
                    if client_queue in event_queues:
                        index = event_queues.index(client_queue)
                        event_queues.remove(client_queue)
                        if index < len(client_info_list):
                            removed_client = client_info_list.pop(index)
                            app.logger.info(f"[SSE] 削除されたクライアント情報: {removed_client}")
                    remaining_count = len(event_queues)
                app.logger.info(f"[SSE] ❌ クライアント切断: {client_info_str}（残り{remaining_count}接続）")
                send_clients_list()

        return Response(event_stream(), content_type="text/event-stream")

    @bp.route("/notify/reply", methods=["POST"], endpoint="notify_reply_route")
    def notify_reply_route():
        payload = request.get_json(silent=True) or {}
        uid = payload.get("utter_id")
        if uid is None:
            app.logger.warning("[SSE] reply missing utter_id; dropping")
            return jsonify({"ok": False, "error": "missing utter_id"}), 400

        raw_text = payload.get("text")
        if raw_text is None:
            text = ""
        elif isinstance(raw_text, str):
            text = raw_text.strip()
        else:
            text = str(raw_text).strip()
        if not text:
            app.logger.warning("[SSE] reply missing text; dropping")
            return jsonify({"ok": False, "error": "missing text"}), 400

        ai_raw = payload.get("ai_raw", "")
        processing_time = payload.get("processing_time", 0.0)
        token_usage = payload.get("token_usage")
        reply_data = {"utter_id": uid, "text": text}
        if ai_raw:
            reply_data["ai_raw"] = ai_raw
        if processing_time:
            reply_data["processing_time"] = processing_time
        if token_usage and isinstance(token_usage, dict):
            reply_data["token_usage"] = token_usage
        send_event("reply", reply_data)
        return jsonify({"ok": True})

    @bp.route("/notify/synth_start", methods=["POST"], endpoint="notify_synth_start_handler")
    def notify_synth_start_handler_route():
        data = request.get_json(silent=True) or {}
        uid = data.get("utter_id")
        seg_idx = data.get("segment_index")
        text = data.get("text", "")
        if not uid:
            return jsonify({"ok": False, "error": "missing utter_id"}), 400
        send_event("synth_start", {"utter_id": uid, "segment_index": seg_idx, "text": text})
        return jsonify({"ok": True})

    @bp.route("/notify/synth_done", methods=["POST"], endpoint="notify_synth_done_handler")
    def notify_synth_done_handler_route():
        data = request.get_json(silent=True) or {}
        uid = data.get("utter_id")
        seg_idx = data.get("segment_index")
        url = data.get("url", "")
        text = data.get("text", "")
        synthesis_time = data.get("synthesis_time", 0.0)
        synthesis_utter_id = data.get("synthesis_utter_id")
        if not uid or not url:
            return jsonify({"ok": False, "error": "missing data"}), 400
        event_data = {"utter_id": uid, "segment_index": seg_idx, "url": url, "text": text}
        if synthesis_time > 0 and synthesis_utter_id:
            event_data["synthesis_time"] = synthesis_time
            event_data["synthesis_utter_id"] = synthesis_utter_id
        send_event("synth_done", event_data)
        return jsonify({"ok": True})

    @bp.route("/notify/retry_input", methods=["POST"], endpoint="notify_retry_input")
    def notify_retry_input_route():
        data = request.get_json(silent=True) or {}
        retry_index = data.get("retry_index", 1)
        retry_total = data.get("retry_total", 1)
        retry_content = data.get("retry_content", "")
        raw_result = data.get("raw_result")  # AI整形前の検索結果・読み込んだページの生データなど
        raw_result_source = data.get("raw_result_source")  # READ-PAGE, SEARCH, READ-TEXT 等
        token_usage = data.get("token_usage")  # AI要約の消費トークン（検索・ニュース・READ-PAGE等）
        global_retry_id = data.get("global_retry_id", 0)
        conversation_retry_id = data.get("conversation_retry_id", 0)
        if not retry_content:
            app.logger.warning("[SSE] retry_input missing content; dropping")
            return jsonify({"ok": False, "error": "missing retry_content"}), 400

        increment_stat("retry_inputs")
        event_payload = {
            "retry_index": retry_index,
            "retry_total": retry_total,
            "retry_content": retry_content,
            "global_retry_id": global_retry_id,
            "conversation_retry_id": conversation_retry_id,
        }
        if raw_result is not None:
            event_payload["raw_result"] = raw_result
        if raw_result_source is not None:
            event_payload["raw_result_source"] = raw_result_source
        if token_usage is not None and isinstance(token_usage, dict):
            event_payload["token_usage"] = token_usage
        send_event("retry_input", event_payload)
        return jsonify({"ok": True})

    @bp.route("/notify/user_input", methods=["POST"], endpoint="notify_user_input")
    def notify_user_input_route():
        data = request.get_json(silent=True) or {}
        text = data.get("text") or ""
        mode = data.get("mode") or "main"
        if not text:
            app.logger.warning("[SSE] user_input missing text; dropping")
            return jsonify({"ok": False, "error": "missing text"}), 400

        payload = {
            "mode": mode,
            "input_type": data.get("input_type") or "UI",
            "text": text,
            "segments": data.get("segments") or [],
            "timestamp": data.get("timestamp"),
            "raw_text": data.get("raw_text"),
            "user_id": data.get("user_id"),
            "client_id": data.get("client_id"),
            "retry": bool(data.get("retry", False)),
            "retry_index": data.get("retry_index"),
            "retry_total": data.get("retry_total"),
            "global_retry_id": data.get("global_retry_id"),
            "conversation_retry_id": data.get("conversation_retry_id"),
        }
        send_event("user_input_committed", payload)
        return jsonify({"ok": True})

    @bp.route("/request_clients_list", methods=["POST"], endpoint="request_clients_list")
    @login_required
    def request_clients_list_route():
        send_clients_list()
        return jsonify({"ok": True})

    @bp.route("/request_system_status", methods=["POST"], endpoint="request_system_status")
    @login_required
    def request_system_status_route():
        payload = _build_system_status_payload(force_update=True)
        event_data = ("system_status", json.dumps(payload))
        with event_queues_lock:
            for q in event_queues:
                try:
                    q.put(event_data, block=False)
                except queue.Full:
                    pass
        return jsonify({"ok": True})

    return bp

