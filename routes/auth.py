from flask import Blueprint, request, jsonify, session, redirect, url_for, send_file


def create_auth_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("auth_routes", __name__)
    auth_manager = deps["auth_manager"]
    app = deps["app"]
    login_required = deps["login_required"]
    role_required = deps["role_required"]

    @bp.route("/login", methods=["GET"], endpoint="login_page")
    def login_page_route():
        if "user_id" in session:
            return redirect(url_for("api_routes.index"))
        return send_file("templates/login.html")

    @bp.route("/api/auth/login", methods=["POST"], endpoint="api_login")
    def api_login_route():
        data = request.get_json(force=True) or {}
        user_id = data.get("user_id", "").strip()
        password_hash = data.get("password_hash", "")

        if not user_id or not password_hash:
            return jsonify({"ok": False, "error": "ユーザーIDとパスワードを入力してください"}), 400

        if auth_manager.verify_user(user_id, password_hash):
            session["user_id"] = user_id
            session.permanent = True
            app.logger.info(f"[Auth] ログイン成功: {user_id}")
            return jsonify({"ok": True, "message": "ログインに成功しました"})

        app.logger.warning(f"[Auth] ログイン失敗: {user_id}")
        return jsonify({"ok": False, "error": "ユーザーIDまたはパスワードが正しくありません"}), 401

    @bp.route("/api/auth/signup", methods=["POST"], endpoint="api_signup")
    def api_signup_route():
        data = request.get_json(force=True) or {}
        user_id = data.get("user_id", "").strip()
        password_plain = data.get("password", "")
        master_key_plain = data.get("master_key", "")

        if not user_id or not password_plain or not master_key_plain:
            return jsonify({"ok": False, "error": "すべての項目を入力してください"}), 400

        success, message = auth_manager.create_user_plain(user_id, password_plain, master_key_plain)
        if success:
            app.logger.info(f"[Auth] 新規ユーザー作成: {user_id}")
            return jsonify({"ok": True, "message": message})

        app.logger.warning(f"[Auth] ユーザー作成失敗: {user_id} - {message}")
        return jsonify({"ok": False, "error": message}), 400

    @bp.route("/api/auth/logout", methods=["POST"], endpoint="api_logout")
    @login_required
    def api_logout_route():
        user_id = session.get("user_id", "unknown")
        session.clear()
        app.logger.info(f"[Auth] ログアウト: {user_id}")
        return jsonify({"ok": True, "message": "ログアウトしました"})

    @bp.route("/api/users/list", methods=["GET"], endpoint="api_get_users_list")
    @role_required("operator")
    def api_get_users_list_route():
        from core.auth_manager import get_user_list

        try:
            users = get_user_list()
            return jsonify({"ok": True, "users": users})
        except Exception as e:
            app.logger.error(f"[Auth] ユーザーリスト取得エラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/users/update_role", methods=["POST"], endpoint="api_update_user_role")
    @role_required("operator")
    def api_update_user_role_route():
        from core.auth_manager import update_user_role

        data = request.get_json(force=True) or {}
        target_user_id = data.get("user_id")
        new_role = data.get("role")
        password_hash = data.get("password_hash")
        operator_user_id = session.get("user_id")

        if not target_user_id or not new_role:
            return jsonify({"ok": False, "error": "user_id と role が必要です"}), 400

        try:
            success, message = update_user_role(target_user_id, new_role, operator_user_id, password_hash)
            if success:
                return jsonify({"ok": True, "message": message})
            return jsonify({"ok": False, "error": message}), 400
        except Exception as e:
            app.logger.error(f"[Auth] ユーザー権限更新エラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    @bp.route("/api/users/my_role", methods=["GET"], endpoint="api_get_my_role")
    @login_required
    def api_get_my_role_route():
        from core.auth_manager import get_user_role

        try:
            user_id = session.get("user_id")
            role = get_user_role(user_id)
            return jsonify({"ok": True, "role": role})
        except Exception as e:
            app.logger.error(f"[Auth] ユーザー権限取得エラー: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    return bp

