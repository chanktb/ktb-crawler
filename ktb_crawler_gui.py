import os
import json
import threading
import queue
import requests
import uuid
import random
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from io import BytesIO
from datetime import datetime
import pytz

import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from utils.image_processing import (
        remove_background_advanced,
        trim_transparent_background,
        apply_mockup,
        add_watermark
    )
    from utils.file_io import (
        clean_title,
        pre_clean_filename,
        create_exif_data,
        remove_random_hashes
    )
except ImportError as e:
    print(f"Lỗi import utils: {e}")
    pass

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
GLOBAL_URL_FILE = os.path.join(BASE_DIR, "all_image_urls.txt")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
MOCKUP_DIR = os.path.join(BASE_DIR, "mockup")
WATERMARK_DIR = os.path.join(BASE_DIR, "watermark")
FONT_FILE = os.path.join(BASE_DIR, "fonts", "verdanab.ttf")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

class KTBCrawlerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KTB Crawler - Auto Mockup Generator")
        self.geometry("1000x900")
        self.config_data = {}
        self.urls = []
        self.current_idx = 0
        self.processed_urls = set()
        
        self.site_vars = {}
        self.watermark_var = tk.BooleanVar(value=False)
        self.output_format_var = tk.StringVar(value="jpg")
        self.image_ref = None
        self.crop_handler = None
        
        # Hàng đợi (Queue) kết hợp Đa luồng (Multi-threading)
        self.job_queue = queue.Queue()
        self.num_workers = 4  # Số luồng chạy song song
        for _ in range(self.num_workers):
            threading.Thread(target=self._queue_worker, daemon=True).start()
        
        self.load_config()
        self.load_urls()
        
        self.setup_ui()
        self.load_image_at_idx()
        
        self.prefetch_thread = threading.Thread(target=self.prefetch_images, daemon=True)
        self.prefetch_thread.start()

    def _queue_worker(self):
        """Worker chạy ngầm, liên tục lấy job từ Hàng đợi ra để xử lý"""
        while True:
            job = self.job_queue.get()
            self._process_auto(job)
            self.job_queue.task_done()
            
            # Cập nhật số lượng queue lên UI
            q_size = self.job_queue.qsize()
            if q_size == 0:
                self.after(0, lambda: self.lbl_status.config(text="✅ Đã xử lý xong tất cả hàng đợi!", fg="green"))
            else:
                self.after(0, lambda sz=q_size: self.lbl_status.config(text=f"Đang xử lý... (Còn {sz} ảnh trong Hàng đợi)", fg="orange"))

    def _process_auto(self, job):
        """Hàm này chạy trong _queue_worker thread của KTBCrawlerGUI"""
        try:
            pil_image = Image.open(job['filepath']).convert("RGBA")
            x, y, w, h = job['crop_coords']['x'], job['crop_coords']['y'], job['crop_coords']['w'], job['crop_coords']['h']
            
            cropped = pil_image.crop((x, y, x + w, y + h)).convert("RGBA")
            
            pixel = cropped.getpixel((0, 0))
            is_white = sum(pixel[:3]) > 384
            color_key = "white" if is_white else "black"
            
            bg_removed = remove_background_advanced(cropped)
            trimmed_img = trim_transparent_background(bg_removed)
            
            if not trimmed_img:
                print(f"Lỗi: Không tìm thấy thiết kế sau tách nền cho URL: {job['url']}")
                return
                
            filename_with_ext = job['url'].split('/')[-1]
            raw_name = os.path.splitext(filename_with_ext)[0]
            
            mockup_sets_config = self.config_data.get("mockup_sets", {})
            defaults = self.config_data.get("defaults", {})
            use_watermark = job['use_watermark']
            output_format = job['output_format']
            
            for site_name in job['checked_sites']:
                site_output_dir = os.path.join(OUTPUT_DIR, site_name)
                os.makedirs(site_output_dir, exist_ok=True)
                
                mockup_cfg = mockup_sets_config.get(site_name)
                if not mockup_cfg: continue
                
                color_list = mockup_cfg.get(color_key, [])
                if not color_list: continue
                
                chosen_mockup = random.choice(color_list)
                mockup_filename = chosen_mockup.get("file")
                mockup_path = os.path.join(MOCKUP_DIR, mockup_filename)
                
                if not os.path.exists(mockup_path): continue
                
                try:
                    with Image.open(mockup_path) as m_img:
                        final_mockup = apply_mockup(trimmed_img, m_img, chosen_mockup.get("coords"))
                        
                        if use_watermark:
                            wm_path = os.path.join(WATERMARK_DIR, f"{site_name}.png")
                            if os.path.exists(wm_path):
                                wm_img = Image.open(wm_path).convert("RGBA")
                                wm_w, wm_h = wm_img.size
                                fm_w, fm_h = final_mockup.size
                                final_mockup.paste(wm_img, (fm_w - wm_w - 20, fm_h - wm_h - 20), wm_img)
                                
                    # Lấy Whitelist từ config
                    whitelist = defaults.get("whitelist_keywords", [])
                    
                    # Loại bỏ hash ngẫu nhiên rồi mới clean title bình thường
                    raw_title_no_hash = remove_random_hashes(raw_name, whitelist)
                    cleaned_title = clean_title(pre_clean_filename(raw_title_no_hash, None), defaults.get("title_clean_keywords", []))
                    prefix = mockup_cfg.get("title_prefix_to_add", "")
                    suffix = mockup_cfg.get("title_suffix_to_add", "")
                    
                    final_filename_base = f"{prefix} {cleaned_title} {suffix}".strip().replace('  ', ' ')
                    ext = f".{output_format}"
                    save_path = os.path.join(site_output_dir, f"{final_filename_base}{ext}")
                    
                    image_to_save = final_mockup.convert('RGB')
                    exif_bytes = create_exif_data(site_name, f"{final_filename_base}{ext}", defaults.get("exif_defaults", {}))
                    save_format = "WEBP" if output_format == "webp" else "JPEG"
                    
                    image_to_save.save(save_path, format=save_format, quality=90, exif=exif_bytes)
                except Exception as e:
                    print(f"Lỗi ghép {site_name}: {e}")
                    
        except Exception as e:
            print(f"Lỗi hệ thống khi xử lý job: {e}")

    def load_config(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                self.config_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đọc config: {e}")

    def load_urls(self):
        self.urls = []
        
        # 1. Tải danh sách mới từ GitHub nếu có cấu hình
        defaults = self.config_data.get("defaults", {})
        github_raw_url = defaults.get("github_raw_url", "")
        
        if github_raw_url:
            print(f"Đang đồng bộ danh sách URL từ GitHub: {github_raw_url}")
            try:
                r = requests.get(github_raw_url, timeout=10)
                if r.status_code == 200:
                    with open(GLOBAL_URL_FILE, 'w', encoding='utf-8') as f:
                        f.write(r.text)
                    print("Đồng bộ danh sách URL thành công!")
            except Exception as e:
                print(f"Lỗi tải từ GitHub: {e}. Sẽ dùng lại danh sách cũ trên máy.")

        # 2. Đọc file local
        if os.path.exists(GLOBAL_URL_FILE):
            with open(GLOBAL_URL_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    u = line.strip()
                    if u: self.urls.append(u)

    def setup_ui(self):
        top_frame = tk.Frame(self, bg="#f0f0f0")
        top_frame.pack(side=tk.TOP, fill=tk.X)
        
        sites_frame = tk.LabelFrame(top_frame, text="1. Chọn Output Sites", bg="#f0f0f0")
        sites_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=2)
        
        output_sites = self.config_data.get("output_sites", [])
        for site in output_sites:
            var = tk.BooleanVar(value=True)
            self.site_vars[site] = var
            cb = tk.Checkbutton(sites_frame, text=site, variable=var, bg="#f0f0f0")
            cb.pack(side=tk.LEFT, padx=5)
            
        options_frame = tk.LabelFrame(top_frame, text="2. Tùy chọn", bg="#f0f0f0")
        options_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=2)
        
        wm_cb = tk.Checkbutton(options_frame, text="Thêm Watermark", variable=self.watermark_var, bg="#f0f0f0", font=("Arial", 10, "bold"), fg="blue")
        wm_cb.pack(side=tk.LEFT, padx=10)
        
        tk.Label(options_frame, text="Định dạng Output:", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(options_frame, text="JPG", variable=self.output_format_var, value="jpg", bg="#f0f0f0").pack(side=tk.LEFT)
        tk.Radiobutton(options_frame, text="WEBP", variable=self.output_format_var, value="webp", bg="#f0f0f0").pack(side=tk.LEFT)
        
        title_frame = tk.LabelFrame(top_frame, text="3. Cấu hình Title (Tiền tố/Hậu tố)", bg="#f0f0f0")
        title_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=2)
        
        tk.Label(title_frame, text="Site:", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        self.title_site_combo = ttk.Combobox(title_frame, values=output_sites, state="readonly", width=15)
        self.title_site_combo.pack(side=tk.LEFT, padx=5)
        self.title_site_combo.bind("<<ComboboxSelected>>", self.on_title_site_selected)
        
        tk.Label(title_frame, text="Prefix:", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        self.title_prefix_var = tk.StringVar()
        tk.Entry(title_frame, textvariable=self.title_prefix_var, width=15).pack(side=tk.LEFT, padx=5)
        
        tk.Label(title_frame, text="Suffix:", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        self.title_suffix_var = tk.StringVar()
        tk.Entry(title_frame, textvariable=self.title_suffix_var, width=25).pack(side=tk.LEFT, padx=5)
        
        tk.Button(title_frame, text="Lưu Config", command=self.save_title_config, bg="#aaddaa").pack(side=tk.LEFT, padx=10)
        
        if output_sites:
            self.title_site_combo.current(0)
            self.on_title_site_selected(None)
        
        nav_frame = tk.Frame(top_frame, bg="#f0f0f0")
        nav_frame.pack(side=tk.TOP, fill=tk.X, pady=5)
        
        tk.Button(nav_frame, text="<< Ảnh Trước", command=self.prev_image, bg="#ddd").pack(side=tk.LEFT, padx=10)
        
        self.lbl_page = tk.Label(nav_frame, text="Ảnh 1 / X", font=("Arial", 12, "bold"), bg="#f0f0f0")
        self.lbl_page.pack(side=tk.LEFT, expand=True)
        
        self.lbl_processed = tk.Label(nav_frame, text="", font=("Arial", 14, "bold"), fg="red", bg="#f0f0f0")
        self.lbl_processed.pack(side=tk.LEFT, expand=True)
        
        tk.Button(nav_frame, text="Ảnh Sau >>", command=self.next_image, bg="#ddd").pack(side=tk.RIGHT, padx=10)

        self.lbl_status = tk.Label(top_frame, text="Sẵn sàng", fg="green", bg="#f0f0f0")
        self.lbl_status.pack(side=tk.TOP, pady=2)
        
        self.lbl_url = tk.Label(top_frame, text="", bg="#333", fg="white", font=("Arial", 9))
        self.lbl_url.pack(side=tk.TOP, fill=tk.X)

        self.image_frame = tk.Frame(self, bg="#2b2b2b")
        self.image_frame.pack(fill=tk.BOTH, expand=True)
        
        self.canvas = tk.Canvas(self.image_frame, bg="black", cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Binds
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Up>", lambda e: self.prev_image())
        self.bind("<Left>", lambda e: self.prev_image())
        self.bind("<Down>", lambda e: self.next_image())
        self.bind("<Right>", lambda e: self.next_image())
        self.bind("<Escape>", lambda e: self.cancel_crop())

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self.prev_image()
        else:
            self.next_image()
        # Hiển thị ảnh đầu tiên
        self.update_image_display()

    def on_title_site_selected(self, event):
        site = self.title_site_combo.get()
        if site:
            mockup_cfg = self.config_data.get("mockup_sets", {}).get(site, {})
            self.title_prefix_var.set(mockup_cfg.get("title_prefix_to_add", ""))
            self.title_suffix_var.set(mockup_cfg.get("title_suffix_to_add", ""))

    def save_title_config(self):
        site = self.title_site_combo.get()
        if not site: return
        prefix = self.title_prefix_var.get()
        suffix = self.title_suffix_var.get()
        
        if "mockup_sets" not in self.config_data:
            self.config_data["mockup_sets"] = {}
        if site not in self.config_data["mockup_sets"]:
            self.config_data["mockup_sets"][site] = {}
            
        self.config_data["mockup_sets"][site]["title_prefix_to_add"] = prefix
        self.config_data["mockup_sets"][site]["title_suffix_to_add"] = suffix
        
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.config_data, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Thành công", f"Đã lưu Prefix/Suffix cho site {site}!")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể lưu config: {e}")

    def prev_image(self):
        if self.current_idx > 0:
            self.cancel_crop()
            self.current_idx -= 1
            self.load_image_at_idx()

    def next_image(self):
        if self.current_idx < len(self.urls) - 1:
            self.cancel_crop()
            self.current_idx += 1
            self.load_image_at_idx()

    def cancel_crop(self):
        if self.crop_handler:
            self.crop_handler.cancel()

    def prefetch_images(self):
        for i in range(min(5, len(self.urls))):
            self._download_if_needed(self.urls[i])

    def _download_if_needed(self, url):
        filename = uuid.uuid5(uuid.NAMESPACE_URL, url).hex + ".jpg"
        filepath = os.path.join(CACHE_DIR, filename)
        if not os.path.exists(filepath):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8"
                }
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    with open(filepath, 'wb') as f: f.write(r.content)
                else:
                    print(f"Lỗi tải ảnh {url}: HTTP Status {r.status_code}")
            except Exception as e:
                print(f"Lỗi tải ảnh {url} (Timeout hoặc Connection Error): {e}")
        return filepath

    def load_image_at_idx(self):
        if not self.urls: return
        self.canvas.delete("all")
        
        url = self.urls[self.current_idx]
        self.lbl_page.config(text=f"Ảnh {self.current_idx + 1} / {len(self.urls)}")
        self.lbl_url.config(text=url)
        
        q_size = self.job_queue.qsize()
        if q_size > 0:
            self.lbl_status.config(text=f"Đang chờ Hàng đợi ({q_size} ảnh)...", fg="orange")
        else:
            self.lbl_status.config(text="Sẵn sàng ghép", fg="green")
        
        if url in self.processed_urls:
            self.lbl_processed.config(text="✅ ĐÃ GHÉP", fg="green")
        else:
            self.lbl_processed.config(text="")
        
        threading.Thread(target=self._prefetch_next, args=(self.current_idx,), daemon=True).start()
        threading.Thread(target=self._render_single_image, args=(url,), daemon=True).start()
        
    def _prefetch_next(self, idx):
        for i in range(idx + 1, min(idx + 3, len(self.urls))):
            self._download_if_needed(self.urls[i])

    def _render_single_image(self, url):
        filepath = self._download_if_needed(url)
        if not os.path.exists(filepath):
            self.after(0, lambda: self.lbl_status.config(text="Lỗi tải ảnh", fg="red"))
            return
            
        try:
            pil_img = Image.open(filepath).convert("RGB")
        except:
            self.after(0, lambda: self.lbl_status.config(text="Ảnh bị lỗi định dạng", fg="red"))
            return
            
        self.update_idletasks()
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        if canvas_w < 100 or canvas_h < 100:
            canvas_w, canvas_h = 950, 700
            
        orig_w, orig_h = pil_img.size
        scale_w = canvas_w / orig_w
        scale_h = canvas_h / orig_h
        scale = min(scale_w, scale_h) * 0.95
        
        if scale > 1: scale = 1
        
        display_w = int(orig_w * scale)
        display_h = int(orig_h * scale)
        
        display_img = pil_img.resize((display_w, display_h), Image.LANCZOS)
        tk_photo = ImageTk.PhotoImage(display_img)
        
        self.after(0, self._set_image_on_canvas, tk_photo, filepath, url, scale, display_w, display_h, canvas_w, canvas_h)

    def _set_image_on_canvas(self, tk_photo, filepath, url, scale, dw, dh, cw, ch):
        self.image_ref = tk_photo
        self.canvas.delete("all")
        
        offset_x = (cw - dw) // 2
        offset_y = (ch - dh) // 2
        
        self.canvas.create_image(offset_x, offset_y, image=self.image_ref, anchor="nw", tags="image_tag")
        
        self.crop_handler = CropHandler(self, self.canvas, filepath, url, scale, offset_x, offset_y)
        self.canvas.bind("<ButtonPress-1>", self.crop_handler.on_press)
        self.canvas.bind("<B1-Motion>", self.crop_handler.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.crop_handler.on_release)


class CropHandler:
    def __init__(self, app, canvas, original_image_path, url, scale, offset_x, offset_y):
        self.app = app
        self.canvas = canvas
        self.filepath = original_image_path
        self.url = url
        self.scale = scale
        self.offset_x = offset_x
        self.offset_y = offset_y
        
        self.start_x = 0
        self.start_y = 0
        self.rect = None

    def cancel(self):
        if self.rect:
            self.canvas.delete(self.rect)
            self.rect = None

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect: self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, 1, 1, outline='#00ff00', width=3, dash=(4, 4))

    def on_drag(self, event):
        if self.rect:
            self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        if not self.rect: return
        
        x1, x2 = min(self.start_x, event.x), max(self.start_x, event.x)
        y1, y2 = min(self.start_y, event.y), max(self.start_y, event.y)
        
        w, h = x2 - x1, y2 - y1
        if w > 20 and h > 20:
            adj_x1 = x1 - self.offset_x
            adj_y1 = y1 - self.offset_y
            
            orig_x = int(adj_x1 / self.scale)
            orig_y = int(adj_y1 / self.scale)
            orig_w = int(w / self.scale)
            orig_h = int(h / self.scale)
            
            crop_coords = {'x': orig_x, 'y': orig_y, 'w': orig_w, 'h': orig_h}
            
            # Kiểm tra vùng chọn nằm ngoài ảnh
            try:
                pil_image = Image.open(self.filepath)
                if orig_x < 0 or orig_y < 0 or orig_x + orig_w > pil_image.width or orig_y + orig_h > pil_image.height:
                    self.app.lbl_status.config(text="Vùng chọn nằm ngoài ảnh, đã hủy", fg="orange")
                    self.cancel()
                    return
            except: pass
            
            # Gửi job vào Queue
            job = {
                'url': self.url,
                'filepath': self.filepath,
                'crop_coords': crop_coords,
                'checked_sites': [site for site, var in self.app.site_vars.items() if var.get()],
                'use_watermark': self.app.watermark_var.get(),
                'output_format': self.app.output_format_var.get().lower()
            }
            self.app.job_queue.put(job)
            
            # Cập nhật UI ngay lập tức để user tiếp tục
            self.app.processed_urls.add(self.url)
            self.app.lbl_processed.config(text="✅ ĐÃ GHÉP", fg="green")
            self.canvas.itemconfig(self.rect, outline="blue") # Đổi màu xanh dương báo hiệu đã đưa vào Queue
            
            q_size = self.app.job_queue.qsize()
            self.app.lbl_status.config(text=f"Đã đưa vào Hàng đợi (Đang chờ: {q_size})", fg="blue")
            
        else:
            self.app.lbl_status.config(text="Vùng chọn quá nhỏ, đã hủy", fg="orange")
            self.cancel()

if __name__ == "__main__":
    app = KTBCrawlerGUI()
    app.mainloop()
