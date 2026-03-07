import json
import time
import datetime
import os
from flask import Blueprint, request, jsonify, send_file


def create_settings_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("settings_routes", __name__)
    app = deps["app"]
    role_required = deps["role_required"]
    send_event = deps["send_event"]

    @bp.route("/api/settings", methods=["GET"], endpoint="api_get_settings")
    @role_required("operator")
    def api_get_settings_route():
        from core.settings_manager import get_current_settings

        try:
            settings = get_current_settings()
            return jsonify({"ok": True, "settings": settings})
        except Exception as e:
            app.logger.error(f"[Settings] 設定取得エラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/settings", methods=["POST"], endpoint="api_update_settings")
    @role_required("operator")
    def api_update_settings_route():
        from core.settings_manager import update_settings, get_current_settings, get_setting as get_setting_for_diff

        data = request.get_json(force=True) or {}
        new_settings = data.get("settings")
        if not new_settings:
            return jsonify({"ok": False, "error": "設定データが空です"}), 400

        try:
            old_tts_enabled = bool(get_setting_for_diff("tts.enabled", True))
            success = update_settings(new_settings)
            if success:
                app.logger.info("[Settings] 設定を更新しました")
                current_settings = get_current_settings()
                app.logger.info("[Settings] settings_updated イベントを送信します")
                send_event("settings_updated", {
                    "message": "サーバー設定が更新されました",
                    "timestamp": time.time(),
                    "settings": current_settings,
                })
                app.logger.info("[Settings] settings_updated イベントを送信しました")

                try:
                    new_tts_enabled = bool(current_settings.get("tts", {}).get("enabled", True))
                    if new_tts_enabled != old_tts_enabled:
                        app.logger.info(f"[Settings] TTS有効状態が変更されました: {old_tts_enabled} -> {new_tts_enabled}")
                        send_event("tts_enabled_changed", {"enabled": new_tts_enabled})
                except Exception as e_inner:
                    app.logger.error(f"[Settings] TTS有効状態変更イベント送信エラー: {e_inner}")

                return jsonify({"ok": True, "message": "設定を更新しました", "settings": current_settings})
            return jsonify({"ok": False, "error": "設定の保存に失敗しました"}), 500
        except Exception as e:
            app.logger.error(f"[Settings] 設定更新エラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/settings/reset", methods=["POST"], endpoint="api_reset_settings")
    @role_required("operator")
    def api_reset_settings_route():
        from core.settings_manager import reset_to_default

        try:
            success = reset_to_default()
            if success:
                app.logger.info("[Settings] 設定をデフォルトに戻しました")
                send_event("settings_reset", {
                    "message": "設定をデフォルトに戻しました",
                    "timestamp": time.time(),
                })
                return jsonify({"ok": True, "message": "設定をデフォルトに戻しました"})
            return jsonify({"ok": False, "error": "設定のリセットに失敗しました"}), 500
        except Exception as e:
            app.logger.error(f"[Settings] 設定リセットエラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/settings/prompts/reset", methods=["POST"], endpoint="api_reset_single_prompt")
    @role_required("operator")
    def api_reset_single_prompt_route():
        from core.settings_manager import get_current_settings, get_default_settings, update_settings

        data = request.get_json(silent=True) or {}
        prompt_type = (data.get("prompt_type") or "").strip()

        key_map = {
            "main": "main",
            "chat_only": "chat_only",
            "search": "search_summary",
            "news": "news_summary",
            "text": "text_summary",
            "webpage": "webpage_summary",
            "webpage_summary": "webpage_summary",
            "weather_jma_id": "weather_jma_id",
            "weather_coordinates": "weather_coordinates",
            "search_summary": "search_summary",
            "news_summary": "news_summary",
            "text_summary": "text_summary",
            "weather_location": "weather_location",
            "weather_summary": "weather_summary",
        }
        if prompt_type not in key_map:
            return jsonify({"ok": False, "error": f"invalid prompt_type: {prompt_type}"}), 400

        settings_key = key_map[prompt_type]
        try:
            defaults = get_default_settings()
            default_prompts = defaults.get("system_prompts") or {}
            default_value = default_prompts.get(settings_key)
            if not isinstance(default_value, str) or not default_value.strip():
                return jsonify({"ok": False, "error": f"default prompt is empty: {settings_key}"}), 500

            current = get_current_settings()
            new_settings = current.copy()
            sp = (new_settings.get("system_prompts") or {}).copy()
            sp[settings_key] = default_value
            new_settings["system_prompts"] = sp

            ok = update_settings(new_settings)
            if not ok:
                return jsonify({"ok": False, "error": "設定の保存に失敗しました"}), 500

            latest = get_current_settings()
            send_event("settings_updated", {
                "message": f"プロンプトをデフォルトに戻しました: {settings_key}",
                "timestamp": time.time(),
                "settings": latest,
            })
            return jsonify({
                "ok": True,
                "message": "プロンプトをデフォルトに戻しました",
                "prompt_type": prompt_type,
                "settings_key": settings_key,
            })
        except Exception as e:
            app.logger.error(f"[Settings] prompt reset error ({prompt_type}): {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/settings/download", methods=["GET"], endpoint="api_download_settings")
    @role_required("operator")
    def api_download_settings_route():
        from core.settings_manager import SETTINGS_FILE

        try:
            if not SETTINGS_FILE.exists():
                return jsonify({"ok": False, "error": "設定ファイルが存在しません"}), 404
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            download_name = f"dkis_settings_{timestamp}.json"
            return send_file(
                SETTINGS_FILE,
                as_attachment=True,
                download_name=download_name,
                mimetype="application/json",
            )
        except Exception as e:
            app.logger.error(f"[Settings] 設定ダウンロードエラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/settings/upload", methods=["POST"], endpoint="api_upload_settings")
    @role_required("operator")
    def api_upload_settings_route():
        from core.settings_manager import update_settings

        if "file" not in request.files:
            return jsonify({"ok": False, "error": "ファイルが指定されていません"}), 400
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"ok": False, "error": "ファイルが選択されていません"}), 400

        try:
            content = file.read().decode("utf-8")
            settings = json.loads(content)
            success = update_settings(settings)
            if success:
                app.logger.info(f"[Settings] 設定ファイルをアップロードしました: {file.filename}")
                send_event("settings_uploaded", {
                    "message": "設定ファイルがアップロードされました",
                    "timestamp": time.time(),
                    "filename": file.filename,
                })
                return jsonify({"ok": True, "message": "設定ファイルをアップロードしました"})
            return jsonify({"ok": False, "error": "設定の保存に失敗しました"}), 500
        except json.JSONDecodeError as e:
            app.logger.error(f"[Settings] JSONパースエラー: {e}")
            return jsonify({"ok": False, "error": "JSONファイルの形式が不正です"}), 400
        except Exception as e:
            app.logger.error(f"[Settings] 設定アップロードエラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    return bp

