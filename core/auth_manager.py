# core/auth_manager.py
"""
認証管理システム（ハッシュ化版）

ユーザー登録、ログイン認証を管理
パスワード・マスターキーはSHA-256でハッシュ化して保存
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Tuple
import threading

# ユーザーデータファイルのパス（プロジェクトルート）
USERS_FILE = Path("users.json")

# メモリ上のユーザーデータ
_users_data: Dict[str, Any] = {}
_users_lock = threading.Lock()

def _hash_sha256(text: str) -> str:
    """
    テキストをSHA-256でハッシュ化
    ※このプロジェクトの現行運用（暫定）:
      - users.json には「平文」の password / master_keys を保存する
      - クライアントは送信時に SHA-256 を計算して password_hash / master_key_hash を送る
      - サーバーは users.json の平文を SHA-256 して照合する（平文はネットに流さない）
    ⚠️ 注意: これは「送られてきたハッシュがパスワードの代用品（リプレイ可能）」になり得る方式。
      公開運用では bcrypt/argon2 等＋HTTPS を推奨。
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def load_users() -> Dict[str, Any]:
    """
    ユーザーデータをファイルから読み込む
    ファイルが存在しない場合は初期データを作成
    """
    global _users_data
    
    need_save = False
    
    with _users_lock:
        if USERS_FILE.exists():
            try:
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    _users_data = json.load(f)
                print(f"[Auth] ユーザーデータを読み込みました: {len(_users_data.get('users', {}))} ユーザー")
            except Exception as e:
                print(f"[Auth] ユーザーデータ読み込みエラー: {e}")
                _users_data = _create_initial_data()
                need_save = True
        else:
            # 初回起動時は初期データを作成
            print("[Auth] ユーザーデータファイルが存在しません。初期データを作成します。")
            _users_data = _create_initial_data()
            need_save = True
    
    # ロックの外でファイル保存（初回起動時のみ）
    if need_save:
        save_users()
    
    result = _users_data.copy()
    return result

def _create_initial_data() -> Dict[str, Any]:
    """
    初期ユーザーデータを作成
    ※暫定運用: マスターキー/パスワードは users.json に平文保存する
    """
    # 初期マスターキー（要望どおり）
    default_master_key_plain = "ilovekiritan"

    # 初期ユーザー（要望どおり）
    default_user_id = "kogasachan"
    default_password_plain = "114514"

    return {
        "master_keys": {
            "operator": default_master_key_plain,
            "member": default_master_key_plain
        },
        "users": {
            default_user_id: {
                "password": default_password_plain,
                "created_at": 0,
                "role": "operator"
            }
        }
    }

def save_users() -> bool:
    """ユーザーデータをファイルに保存"""
    try:
        with _users_lock:
            data_to_save = _users_data.copy()
        
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        
        print(f"[Auth] ユーザーデータを保存しました")
        return True
    except Exception as e:
        print(f"[Auth] ユーザーデータ保存エラー: {e}")
        return False

def verify_master_key(master_key_hash: str, role: str = "operator") -> bool:
    """
    マスターキーを検証
    クライアント側で既にSHA-256ハッシュ化されている前提
    ※ 認証のたびにファイルから最新のマスターキーを読み込む
    
    Args:
        master_key_hash: マスターキー（クライアント側でハッシュ化済み）
        role: 権限レベル（"operator" または "member"）
    
    Returns:
        認証成功/失敗
    """
    # ファイルから最新のマスターキーを読み込む
    try:
        if USERS_FILE.exists():
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 新しい構造と古い構造の両方に対応
                if "master_keys" in data:
                    stored_val = data["master_keys"].get(role, "")
                else:
                    # 古い構造の場合（後方互換性）
                    stored_val = data.get("master_key", "")

                # stored_val が平文ならサーバー側でSHA-256して照合。
                # stored_val が既にハッシュ文字列だった場合（過去データ）は、そのまま照合も許容する。
                if not isinstance(stored_val, str):
                    return False
                stored_val = stored_val.strip()
                if not stored_val:
                    return False

                # 平文想定: sha256(平文) == クライアント送信hash
                if master_key_hash == _hash_sha256(stored_val):
                    return True
                # 後方互換: stored_val が既にハッシュで保存されていた場合
                return master_key_hash == stored_val
        else:
            return False
    except Exception as e:
        print(f"[Auth] マスターキー検証エラー: {e}")
        # エラー時はメモリキャッシュで検証
        with _users_lock:
            if "master_keys" in _users_data:
                stored_val = _users_data["master_keys"].get(role, "")
            else:
                # 古い構造の場合（後方互換性）
                stored_val = _users_data.get("master_key", "")

            if not isinstance(stored_val, str):
                return False
            stored_val = stored_val.strip()
            if not stored_val:
                return False
            if master_key_hash == _hash_sha256(stored_val):
                return True
            return master_key_hash == stored_val

