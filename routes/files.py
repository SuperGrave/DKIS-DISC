from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from core.file_manager import (
    get_file_tree,
    list_directory,
    read_file,
    write_file,
    delete_file,
    rename_file,
    create_directory,
    move_file,
    copy_file,
    FileManagerError,
)


def create_files_blueprint(deps: dict) -> Blueprint:
    bp = Blueprint("file_routes", __name__)
    role_required = deps["role_required"]
    app = deps["app"]

    @bp.route("/api/files/tree", methods=["GET"], endpoint="api_get_file_tree")
    @role_required("operator")
    def api_get_file_tree_route():
        try:
            tree = get_file_tree()
            return jsonify({"ok": True, "tree": tree})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in get_tree: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/list", methods=["GET"], endpoint="api_list_directory")
    @role_required("operator")
    def api_list_directory_route():
        dir_path = request.args.get("path", "dist")
        try:
            items = list_directory(dir_path)
            return jsonify({"ok": True, "items": items, "path": dir_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in list: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/read", methods=["GET"], endpoint="api_read_file")
    @role_required("operator")
    def api_read_file_route():
        file_path = request.args.get("path")
        encoding = request.args.get("encoding", "auto")
        if not file_path:
            return jsonify({"ok": False, "error": "Missing path parameter"}), 400

        try:
            content, detected_encoding = read_file(file_path, encoding)
            return jsonify({
                "ok": True,
                "content": content,
                "encoding": detected_encoding,
                "path": file_path,
            })
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in read: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/write", methods=["POST"], endpoint="api_write_file")
    @role_required("operator")
    def api_write_file_route():
        data = request.get_json(force=True) or {}
        file_path = data.get("path")
        content = data.get("content", "")
        encoding = data.get("encoding", "utf-8")
        if not file_path:
            return jsonify({"ok": False, "error": "Missing path parameter"}), 400

        try:
            write_file(file_path, content, encoding)
            return jsonify({"ok": True, "path": file_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in write: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/upload", methods=["POST"], endpoint="api_upload_file")
    @role_required("operator")
    def api_upload_file_route():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file provided"}), 400

        file = request.files["file"]
        target_path = request.form.get("path", "dist")
        if file.filename == "":
            return jsonify({"ok": False, "error": "No file selected"}), 400

        try:
            filename = secure_filename(file.filename)
            full_path = f"{target_path}/{filename}" if not target_path.endswith("/") else f"{target_path}{filename}"
            content = file.read()
            from core.file_manager import validate_path

            validated_path = validate_path(full_path)
            validated_path.parent.mkdir(parents=True, exist_ok=True)
            with open(validated_path, "wb") as f:
                f.write(content)

            app.logger.info(f"[FileManager] Uploaded file: {full_path} ({len(content)} bytes)")
            return jsonify({"ok": True, "path": full_path, "size": len(content)})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in upload: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/download", methods=["GET"], endpoint="api_download_file")
    @role_required("operator")
    def api_download_file_route():
        file_path = request.args.get("path")
        if not file_path:
            return jsonify({"ok": False, "error": "Missing path parameter"}), 400

        try:
            from core.file_manager import validate_path

            validated_path = validate_path(file_path)
            if not validated_path.exists() or not validated_path.is_file():
                return jsonify({"ok": False, "error": "File not found"}), 404
            return send_file(validated_path, as_attachment=True, download_name=validated_path.name)
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in download: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/delete", methods=["DELETE"], endpoint="api_delete_file")
    @role_required("operator")
    def api_delete_file_route():
        data = request.get_json(force=True) or {}
        file_path = data.get("path")
        if not file_path:
            return jsonify({"ok": False, "error": "Missing path parameter"}), 400

        try:
            delete_file(file_path)
            app.logger.info(f"[FileManager] Deleted: {file_path}")
            return jsonify({"ok": True, "path": file_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in delete: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/rename", methods=["POST"], endpoint="api_rename_file")
    @role_required("operator")
    def api_rename_file_route():
        data = request.get_json(force=True) or {}
        old_path = data.get("path")
        new_name = data.get("new_name")
        if not old_path or not new_name:
            return jsonify({"ok": False, "error": "Missing parameters"}), 400

        try:
            new_path = rename_file(old_path, new_name)
            app.logger.info(f"[FileManager] Renamed: {old_path} -> {new_path}")
            return jsonify({"ok": True, "old_path": old_path, "new_path": new_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in rename: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/mkdir", methods=["POST"], endpoint="api_create_directory")
    @role_required("operator")
    def api_create_directory_route():
        data = request.get_json(force=True) or {}
        dir_path = data.get("path")
        if not dir_path:
            return jsonify({"ok": False, "error": "Missing path parameter"}), 400

        try:
            create_directory(dir_path)
            app.logger.info(f"[FileManager] Created directory: {dir_path}")
            return jsonify({"ok": True, "path": dir_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in mkdir: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/move", methods=["POST"], endpoint="api_move_file")
    @role_required("operator")
    def api_move_file_route():
        data = request.get_json(force=True) or {}
        src_path = data.get("from")
        dst_path = data.get("to")
        if not src_path or not dst_path:
            return jsonify({"ok": False, "error": "Missing parameters"}), 400

        try:
            new_path = move_file(src_path, dst_path)
            app.logger.info(f"[FileManager] Moved: {src_path} -> {new_path}")
            return jsonify({"ok": True, "from": src_path, "to": new_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in move: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    @bp.route("/api/files/copy", methods=["POST"], endpoint="api_copy_file")
    @role_required("operator")
    def api_copy_file_route():
        data = request.get_json(force=True) or {}
        src_path = data.get("from")
        dst_path = data.get("to")
        if not src_path or not dst_path:
            return jsonify({"ok": False, "error": "Missing parameters"}), 400

        try:
            new_path = copy_file(src_path, dst_path)
            app.logger.info(f"[FileManager] Copied: {src_path} -> {new_path}")
            return jsonify({"ok": True, "from": src_path, "to": new_path})
        except FileManagerError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            app.logger.error(f"[FileManager] Unexpected error in copy: {e}")
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    return bp

