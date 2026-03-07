"""
ファイルマネージャー機能
distフォルダ内のファイル操作を安全に行う
"""

import os
import json
from pathlib import Path
from datetime import datetime
import chardet
import shutil

# distフォルダのパス
DIST_ROOT = Path("./dist").resolve()

class FileManagerError(Exception):
    """ファイルマネージャー関連のエラー"""
    pass

def validate_path(user_path):
    """
    パストラバーサル攻撃を防ぐ
    user_path: ユーザーから受け取った相対パス（例: "documents/note.txt"）
    戻り値: 検証済みの絶対Path、またはエラー時はNone
    """
    try:
        # dist/を起点とした絶対パスを生成
        if user_path.startswith("dist/"):
            user_path = user_path[5:]  # "dist/"を除去
        elif user_path.startswith("dist"):
            user_path = user_path[4:].lstrip("/\\")
        
        requested_path = (DIST_ROOT / user_path).resolve()
        
        # dist/配下かチェック
        if not str(requested_path).startswith(str(DIST_ROOT)):
            raise FileManagerError(f"Access denied: Path outside dist/ ({requested_path})")
        
        return requested_path
    except Exception as e:
        raise FileManagerError(f"Invalid path: {e}")

def validate_filename(filename):
    """危険なファイル名を拒否"""
    forbidden_chars = ['..', '/', '\\', '<', '>', ':', '"', '|', '?', '*']
    if any(char in filename for char in forbidden_chars):
        raise FileManagerError(f"Invalid filename: {filename}")
    
    if filename.startswith('.'):
        raise FileManagerError(f"Hidden files not allowed: {filename}")
    
    return filename

def detect_encoding(file_path):
    """ファイルのエンコーディングを自動判定"""
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(10000)  # 最初の10KBで判定
            result = chardet.detect(raw_data)
            encoding = result['encoding']
            confidence = result['confidence']
            
            # 信頼度が低い場合はUTF-8をデフォルトに
            if confidence < 0.7 or encoding is None:
                return 'utf-8'
            
            # Shift-JISの別名を統一
            if encoding.lower() in ['shift_jis', 'shift-jis', 'sjis', 'ms932']:
                return 'shift_jis'
            
            return encoding.lower()
    except Exception:
        return 'utf-8'

def get_file_info(path):
    """ファイル/フォルダの情報を取得"""
    try:
        stat = path.stat()
        
        # 基本情報
        info = {
            "name": path.name,
            "path": str(path.relative_to(DIST_ROOT.parent)),  # "dist/..."形式
            "is_directory": path.is_dir(),
            "size": stat.st_size if path.is_file() else 0,
            "size_display": format_size(stat.st_size) if path.is_file() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        }
        
        # ファイルの場合は拡張子とエンコーディングを追加
        if path.is_file():
            info["extension"] = path.suffix
            
            # テキストファイルの場合はエンコーディングを判定
            text_extensions = ['.txt', '.md', '.json', '.csv', '.log', '.py', '.js', '.html', '.css', '.xml']
            if path.suffix.lower() in text_extensions:
                info["encoding"] = detect_encoding(path)
            else:
                info["encoding"] = None
        else:
            info["extension"] = None
            info["encoding"] = None
        
        return info
    except Exception as e:
        raise FileManagerError(f"Failed to get file info: {e}")