def create_user(user_id: str, password_hash: str, master_key_hash: str, role: str = "member") -> Tuple[bool, str]:
    """
    新規ユーザーを作成
    
    Args:
        user_id: ユーザーID
        password_hash: パスワード（クライアント側でSHA-256ハッシュ化済み）
        master_key_hash: マスターキー（クライアント側でSHA-256ハッシュ化済み）
        role: ユーザー権限（"operator" または "member"）
    
    Returns:
        (成功/失敗, メッセージ)
    """
    # ⚠️ 暫定運用:
    # このプロジェクトは users.json に平文パスワードを保存し、ログインは「平文→SHA-256」で照合する方式。
    # しかし signup API で受け取れるのは password_hash だけなので、平文を復元できず保存できない。
    # よって現状はサインアップを無効化し、ユーザー追加は users.json を直接編集する運用にする。
    return False, "サインアップは無効です。ユーザー追加は users.json を直接編集してください。"
    
    # ユーザーIDの検証
    if not user_id or len(user_id) < 3:
        return False, "ユーザーIDは3文字以上必要です"
    
    if not user_id.replace('_', '').isalnum():
        return False, "ユーザーIDは英数字とアンダースコアのみ使用できます"
    
    # 権限の検証
    if role not in ["operator", "member"]:
        return False, "権限は'operator'または'member'である必要があります"
    
    with _users_lock:
        # 既存ユーザーチェック
        if user_id in _users_data.get("users", {}):
            return False, "このユーザーIDは既に使用されています"
        
        # ユーザーデータを保存（ハッシュ値で保存）
        _users_data["users"][user_id] = {
            "password_hash": password_hash,  # SHA-256ハッシュで保存
            "created_at": __import__('time').time(),
            "role": role
        }
    
    # ファイルに保存
    if save_users():
        print(f"[Auth] 新規ユーザー作成: {user_id} (role: {role})")
        return True, "アカウントを作成しました"
    else:
        return False, "アカウントの保存に失敗しました"


def create_user_plain(user_id: str, password: str, master_key: str, role: str = "member") -> Tuple[bool, str]:
    """
    新規ユーザーを作成（サインアップ用・平文入力）。

    ※暫定運用:
      - users.json には password / master_keys を平文保存する
      - ネット越しには平文が流れるため、HTTPS前提
    """
    # マスターキーの検証（平文→hash化して既存の検証関数に流す）
    if not isinstance(master_key, str) or not master_key.strip():
        return False, "マスターキーが空です"
    if not verify_master_key(_hash_sha256(master_key.strip()), role="operator"):
        return False, "マスターキーが正しくありません"

    # ユーザーIDの検証
    if not user_id or len(user_id) < 3:
        return False, "ユーザーIDは3文字以上必要です"
    if not user_id.replace('_', '').isalnum():
        return False, "ユーザーIDは英数字とアンダースコアのみ使用できます"

    # パスワード検証（最低限）
    if not isinstance(password, str) or not password:
        return False, "パスワードが空です"
    if len(password) < 6:
        return False, "パスワードは6文字以上必要です"

    # 権限の検証
    if role not in ["operator", "member"]:
        return False, "権限は'operator'または'member'である必要があります"

    with _users_lock:
        # 既存ユーザーチェック
        users = _users_data.setdefault("users", {})
        if user_id in users:
            return False, "このユーザーIDは既に使用されています"

        users[user_id] = {
            "password": password,  # 平文保存（暫定）
            "created_at": __import__('time').time(),
            "role": role
        }

    if save_users():
        print(f"[Auth] 新規ユーザー作成: {user_id} (role: {role})")
        return True, "アカウントを作成しました"
    return False, "アカウントの保存に失敗しました"

