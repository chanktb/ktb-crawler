import os
import sys
import json
import time
import shutil
import zipfile
import getpass
import requests
import shlex
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
try:
    import paramiko
except ImportError:
    print("[LỖI] Chưa cài đặt thư viện 'paramiko'. Chạy lệnh: pip install paramiko python-dotenv")
    sys.exit(1)

# --- CẤU HÌNH ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
MAX_FILES_PER_ZIP = 300

load_dotenv()

try:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"[LỖI] Không tìm thấy file {CONFIG_FILE}.")
    sys.exit(1)

def send_telegram_message(bot_token, chat_id, message_content):
    if not bot_token or not chat_id: return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message_content}
    try:
        requests.post(url, json=payload, timeout=10)
    except: pass

def main():
    print("--- Bắt đầu quy trình KTB Crawler Uploader ---")
    
    if not os.path.isdir(OUTPUT_DIR):
        print(f"[LỖI] Không tìm thấy thư mục '{OUTPUT_DIR}'.")
        sys.exit(1)

    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    defaults = config.get("defaults", {})
    wp_author = defaults.get("wp_username", defaults.get("default_user_author", "chi"))
    remote_queue_dir = defaults.get("remote_queue_dir", "/home/khue/ktb_tmp_uploads")
    
    vps_user = os.getenv("VPS_USERNAME")
    vps_password = os.getenv("VPS_PASSWORD")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not vps_user:
        print("[LỖI] Thiếu VPS_USERNAME trong .env")
        sys.exit(1)
        
    if not vps_password:
        vps_password = getpass.getpass(f"Nhập mật khẩu VPS cho user '{vps_user}' (sẽ bị ẩn): ")
        if not vps_password:
            print("[LỖI] Mật khẩu không được để trống.")
            sys.exit(1)

    mockup_sets = config.get("mockup_sets", {})
    
    # 1. Quét output và tạo file ZIP
    print("Đang quét thư mục output và gom lô ảnh (Tối đa 300 ảnh/Zip)...")
    job_packages = []
    
    for site_name in os.listdir(OUTPUT_DIR):
        site_dir = os.path.join(OUTPUT_DIR, site_name)
        if not os.path.isdir(site_dir): continue
        
        site_config = mockup_sets.get(site_name)
        if not site_config:
            print(f"⚠️ Bỏ qua '{site_name}' vì không có cấu hình trong mockup_sets.")
            continue
            
        vps_prefix = site_config.get("vps_secret_prefix")
        wp_path = site_config.get("wp_path")
        if not vps_prefix or not wp_path:
            print(f"⚠️ Thiếu vps_secret_prefix hoặc wp_path cho '{site_name}'. Bỏ qua.")
            continue
            
        vps_host = os.getenv(f"{vps_prefix}_VPS_HOST")
        vps_port = os.getenv(f"{vps_prefix}_VPS_PORT")
        
        if not vps_host or not vps_port:
            print(f"⚠️ Lỗi: Không tìm thấy host/port cho {vps_prefix} trong .env. Bỏ qua.")
            continue
            
        vps_port = int(vps_port)
        
        # Lấy danh sách ảnh
        valid_exts = ('.jpg', '.jpeg', '.png', '.webp')
        all_images = [f for f in os.listdir(site_dir) if f.lower().endswith(valid_exts)]
        if not all_images:
            continue
            
        print(f"📦 [{site_name}] Tìm thấy {len(all_images)} ảnh.")
        
        # Chia lô 300
        chunks = [all_images[i:i + MAX_FILES_PER_ZIP] for i in range(0, len(all_images), MAX_FILES_PER_ZIP)]
        
        for idx, chunk in enumerate(chunks, 1):
            zip_filename = f"{site_name}.{wp_author}.{idx}.zip"
            zip_filepath = os.path.join(CACHE_DIR, zip_filename)
            
            print(f"   ⏳ Đang nén lô {idx}/{len(chunks)} ({len(chunk)} ảnh) -> {zip_filename}...")
            try:
                with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for img in chunk:
                        img_path = os.path.join(site_dir, img)
                        zipf.write(img_path, arcname=img)
            except Exception as e:
                print(f"   ❌ Lỗi nén file: {e}")
                continue
                
            meta_content = {
                "wp_author": wp_author,
                "wp_path": wp_path,
                "zip_filename": zip_filename,
                "prefix": site_name,
                "telegram_bot_token": telegram_bot_token,
                "telegram_chat_id": telegram_chat_id
            }
            
            unique_job_dir_name = f"job_{int(time.time())}_{wp_author}_{site_name}_{idx}"
            
            job_packages.append({
                "host": vps_host,
                "port": vps_port,
                "site_name": site_name,
                "chunk_images": chunk,  # Để xóa sau khi up
                "local_zip_path": zip_filepath,
                "zip_filename": zip_filename,
                "meta_content": meta_content,
                "unique_job_dir_name": unique_job_dir_name,
                "site_dir": site_dir
            })

    if not job_packages:
        print("Không có file nào cần upload.")
        return

    # 2. Phân loại theo Host và Upload
    files_by_host = defaultdict(list)
    for pkg in job_packages:
        files_by_host[(pkg["host"], pkg["port"])].append(pkg)
        
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_content = f"--- Báo cáo Upload KTB Crawler ---\nUser: {wp_author}\nThời gian: {timestamp}\n"
    total_queued = 0

    for (host, port), file_list in files_by_host.items():
        print(f"\n{'='*50}")
        print(f"🚀 Kết nối tới Host: {host}:{port} (User: {vps_user})")
        
        ssh = None
        sftp = None
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=port, username=vps_user, password=vps_password, timeout=15, disabled_algorithms={'publickey': []})
            sftp = ssh.open_sftp()
            
            print(f"✅ Kết nối {host} thành công! Bắt đầu upload {len(file_list)} jobs...")
            
            for pkg in file_list:
                zip_filename = pkg["zip_filename"]
                unique_job_dir = pkg["unique_job_dir_name"]
                
                local_meta_path = os.path.join(CACHE_DIR, f"{unique_job_dir}_meta.json")
                remote_tmp_dir = f"{remote_queue_dir}/tmp_{unique_job_dir}"
                remote_final_dir = f"{remote_queue_dir}/{unique_job_dir}"
                
                remote_zip_path = f"{remote_tmp_dir}/{zip_filename}"
                remote_meta_path = f"{remote_tmp_dir}/meta.json"
                
                upload_success = False
                
                try:
                    sftp.mkdir(remote_tmp_dir)
                    
                    with open(local_meta_path, 'w', encoding='utf-8') as f:
                        json.dump(pkg["meta_content"], f)
                        
                    print(f"   -> Đang đẩy meta.json ({zip_filename})...")
                    sftp.put(local_meta_path, remote_meta_path)
                    
                    print(f"   -> Đang đẩy {zip_filename}...")
                    sftp.put(pkg["local_zip_path"], remote_zip_path)
                    
                    print(f"   -> Kích hoạt Job (chống race condition)...")
                    command = f"mv {shlex.quote(remote_tmp_dir)} {shlex.quote(remote_final_dir)}"
                    stdin, stdout, stderr = ssh.exec_command(command)
                    exit_status = stdout.channel.recv_exit_status()
                    
                    if exit_status != 0:
                        raise Exception(f"Lỗi đổi tên: {stderr.read().decode()}")
                        
                    upload_success = True
                    print(f"   ✅ {zip_filename} tải lên thành công!")
                    report_content += f"\n[OK] {zip_filename} -> {host}"
                    total_queued += 1
                    
                except Exception as e:
                    print(f"   ❌ Lỗi upload {zip_filename}: {e}")
                    report_content += f"\n[LỖI] {zip_filename} ({e})"
                    try:
                        sftp.remove(remote_meta_path)
                        sftp.remove(remote_zip_path)
                        sftp.rmdir(remote_tmp_dir)
                    except: pass
                finally:
                    if os.path.exists(local_meta_path):
                        os.remove(local_meta_path)
                    
                    if upload_success:
                        # Xóa file zip local
                        os.remove(pkg["local_zip_path"])
                        # Xóa ảnh gốc
                        deleted_imgs = 0
                        for img in pkg["chunk_images"]:
                            try:
                                os.remove(os.path.join(pkg["site_dir"], img))
                                deleted_imgs += 1
                            except: pass
                        print(f"   🧹 Đã dọn dẹp {deleted_imgs} ảnh khỏi output.")
                    else:
                        # Nếu lỗi, giữ nguyên file zip và ảnh gốc để chạy lại
                        pass
                        
        except paramiko.AuthenticationException:
            print(f"❌ XÁC THỰC THẤT BẠI ở {host}. Sai mật khẩu VPS.")
            report_content += f"\n\n❌ LỖI KẾT NỐI {host}: SAI MẬT KHẨU."
        except Exception as e:
            print(f"❌ LỖI {host}: {e}")
            report_content += f"\n\n❌ LỖI {host}: {e}"
        finally:
            if sftp: sftp.close()
            if ssh: ssh.close()
            
    print(f"\n{'='*50}")
    print(f"Tổng kết: Đã đẩy thành công {total_queued} jobs.")
    report_content += f"\n\nTổng cộng: {total_queued} jobs thành công."
    send_telegram_message(telegram_bot_token, telegram_chat_id, report_content)

if __name__ == "__main__":
    main()
