import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import json
import os
import time
import logging
import traceback
from datetime import datetime
from PIL import Image, ImageTk

from capture import capture_region, select_region
from ocr import extract_chat_text, image_to_base64
from models import ChatSightModel, PROVIDER_DEFAULTS
from storage import ChatStorage
from paths import CONFIG_PATH, SCREENSHOT_DIR, ensure_writable_dir

logger = logging.getLogger(__name__)


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class ChatSightApp:
    def __init__(self):
        logger.info("正在初始化 ChatSightApp...")
        self.root = tk.Tk()
        self.root.title("ChatSight - AI 聊天智能助手")
        self.root.geometry("1000x750")
        self.root.minsize(800, 600)

        self.config = load_config()
        logger.info(f"配置加载完成: provider={self.config.get('api_provider', 'openai')}")
        self.model = ChatSightModel(self.config)
        self.storage = ChatStorage()
        logger.info("存储模块初始化完成")
        self.current_session_id = None
        self.last_image = None
        self.chat_history = []

        # tkinter 回调里的异常不会走 sys.excepthook，必须单独接管，否则只闪一下、零日志
        self.root.report_callback_exception = self._report_callback_exception

        self._build_ui()
        self._update_status("就绪")
        logger.info("界面初始化完成")

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback):
        """记录界面回调（按钮点击等）中抛出的异常，并提示用户"""
        if issubclass(exc_type, KeyboardInterrupt):
            return
        error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        logger.critical(f"界面回调异常:\n{error_msg}")

        try:
            from logger import write_crash_log
            crash_file = write_crash_log("界面回调异常", error_msg)
        except Exception:
            crash_file = ""

        self._update_status(f"错误: {exc_value}")
        messagebox.showerror(
            "错误",
            f"{exc_value}\n\n" + (f"错误日志已保存到: {crash_file}" if crash_file else "")
        )

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_chat_tab()
        self._build_recognition_tab()
        self._build_ocr_tab()
        self._build_history_tab()
        self._build_settings_tab()

        self.status_bar = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_chat_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="AI 对话")

        ttk.Label(tab, text="AI 智能对话", font=("Microsoft YaHei", 12, "bold")).pack(pady=10)

        chat_frame = ttk.Frame(tab)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.chat_display = scrolledtext.ScrolledText(chat_frame, wrap=tk.WORD,
                                                       font=("Microsoft YaHei", 10),
                                                       state=tk.DISABLED)
        self.chat_display.pack(fill=tk.BOTH, expand=True)

        input_frame = ttk.Frame(tab)
        input_frame.pack(fill=tk.X, padx=10, pady=10)

        self.chat_input = scrolledtext.ScrolledText(input_frame, wrap=tk.WORD,
                                                     font=("Microsoft YaHei", 10),
                                                     height=3)
        self.chat_input.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 5))

        btn_frame = ttk.Frame(input_frame)
        btn_frame.pack(side=tk.RIGHT)

        ttk.Button(btn_frame, text="发送", command=self._send_chat_message).pack(pady=2)
        ttk.Button(btn_frame, text="清空对话", command=self._clear_chat).pack(pady=2)

        self.chat_input.bind("<Return>", lambda e: (self._send_chat_message(), "break"))
        self.chat_input.bind("<Shift-Return>", lambda e: self.chat_input.insert(tk.END, "\n") or "break")

    def _build_recognition_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="聊天识别")

        ttk.Label(tab, text="上传聊天截图，自动识别文字并生成推荐回复",
                  font=("Microsoft YaHei", 10)).pack(pady=5)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(btn_frame, text="上传图片", command=self._load_image_recognition).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="截屏识别", command=self._capture_recognition).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="生成推荐回复", command=self._generate_replies_recognition).pack(side=tk.LEFT, padx=5)

        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        ttk.Label(left, text="识别结果", font=("Microsoft YaHei", 10, "bold")).pack(anchor=tk.W)
        self.recognition_text = scrolledtext.ScrolledText(left, wrap=tk.WORD,
                                                           font=("Microsoft YaHei", 10))
        self.recognition_text.pack(fill=tk.BOTH, expand=True, pady=5)

        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        ttk.Label(right, text="推荐回复", font=("Microsoft YaHei", 10, "bold")).pack(anchor=tk.W)

        sug_frame = ttk.Frame(right)
        sug_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.recognition_suggestions_canvas = tk.Canvas(sug_frame)
        sug_scrollbar = ttk.Scrollbar(sug_frame, orient=tk.VERTICAL,
                                       command=self.recognition_suggestions_canvas.yview)
        self.recognition_suggestions_inner = ttk.Frame(self.recognition_suggestions_canvas)

        self.recognition_suggestions_inner.bind(
            "<Configure>",
            lambda e: self.recognition_suggestions_canvas.configure(scrollregion=self.recognition_suggestions_canvas.bbox("all"))
        )
        self.recognition_suggestions_canvas.create_window((0, 0), window=self.recognition_suggestions_inner, anchor=tk.NW)
        self.recognition_suggestions_canvas.configure(yscrollcommand=sug_scrollbar.set)

        self.recognition_suggestions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sug_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_ocr_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="截图 OCR")

        ttk.Label(tab, text="识别图片中的文字，支持编辑和导出",
                  font=("Microsoft YaHei", 10)).pack(pady=5)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(btn_frame, text="上传图片", command=self._load_image_ocr).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="截屏识别", command=self._capture_ocr).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="复制文本", command=self._copy_ocr_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="导出文本", command=self._export_ocr_text).pack(side=tk.LEFT, padx=5)

        ttk.Label(tab, text="识别结果", font=("Microsoft YaHei", 10, "bold")).pack(anchor=tk.W, padx=10)
        self.ocr_text = scrolledtext.ScrolledText(tab, wrap=tk.WORD,
                                                   font=("Microsoft YaHei", 10))
        self.ocr_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def _build_history_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="历史记录")

        ttk.Label(tab, text="历史对话记录", font=("Microsoft YaHei", 10, "bold")).pack(pady=5)

        list_frame = ttk.Frame(tab)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("title", "updated_at")
        self.history_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        self.history_tree.heading("title", text="会话标题")
        self.history_tree.heading("updated_at", text="更新时间")
        self.history_tree.column("title", width=400)
        self.history_tree.column("updated_at", width=200)

        self.history_tree.pack(fill=tk.BOTH, expand=True)
        self.history_tree.bind("<Double-1>", self._on_history_select)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text="刷新", command=self._refresh_history).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="加载", command=self._load_history_session).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除", command=self._delete_history_session).pack(side=tk.LEFT, padx=5)

        self._refresh_history()

    def _build_settings_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="设置")

        canvas = tk.Canvas(tab)
        scrollbar = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(scrollable, text="AI 模型配置", font=("Microsoft YaHei", 11, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Separator(scrollable).pack(fill=tk.X, pady=5)

        form = ttk.Frame(scrollable)
        form.pack(fill=tk.X, pady=5)

        ttk.Label(form, text="API 提供商:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.provider_var = tk.StringVar(value=self.config.get("api_provider", "openai"))
        provider_combo = ttk.Combobox(form, textvariable=self.provider_var, width=30,
                                       values=list(PROVIDER_DEFAULTS.keys()))
        provider_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        self.provider_var.trace_add("write", self._on_provider_changed)

        ttk.Label(form, text="API Key:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=5)
        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        ttk.Entry(form, textvariable=self.api_key_var, width=35, show="*").grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(form, text="API Base URL:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=5)
        self.base_url_var = tk.StringVar(value=self.config.get("api_base_url", ""))
        ttk.Entry(form, textvariable=self.base_url_var, width=35).grid(row=2, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(form, text="(留空使用默认地址)", foreground="gray").grid(row=2, column=2, sticky=tk.W, pady=5, padx=5)

        ttk.Label(form, text="文本模型:").grid(row=3, column=0, sticky=tk.W, pady=5, padx=5)
        self.text_model_var = tk.StringVar(value=self.config.get("text_model", "gpt-4o-mini"))
        model_frame = ttk.Frame(form)
        model_frame.grid(row=3, column=1, columnspan=2, sticky=tk.W, pady=5, padx=5)
        self.text_model_combo = ttk.Combobox(model_frame, textvariable=self.text_model_var, width=25,
                                              values=PROVIDER_DEFAULTS.get(self.config.get("api_provider", "openai"), {}).get("text_models", []))
        self.text_model_combo.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(model_frame, text="获取模型列表", command=self._fetch_text_models).pack(side=tk.LEFT)

        ttk.Label(form, text="视觉模型:").grid(row=4, column=0, sticky=tk.W, pady=5, padx=5)
        self.vision_model_var = tk.StringVar(value=self.config.get("vision_model", "qwen3-vl-plus"))
        vision_model_frame = ttk.Frame(form)
        vision_model_frame.grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=5, padx=5)
        self.vision_model_combo = ttk.Combobox(vision_model_frame, textvariable=self.vision_model_var, width=25,
                                                values=self.model.get_available_models("vision"))
        self.vision_model_combo.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(vision_model_frame, text="获取模型列表", command=self._fetch_vision_models).pack(side=tk.LEFT)

        ttk.Separator(scrollable).pack(fill=tk.X, pady=10)
        ttk.Label(scrollable, text="视觉 OCR 独立配置（可选）", font=("Microsoft YaHei", 11, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Label(scrollable, text="文本模型和 OCR 可以用不同的服务商，例如文本用 DeepSeek、OCR 用千问。",
                  foreground="gray").pack(anchor=tk.W)
        ttk.Separator(scrollable).pack(fill=tk.X, pady=5)

        vision_form = ttk.Frame(scrollable)
        vision_form.pack(fill=tk.X, pady=5)

        ttk.Label(vision_form, text="视觉提供商:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.vision_provider_var = tk.StringVar(value=self.config.get("vision_provider", ""))
        vision_provider_combo = ttk.Combobox(vision_form, textvariable=self.vision_provider_var, width=30,
                                              values=[""] + list(PROVIDER_DEFAULTS.keys()))
        vision_provider_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(vision_form, text="(留空 = 与文本模型相同)", foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=5, padx=5)
        self.vision_provider_var.trace_add("write", self._on_vision_provider_changed)

        ttk.Label(vision_form, text="视觉 API Key:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=5)
        self.vision_api_key_var = tk.StringVar(value=self.config.get("vision_api_key", ""))
        ttk.Entry(vision_form, textvariable=self.vision_api_key_var, width=35, show="*").grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(vision_form, text="(留空 = 与文本模型相同)", foreground="gray").grid(row=1, column=2, sticky=tk.W, pady=5, padx=5)

        ttk.Label(vision_form, text="视觉 Base URL:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=5)
        self.vision_base_url_var = tk.StringVar(value=self.config.get("vision_base_url", ""))
        ttk.Entry(vision_form, textvariable=self.vision_base_url_var, width=35).grid(row=2, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(vision_form, text="(留空使用默认地址)", foreground="gray").grid(row=2, column=2, sticky=tk.W, pady=5, padx=5)

        ttk.Separator(scrollable).pack(fill=tk.X, pady=10)
        ttk.Label(scrollable, text="OCR 配置", font=("Microsoft YaHei", 11, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Separator(scrollable).pack(fill=tk.X, pady=5)

        ocr_form = ttk.Frame(scrollable)
        ocr_form.pack(fill=tk.X, pady=5)

        ttk.Label(ocr_form, text="OCR 方式:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.ocr_method_var = tk.StringVar(value=self.config.get("ocr_method", "auto"))
        ttk.Combobox(ocr_form, textvariable=self.ocr_method_var, width=20,
                      values=["auto", "vision", "tesseract"]).grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(ocr_form, text="(auto=优先视觉模型, tesseract=本地OCR)", foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=5, padx=5)

        ttk.Separator(scrollable).pack(fill=tk.X, pady=10)
        ttk.Label(scrollable, text="回复设置", font=("Microsoft YaHei", 11, "bold")).pack(anchor=tk.W, pady=5)
        ttk.Separator(scrollable).pack(fill=tk.X, pady=5)

        reply_form = ttk.Frame(scrollable)
        reply_form.pack(fill=tk.X, pady=5)

        ttk.Label(reply_form, text="推荐回复数:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.reply_count_var = tk.IntVar(value=self.config.get("reply_count", 3))
        ttk.Spinbox(reply_form, from_=1, to=10, textvariable=self.reply_count_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(reply_form, text="回复风格:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=5)
        self.reply_style_var = tk.StringVar(value=self.config.get("reply_style", "friendly"))
        ttk.Combobox(reply_form, textvariable=self.reply_style_var, width=20,
                      values=["friendly", "professional", "humorous", "concise", "empathetic"]).grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Separator(scrollable).pack(fill=tk.X, pady=15)
        ttk.Button(scrollable, text="保存设置", command=self._save_settings).pack(pady=10)

    def _update_status(self, text: str):
        self.status_bar.config(text=text)
        self.root.update_idletasks()

    def _send_chat_message(self):
        message = self.chat_input.get("1.0", tk.END).strip()
        if not message:
            return

        self.chat_input.delete("1.0", tk.END)
        self.chat_history.append({"role": "user", "content": message})

        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"你: {message}\n\n")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        if not self.model.is_configured:
            self.chat_display.config(state=tk.NORMAL)
            self.chat_display.insert(tk.END, "AI: 请先在「设置」中配置 API Key\n\n")
            self.chat_display.config(state=tk.DISABLED)
            return

        self._update_status("AI 正在思考...")

        def process():
            try:
                model_name = self.config.get("text_model", "gpt-4o-mini")
                response = self.model.client.chat.completions.create(
                    model=model_name,
                    messages=self.chat_history,
                    max_tokens=2000,
                    temperature=0.7
                )
                reply = response.choices[0].message.content.strip()
                self.chat_history.append({"role": "assistant", "content": reply})

                self.root.after(0, lambda: self._append_chat_reply(reply))
            except Exception as e:
                logger.exception(f"AI 对话请求失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._append_chat_reply(f"错误: {err_msg}"))

        threading.Thread(target=process, daemon=True).start()

    def _append_chat_reply(self, reply: str):
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"AI: {reply}\n\n")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)
        self._update_status("就绪")

    def _clear_chat(self):
        self.chat_history = []
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state=tk.DISABLED)

    def _load_image_recognition(self):
        path = filedialog.askopenfilename(
            title="选择聊天截图",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有文件", "*.*")]
        )
        if not path:
            return

        self._update_status("正在识别图片中的文字...")

        def process():
            try:
                image = Image.open(path).convert("RGB")
                self.last_image = image

                client = self.model.vision_client if self.config.get("ocr_method") in ("auto", "vision") else None
                vision_model = self.config.get("vision_model", "qwen3-vl-plus")
                ocr_method = self.config.get("ocr_method", "auto")

                text = extract_chat_text(image, method=ocr_method, client=client, vision_model=vision_model)

                if self.current_session_id is None:
                    self.current_session_id = self.storage.create_session()
                self.storage.save_message(self.current_session_id, text, path)

                self.root.after(0, lambda: self._on_recognition_done(text))
            except Exception as e:
                logger.exception(f"图片识别失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._on_error(err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _capture_recognition(self):
        self._update_status("正在截屏... 请拖拽选择聊天窗口区域")
        self.root.withdraw()
        self.root.after(300, self._do_capture_recognition)

    def _do_capture_recognition(self):
        def on_region_selected(region, image):
            # 图片已经在覆盖层里裁剪好了，这里恢复主窗口不会影响识别内容
            self.root.deiconify()
            self.root.lift()
            self._on_region_recognition(region, image)

        try:
            select_region(on_region_selected, master=self.root)
        except Exception as e:
            logger.exception(f"打开截屏选择层失败: {e}")
            self.root.deiconify()
            err_msg = str(e)
            self.root.after(0, lambda: self._on_error(f"截屏失败: {err_msg}"))

    def _on_region_recognition(self, region, image=None):
        if region is None:
            self._update_status("截屏已取消")
            return

        self._update_status("正在识别文字...")

        def process():
            try:
                # img 来自覆盖层里那张截图的裁剪（所见即所得）；
                # 只有兜底路径（img 为空）才重新抓一次屏
                img = image
                if img is None:
                    img = capture_region(region)
                self.last_image = img

                screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
                img_path = os.path.join(screenshots_dir, f"{int(time.time())}.png")
                img.save(img_path)

                client = self.model.vision_client if self.config.get("ocr_method") in ("auto", "vision") else None
                vision_model = self.config.get("vision_model", "qwen3-vl-plus")
                ocr_method = self.config.get("ocr_method", "auto")

                text = extract_chat_text(img, method=ocr_method, client=client, vision_model=vision_model)

                if self.current_session_id is None:
                    self.current_session_id = self.storage.create_session()
                self.storage.save_message(self.current_session_id, text, img_path)

                self.root.after(0, lambda: self._on_recognition_done(text))
            except Exception as e:
                logger.exception(f"截屏识别失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._on_error(err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _on_recognition_done(self, text: str):
        self.recognition_text.delete("1.0", tk.END)
        self.recognition_text.insert("1.0", text)
        self._update_status(f"识别完成，共 {len(text)} 个字符")

    def _generate_replies_recognition(self):
        chat_text = self.recognition_text.get("1.0", tk.END).strip()
        if not chat_text:
            messagebox.showinfo("提示", "请先识别聊天内容")
            return

        if not self.model.is_configured:
            messagebox.showwarning("未配置", "请先在设置中配置 API Key")
            return

        self._update_status("正在生成推荐回复...")

        for widget in self.recognition_suggestions_inner.winfo_children():
            widget.destroy()

        def process():
            try:
                count = self.config.get("reply_count", 3)
                style = self.config.get("reply_style", "friendly")
                suggestions = self.model.get_reply_suggestions(chat_text, count=count, style=style)

                msg_id = None
                if self.current_session_id:
                    msgs = self.storage.get_session_messages(self.current_session_id)
                    if msgs:
                        msg_id = msgs[-1]["id"]

                sug_ids = None
                if msg_id:
                    model_name = self.config.get("text_model", "unknown")
                    sug_ids = self.storage.save_suggestions(msg_id, suggestions, model_used=model_name)

                self.root.after(0, lambda: self._on_recognition_replies_done(suggestions, sug_ids))
            except Exception as e:
                logger.exception(f"生成推荐回复失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._on_error(err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _on_recognition_replies_done(self, suggestions: list, suggestion_ids: list = None):
        for widget in self.recognition_suggestions_inner.winfo_children():
            widget.destroy()

        for i, s in enumerate(suggestions, 1):
            card = ttk.Frame(self.recognition_suggestions_inner, relief=tk.RAISED, borderwidth=1)
            card.pack(fill=tk.X, padx=5, pady=3)

            header = ttk.Label(card, text=f"推荐 {i}", font=("Microsoft YaHei", 9, "bold"))
            header.pack(anchor=tk.W, padx=5, pady=(3, 0))

            content = tk.Text(card, wrap=tk.WORD, height=3, font=("Microsoft YaHei", 10),
                              borderwidth=0, highlightthickness=0)
            content.pack(fill=tk.X, padx=5, pady=3)
            content.insert("1.0", s)
            content.config(state=tk.DISABLED)

            btn_row = ttk.Frame(card)
            btn_row.pack(fill=tk.X, padx=5, pady=(0, 3))

            sug_id = suggestion_ids[i - 1] if suggestion_ids and i - 1 < len(suggestion_ids) else None

            def copy_cmd(t=s, sid=sug_id):
                self.root.clipboard_clear()
                self.root.clipboard_append(t)
                if sid is not None:
                    self.storage.mark_suggestion_copied(sid)
                self._update_status(f"已复制推荐回复到剪贴板")

            ttk.Button(btn_row, text="复制", command=copy_cmd).pack(side=tk.LEFT, padx=2)

        self._update_status(f"已生成 {len(suggestions)} 条推荐回复")

    def _load_image_ocr(self):
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有文件", "*.*")]
        )
        if not path:
            return

        self._update_status("正在识别图片中的文字...")

        def process():
            try:
                image = Image.open(path).convert("RGB")

                client = self.model.vision_client if self.config.get("ocr_method") in ("auto", "vision") else None
                vision_model = self.config.get("vision_model", "qwen3-vl-plus")
                ocr_method = self.config.get("ocr_method", "auto")

                text = extract_chat_text(image, method=ocr_method, client=client, vision_model=vision_model)

                self.root.after(0, lambda: self._on_ocr_done(text))
            except Exception as e:
                logger.exception(f"图片 OCR 失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._on_error(err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _capture_ocr(self):
        self._update_status("正在截屏... 请拖拽选择区域")
        self.root.withdraw()
        self.root.after(300, self._do_capture_ocr)

    def _do_capture_ocr(self):
        def on_region_selected(region, image):
            self.root.deiconify()
            self.root.lift()
            self._on_region_ocr(region, image)

        try:
            select_region(on_region_selected, master=self.root)
        except Exception as e:
            logger.exception(f"打开截屏选择层失败: {e}")
            self.root.deiconify()
            err_msg = str(e)
            self.root.after(0, lambda: self._on_error(f"截屏失败: {err_msg}"))

    def _on_region_ocr(self, region, image=None):
        if region is None:
            self._update_status("截屏已取消")
            return

        self._update_status("正在识别文字...")

        def process():
            try:
                # img 来自覆盖层里那张截图的裁剪（所见即所得）
                img = image
                if img is None:
                    img = capture_region(region)

                client = self.model.vision_client if self.config.get("ocr_method") in ("auto", "vision") else None
                vision_model = self.config.get("vision_model", "qwen3-vl-plus")
                ocr_method = self.config.get("ocr_method", "auto")

                text = extract_chat_text(img, method=ocr_method, client=client, vision_model=vision_model)

                self.root.after(0, lambda: self._on_ocr_done(text))
            except Exception as e:
                logger.exception(f"截屏 OCR 失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: self._on_error(err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _on_ocr_done(self, text: str):
        self.ocr_text.delete("1.0", tk.END)
        self.ocr_text.insert("1.0", text)
        self._update_status(f"识别完成，共 {len(text)} 个字符")

    def _copy_ocr_text(self):
        text = self.ocr_text.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._update_status("文本已复制到剪贴板")

    def _export_ocr_text(self):
        text = self.ocr_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("提示", "没有可导出的文本")
            return

        path = filedialog.asksaveasfilename(
            title="导出文本",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not path:
            return

        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self._update_status(f"已导出到: {path}")

    def _refresh_history(self):
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        sessions = self.storage.get_sessions()
        for s in sessions:
            self.history_tree.insert("", tk.END, iid=str(s["id"]),
                                      values=(s["title"], s["updated_at"][:16]))

    def _on_history_select(self, event):
        pass

    def _load_history_session(self):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个会话")
            return

        session_id = int(sel[0])
        messages = self.storage.get_session_messages(session_id)
        if messages:
            self.current_session_id = session_id
            all_text = []
            last_msg = None
            for msg in messages:
                all_text.append(msg["raw_text"])
                last_msg = msg

            self.recognition_text.delete("1.0", tk.END)
            self.recognition_text.insert("1.0", "\n\n---\n\n".join(all_text))

            for widget in self.recognition_suggestions_inner.winfo_children():
                widget.destroy()

            if last_msg:
                for i, sug in enumerate(last_msg.get("suggestions", []), 1):
                    card = ttk.Frame(self.recognition_suggestions_inner, relief=tk.RAISED, borderwidth=1)
                    card.pack(fill=tk.X, padx=5, pady=3)

                    header = ttk.Label(card, text=f"推荐 {i}", font=("Microsoft YaHei", 9, "bold"))
                    header.pack(anchor=tk.W, padx=5, pady=(3, 0))

                    content = tk.Text(card, wrap=tk.WORD, height=3, font=("Microsoft YaHei", 10),
                                      borderwidth=0, highlightthickness=0)
                    content.pack(fill=tk.X, padx=5, pady=3)
                    content.insert("1.0", sug["text"])
                    content.config(state=tk.DISABLED)

            self.notebook.select(1)
            self._update_status(f"已加载会话，共 {len(messages)} 条消息")

    def _delete_history_session(self):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个会话")
            return

        if messagebox.askyesno("确认", "确定要删除此会话记录吗？"):
            session_id = int(sel[0])
            self.storage.delete_session(session_id)
            if self.current_session_id == session_id:
                self.current_session_id = None
            self._refresh_history()

    def _collect_config(self) -> dict:
        """把「设置」面板上的当前值收集成配置字典"""
        config = dict(self.config)
        config.update({
            "api_provider": self.provider_var.get(),
            "api_key": self.api_key_var.get(),
            "api_base_url": self.base_url_var.get(),
            "text_model": self.text_model_var.get(),
            "vision_provider": self.vision_provider_var.get().strip(),
            "vision_api_key": self.vision_api_key_var.get().strip(),
            "vision_base_url": self.vision_base_url_var.get().strip(),
            "vision_model": self.vision_model_var.get(),
            "ocr_method": self.ocr_method_var.get(),
            "reply_count": self.reply_count_var.get(),
            "reply_style": self.reply_style_var.get(),
        })
        return config

    def _fetch_text_models(self):
        if not self.api_key_var.get():
            messagebox.showwarning("提示", "请先填写 API Key")
            return

        self._update_status("正在获取模型列表...")
        config = self._collect_config()

        def process():
            try:
                model_obj = ChatSightModel(config)
                if not model_obj.is_configured:
                    self.root.after(0, lambda: messagebox.showerror("错误", "API Key 未配置"))
                    return

                models = model_obj.fetch_models_from_api()
                if not models:
                    self.root.after(0, lambda: messagebox.showwarning("警告", "未能获取到模型列表"))
                    return

                self.root.after(0, lambda: self._on_models_fetched(models, "text"))
            except Exception as e:
                logger.exception(f"获取文本模型列表失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("错误", err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _fetch_vision_models(self):
        if not self.vision_api_key_var.get().strip() and not self.api_key_var.get():
            messagebox.showwarning("提示", "请先填写视觉 API Key（或上面的 API Key）")
            return

        self._update_status("正在获取视觉模型列表...")
        config = self._collect_config()

        def process():
            try:
                model_obj = ChatSightModel(config)
                if not model_obj.is_vision_configured:
                    self.root.after(0, lambda: messagebox.showerror("错误", "视觉 API Key 未配置"))
                    return

                models = model_obj.fetch_models_from_api(vision=True)
                if not models:
                    self.root.after(0, lambda: messagebox.showwarning("警告", "未能获取到模型列表"))
                    return

                self.root.after(0, lambda: self._on_models_fetched(models, "vision"))
            except Exception as e:
                logger.exception(f"获取视觉模型列表失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("错误", err_msg))

        threading.Thread(target=process, daemon=True).start()

    def _on_models_fetched(self, models: list, model_type: str):
        if model_type == "text":
            self.text_model_combo.config(values=models)
        else:
            self.vision_model_combo.config(values=models)

        self._update_status(f"已获取 {len(models)} 个模型（{model_type}），请从下拉框选择")
        messagebox.showinfo("成功", f"已获取 {len(models)} 个模型，请从下拉框中选择要用的那个")

    def _on_provider_changed(self, *args):
        provider = self.provider_var.get()
        text_models = PROVIDER_DEFAULTS.get(provider, {}).get("text_models", [])
        self.text_model_combo.config(values=text_models)
        if text_models:
            self.text_model_var.set(text_models[0])
        # 视觉没单独指定提供商时，跟随文本提供商
        if not self.vision_provider_var.get().strip():
            self._on_vision_provider_changed()

    def _on_vision_provider_changed(self, *args):
        provider = self.vision_provider_var.get().strip() or self.provider_var.get()
        vision_models = PROVIDER_DEFAULTS.get(provider, {}).get("vision_models", [])
        self.vision_model_combo.config(values=vision_models)
        if vision_models and self.vision_model_var.get() not in vision_models:
            self.vision_model_var.set(vision_models[0])

    def _save_settings(self):
        self.config = self._collect_config()
        save_config(self.config)
        self.model.update_config(self.config)
        self._update_status("设置已保存")
        messagebox.showinfo("成功", "设置已保存")

    def _on_error(self, error: str):
        logger.error(f"用户可见错误: {error}")
        self._update_status(f"错误: {error}")
        messagebox.showerror("错误", error)

    def run(self):
        self.root.mainloop()