def format_size(size_bytes):
    """バイト数を人間が読める形式に変換"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

def get_file_tree():
    """distフォルダのツリー構造を取得"""
    def build_tree(path):
        """再帰的にツリーを構築"""
        try:
            info = get_file_info(path)
            
            if path.is_dir():
                # フォルダの場合は子要素を取得
                children = []
                try:
                    for child in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                        # 隠しファイル/フォルダはスキップ
                        if child.name.startswith('.'):
                            continue
                        children.append(build_tree(child))
                except PermissionError:
                    pass  # アクセス権限がない場合はスキップ
                
                info["children"] = children
            
            return info
        except Exception as e:
            print(f"[FileManager] Error building tree for {path}: {e}")
            return None
    
    try:
        # distフォルダが存在しない場合は作成
        if not DIST_ROOT.exists():
            DIST_ROOT.mkdir(parents=True)
            print(f"[FileManager] Created dist folder: {DIST_ROOT}")
        
        tree = build_tree(DIST_ROOT)
        
        # メタデータを追加
        total_files = 0
        total_size = 0
        
        def count_files(node):
            nonlocal total_files, total_size
            if node.get("is_directory"):
                for child in node.get("children", []):
                    count_files(child)
            else:
                total_files += 1
                total_size += node.get("size", 0)
        
        if tree:
            count_files(tree)
            tree["metadata"] = {
                "total_files": total_files,
                "total_size": total_size,
                "total_size_display": format_size(total_size),
                "last_updated": datetime.now().isoformat()
            }
        
        return tree
    except Exception as e:
        raise FileManagerError(f"Failed to get file tree: {e}")

def list_directory(dir_path):
    """指定ディレクトリ内のファイル/フォルダ一覧を取得（フラット）"""
    try:
        validated_path = validate_path(dir_path)
        
        if not validated_path.exists():
            raise FileManagerError(f"Directory not found: {dir_path}")
        
        if not validated_path.is_dir():
            raise FileManagerError(f"Not a directory: {dir_path}")
        
        items = []
        for item in sorted(validated_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # 隠しファイル/フォルダはスキップ
            if item.name.startswith('.'):
                continue
            
            items.append(get_file_info(item))
        
        return items
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to list directory: {e}")

def read_file(file_path, encoding='utf-8'):
    """ファイルの内容を読み込む"""
    try:
        validated_path = validate_path(file_path)
        
        if not validated_path.exists():
            raise FileManagerError(f"File not found: {file_path}")
        
        if not validated_path.is_file():
            raise FileManagerError(f"Not a file: {file_path}")
        
        # エンコーディングが指定されていない場合は自動判定
        if encoding == 'auto':
            encoding = detect_encoding(validated_path)
        
        with open(validated_path, 'r', encoding=encoding, errors='replace') as f:
            content = f.read()
        
        return content, encoding
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to read file: {e}")

def write_file(file_path, content, encoding='utf-8'):
    """ファイルに内容を書き込む"""
    try:
        validated_path = validate_path(file_path)
        
        # 親ディレクトリが存在しない場合は作成
        validated_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(validated_path, 'w', encoding=encoding, errors='replace') as f:
            f.write(content)
        
        return True
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to write file: {e}")

def delete_file(file_path):
    """ファイル/フォルダを削除"""
    try:
        validated_path = validate_path(file_path)
        
        if not validated_path.exists():
            raise FileManagerError(f"File not found: {file_path}")
        
        if validated_path.is_dir():
            shutil.rmtree(validated_path)
        else:
            validated_path.unlink()
        
        return True
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to delete file: {e}")

def rename_file(old_path, new_name):
    """ファイル/フォルダの名前を変更"""
    try:
        validated_old_path = validate_path(old_path)
        validate_filename(new_name)
        
        if not validated_old_path.exists():
            raise FileManagerError(f"File not found: {old_path}")
        
        new_path = validated_old_path.parent / new_name
        
        # 同名のファイルが既に存在する場合はエラー
        if new_path.exists():
            raise FileManagerError(f"File already exists: {new_name}")
        
        validated_old_path.rename(new_path)
        
        return str(new_path.relative_to(DIST_ROOT.parent))
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to rename file: {e}")

def create_directory(dir_path):
    """フォルダを作成"""
    try:
        validated_path = validate_path(dir_path)
        
        if validated_path.exists():
            raise FileManagerError(f"Directory already exists: {dir_path}")
        
        validated_path.mkdir(parents=True, exist_ok=False)
        
        return True
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to create directory: {e}")

def move_file(src_path, dst_path):
    """ファイル/フォルダを移動"""
    try:
        validated_src = validate_path(src_path)
        validated_dst = validate_path(dst_path)
        
        if not validated_src.exists():
            raise FileManagerError(f"Source not found: {src_path}")
        
        # 移動先が既に存在する場合はエラー
        if validated_dst.exists():
            raise FileManagerError(f"Destination already exists: {dst_path}")
        
        # 親ディレクトリが存在しない場合は作成
        validated_dst.parent.mkdir(parents=True, exist_ok=True)
        
        shutil.move(str(validated_src), str(validated_dst))
        
        return str(validated_dst.relative_to(DIST_ROOT.parent))
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to move file: {e}")

def copy_file(src_path, dst_path):
    """ファイル/フォルダをコピー"""
    try:
        validated_src = validate_path(src_path)
        validated_dst = validate_path(dst_path)
        
        if not validated_src.exists():
            raise FileManagerError(f"Source not found: {src_path}")
        
        # コピー先が既に存在する場合はエラー
        if validated_dst.exists():
            raise FileManagerError(f"Destination already exists: {dst_path}")
        
        # 親ディレクトリが存在しない場合は作成
        validated_dst.parent.mkdir(parents=True, exist_ok=True)
        
        if validated_src.is_dir():
            shutil.copytree(str(validated_src), str(validated_dst))
        else:
            shutil.copy2(str(validated_src), str(validated_dst))
        
        return str(validated_dst.relative_to(DIST_ROOT.parent))
    except FileManagerError:
        raise
    except Exception as e:
        raise FileManagerError(f"Failed to copy file: {e}")