def verify_user(user_id: str, password_hash: str) -> bool:
    """
    ユーザー認証
    ※ 認証のたびにファイルから最新のユーザー情報を読み込む
    
    Args:
        user_id: ユーザーID
        password_hash: パスワード（クライアント側でSHA-256ハッシュ化済み）
    
    Returns:
        認証成功/失敗
    """
    # ファイルから最新のユーザー情報を読み込む
    try:
        if USERS_FILE.exists():
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                users = data.get("users", {})
                
                if user_id not in users:
                    print(f"[Auth] ログイン失敗: ユーザーが存在しません ({user_id})")
                    return False
                
                user_data = users[user_id]

                # 平文保存の想定キー
                stored_pw_plain = user_data.get("password", "")
                stored_pw_hash = user_data.get("password_hash", "")

                # 平文があるなら sha256(平文) と比較
                if isinstance(stored_pw_plain, str) and stored_pw_plain.strip():
                    if password_hash == _hash_sha256(stored_pw_plain.strip()):
                        print(f"[Auth] ログイン成功: {user_id}")
                        return True

                # 後方互換: password_hash が保存されていた場合
                if isinstance(stored_pw_hash, str) and stored_pw_hash.strip():
                    if password_hash == stored_pw_hash.strip():
                        print(f"[Auth] ログイン成功: {user_id}")
                        return True

                print(f"[Auth] ログイン失敗: パスワードが正しくありません ({user_id})")
                return False
        else:
            print(f"[Auth] ログイン失敗: ユーザーファイルが存在しません")
            return False
    except Exception as e:
        print(f"[Auth] ログイン検証エラー: {e}")
        # エラー時はメモリキャッシュで検証
        with _users_lock:
            users = _users_data.get("users", {})
            
            if user_id not in users:
                print(f"[Auth] ログイン失敗: ユーザーが存在しません ({user_id})")
                return False
            
            user_data = users[user_id]

            stored_pw_plain = user_data.get("password", "")
            stored_pw_hash = user_data.get("password_hash", "")

            if isinstance(stored_pw_plain, str) and stored_pw_plain.strip():
                if password_hash == _hash_sha256(stored_pw_plain.strip()):
                    print(f"[Auth] ログイン成功: {user_id}")
                    return True

            if isinstance(stored_pw_hash, str) and stored_pw_hash.strip():
                if password_hash == stored_pw_hash.strip():
                    print(f"[Auth] ログイン成功: {user_id}")
                    return True

            print(f"[Auth] ログイン失敗: パスワードが正しくありません ({user_id})")
            return False

def get_user_list() -> list:
    """
    ユーザーリストを取得（管理用）
    """
    with _users_lock:
        users = _users_data.get("users", {})
        return [
            {
                "user_id": user_id,
                "created_at": data.get("created_at", 0),
                "role": data.get("role", "member")
            }
            for user_id, data in users.items()
        ]

def get_user_role(user_id: str) -> str:
    """
    ユーザーの権限を取得
    
    Args:
        user_id: ユーザーID
    
    Returns:
        ユーザーの権限（"operator" または "member"）
    """
    with _users_lock:
        user_data = _users_data.get("users", {}).get(user_id, {})
        return user_data.get("role", "member")

def has_permission(user_id: str, required_role: str) -> bool:
    """
    ユーザーが指定された権限を持っているかチェック
    
    Args:
        user_id: ユーザーID
        required_role: 必要な権限（"operator" または "member"）
    
    Returns:
        権限があるかどうか
    """
    user_role = get_user_role(user_id)
    
    # 権限レベル: operator > member
    if required_role == "operator":
        return user_role == "operator"
    elif required_role == "member":
        return user_role in ["operator", "member"]
    else:
        return False

def verify_password_reauth(user_id: str, password_hash: str) -> bool:
    """
    パスワード再認証（重要な操作前の確認）
    
    Args:
        user_id: ユーザーID
        password_hash: パスワード（クライアント側でハッシュ化済み）
    
    Returns:
        認証成功/失敗
    """
    return verify_user(user_id, password_hash)

def update_user_role(user_id: str, new_role: str, operator_user_id: str, password_hash: str = None) -> Tuple[bool, str]:
    """
    ユーザーの権限を更新（オペレーターのみ実行可能）
    
    Args:
        user_id: 権限を変更するユーザーID
        new_role: 新しい権限（"operator" または "member"）
        operator_user_id: 実行者のユーザーID（オペレーターである必要がある）
        password_hash: 実行者のパスワード（再認証用、オプション）
    
    Returns:
        (成功/失敗, メッセージ)
    """
    # 実行者がオペレーターかチェック
    if not has_permission(operator_user_id, "operator"):
        return False, "権限がありません（オペレーター権限が必要です）"
    
    # パスワード再認証（オプション）
    if password_hash and not verify_password_reauth(operator_user_id, password_hash):
        return False, "パスワードが正しくありません"
    
    # 権限の検証
    if new_role not in ["operator", "member"]:
        return False, "権限は'operator'または'member'である必要があります"
    
    with _users_lock:
        if user_id not in _users_data.get("users", {}):
            return False, "ユーザーが見つかりません"
        
        # 権限を更新
        _users_data["users"][user_id]["role"] = new_role
    
    # ファイルに保存
    if save_users():
        print(f"[Auth] ユーザー権限更新: {user_id} -> {new_role} (by {operator_user_id})")
        return True, f"ユーザー {user_id} の権限を {new_role} に変更しました"
    else:
        return False, "権限の保存に失敗しました"

# 起動時にユーザーデータを読み込む
print("[Auth] 認証管理システムを初期化しています...")
load_users()
print("[Auth] 認証管理システムの初期化が完了しました")
print(f"[Auth] 📂 ユーザーデータファイル: {USERS_FILE.absolute()}")
print(f"[Auth] 🔒 認証方式: users.json は平文保存 / 通信はSHA-256で照合（暫定運用）")
