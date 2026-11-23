import os
import time
import base64
import threading
import subprocess
import http.server
import socketserver
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk
from playwright.sync_api import sync_playwright

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


ENCODER_OPTIONS = {
    "Auto (Detectar Melhor Hardware)": "auto",
    "NVIDIA NVENC H.264 (h264_nvenc)": "h264_nvenc",
    "NVIDIA NVENC H.265/HEVC (hevc_nvenc)": "hevc_nvenc",
    "AMD AMF H.264 (h264_amf)": "h264_amf",
    "AMD AMF H.265/HEVC (hevc_amf)": "hevc_amf",
    "Intel QuickSync H.264 (h264_qsv)": "h264_qsv",
    "Intel QuickSync H.265/HEVC (hevc_qsv)": "hevc_qsv",
    "CPU Universal H.264 (libx264)": "libx264",
    "CPU Alta Compressão H.265 (libx265)": "libx265",
}


def get_ffmpeg_bin() -> str:
    app_dir = Path(__file__).resolve().parent
    ffmpeg_local = app_dir / "ffmpeg.exe"
    return str(ffmpeg_local) if ffmpeg_local.exists() else "ffmpeg"


def probe_available_encoders(ffmpeg_bin: str) -> list[str]:
    """Testa quais encoders de hardware realmente funcionam na máquina atual."""
    candidates = ["h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv"]
    working = []
    for enc in candidates:
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=black:s=256x256:d=0.1",
            "-c:v", enc,
            "-f", "null",
            "-",
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            if res.returncode == 0:
                working.append(enc)
        except Exception:
            pass
    working.extend(["libx264", "libx265"])
    return working


def build_ffmpeg_video_args(encoder: str) -> list[str]:
    """Retorna os parâmetros ideais de qualidade e compatibilidade para cada encoder."""
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "17", "-b:v", "0", "-pix_fmt", "yuv420p"]
    if encoder == "hevc_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "p4", "-cq", "19", "-b:v", "0", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "17", "-qp_p", "17", "-pix_fmt", "yuv420p"]
    if encoder == "hevc_amf":
        return ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "18", "-pix_fmt", "yuv420p"]
    if encoder == "hevc_qsv":
        return ["-c:v", "hevc_qsv", "-preset", "medium", "-global_quality", "20", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    if encoder == "libx265":
        return ["-c:v", "libx265", "-preset", "fast", "-crf", "20", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p"]


class HtmlToMp4App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("HTML5 Motion & Audio to MP4 Studio — Multi-Hardware")
        self.geometry("660x590")
        self.resizable(False, False)

        self.html_path = ctk.StringVar()
        self.output_path = ctk.StringVar()
        self.duration_var = ctk.StringVar(value="45")
        self.fps_var = ctk.StringVar(value="60")
        self.res_var = ctk.StringVar(value="1080x1920")
        self.encoder_var = ctk.StringVar(value="Auto (Detectar Melhor Hardware)")
        self.available_encoders = ["libx264", "libx265"]
        self.is_rendering = False

        self._build_ui()
        threading.Thread(target=self._detect_hardware_bg, daemon=True).start()

    def _build_ui(self):
        header = ctk.CTkLabel(
            self, text="HTML5 + Áudio ➔ MP4 Studio", font=ctk.CTkFont(size=20, weight="bold")
        )
        header.pack(pady=(18, 4))

        sub = ctk.CTkLabel(
            self, text="Captura Canvas + WebAudio ➔ NVENC / AMF / QuickSync / CPU", text_color="gray"
        )
        sub.pack(pady=(0, 14))

        # Frame de Arquivos
        file_frame = ctk.CTkFrame(self)
        file_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(file_frame, text="Arquivo HTML:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        ctk.CTkEntry(file_frame, textvariable=self.html_path, width=350).grid(row=0, column=1, padx=5, pady=10)
        ctk.CTkButton(file_frame, text="Procurar", width=90, command=self._select_html).grid(row=0, column=2, padx=10, pady=10)

        ctk.CTkLabel(file_frame, text="Salvar MP4 em:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        ctk.CTkEntry(file_frame, textvariable=self.output_path, width=350).grid(row=1, column=1, padx=5, pady=10)
        ctk.CTkButton(file_frame, text="Destino", width=90, command=self._select_output).grid(row=1, column=2, padx=10, pady=10)

        # Frame de Vídeo
        cfg_frame = ctk.CTkFrame(self)
        cfg_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(cfg_frame, text="Resolução:").grid(row=0, column=0, padx=10, pady=12)
        ctk.CTkOptionMenu(
            cfg_frame,
            variable=self.res_var,
            values=["1080x1920", "720x1280", "1440x2560", "1920x1080", "1280x720", "1080x1080"],
            width=125,
        ).grid(row=0, column=1, padx=5, pady=12)

        ctk.CTkLabel(cfg_frame, text="FPS:").grid(row=0, column=2, padx=10, pady=12)
        ctk.CTkOptionMenu(
            cfg_frame, variable=self.fps_var, values=["30", "60", "120"], width=80
        ).grid(row=0, column=3, padx=5, pady=12)

        ctk.CTkLabel(cfg_frame, text="Duração (s):").grid(row=0, column=4, padx=10, pady=12)
        ctk.CTkEntry(cfg_frame, textvariable=self.duration_var, width=65).grid(row=0, column=5, padx=10, pady=12)

        # Frame de Codificação / Hardware
        enc_frame = ctk.CTkFrame(self)
        enc_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(enc_frame, text="Codificador (Codec):").grid(row=0, column=0, padx=10, pady=12, sticky="w")
        self.encoder_menu = ctk.CTkOptionMenu(
            enc_frame,
            variable=self.encoder_var,
            values=list(ENCODER_OPTIONS.keys()),
            width=430,
        )
        self.encoder_menu.grid(row=0, column=1, padx=10, pady=12, sticky="e")

        self.hw_badge = ctk.CTkLabel(
            self, text="Detectando aceleradores de vídeo da máquina...", text_color="#6ab0f3", font=ctk.CTkFont(size=12)
        )
        self.hw_badge.pack(pady=(4, 4))

        self.btn_render = ctk.CTkButton(
            self,
            text="RENDERIZAR VÍDEO MP4 (COM SOM)",
            height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#1f6aa5",
            hover_color="#144870",
            command=self._start_render_thread,
        )
        self.btn_render.pack(fill="x", padx=25, pady=(10, 10))

        self.progress = ctk.CTkProgressBar(self)
        self.progress.pack(fill="x", padx=25, pady=8)
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(self, text="Pronto para iniciar.", text_color="#a0a0a0")
        self.status_label.pack(pady=4)

    def _detect_hardware_bg(self):
        ffmpeg_bin = get_ffmpeg_bin()
        detected = probe_available_encoders(ffmpeg_bin)
        self.available_encoders = detected
        best = detected[0] if detected else "libx264"
        label_list = ", ".join(detected)
        self.after(
            0,
            lambda: self.hw_badge.configure(
                text=f"Disponíveis nesta máquina: {label_list} (Auto usará: {best})"
            ),
        )

    def _select_html(self):
        path = filedialog.askopenfilename(filetypes=[("Arquivos HTML", "*.html;*.htm")])
        if path:
            self.html_path.set(path)
            if not self.output_path.get():
                default_out = str(Path(path).with_suffix(".mp4"))
                self.output_path.set(default_out)

    def _select_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("Vídeo MP4", "*.mp4")])
        if path:
            self.output_path.set(path)

    def _update_status(self, text, progress_val=None):
        self.status_label.configure(text=text)
        if progress_val is not None:
            self.progress.set(progress_val)

    def _resolve_target_encoder(self) -> str:
        selected_label = self.encoder_var.get()
        chosen = ENCODER_OPTIONS.get(selected_label, "auto")
        if chosen == "auto":
            for pref in ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"]:
                if pref in self.available_encoders:
                    return pref
            return "libx264"
        return chosen

    def _start_render_thread(self):
        if self.is_rendering:
            return
        if not self.html_path.get() or not os.path.exists(self.html_path.get()):
            messagebox.showerror("Erro", "Selecione um arquivo HTML válido.")
            return
        if not self.output_path.get():
            messagebox.showerror("Erro", "Defina onde salvar o arquivo MP4.")
            return

        self.is_rendering = True
        self.btn_render.configure(state="disabled", text="RENDERIZANDO...")
        threading.Thread(target=self._render_process, daemon=True).start()

    def _render_process(self):
        html_file = Path(self.html_path.get()).resolve()
        out_file = Path(self.output_path.get()).resolve()
        width, height = map(int, self.res_var.get().split("x"))
        fps = int(self.fps_var.get())
        duration = float(self.duration_var.get())
        target_encoder = self._resolve_target_encoder()
        ffmpeg_bin = get_ffmpeg_bin()

        temp_dir = out_file.parent / "_temp_render_html2mp4"
        temp_dir.mkdir(exist_ok=True)
        raw_webm = temp_dir / "capture_stream.webm"
        prev_cwd = os.getcwd()
        chunk_file = None

        try:
            self._update_status("Subindo servidor local para assets e WebAudio...", 0.05)

            os.chdir(html_file.parent)
            httpd = socketserver.TCPServer(("127.0.0.1", 0), SilentHandler)
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

            url = f"http://127.0.0.1:{port}/{html_file.name}"
            chunk_file = open(raw_webm, "wb")

            def receive_chunk(b64_data: str):
                if b64_data and chunk_file:
                    chunk_file.write(base64.b64decode(b64_data))

            self._update_status("Iniciando Chromium com aceleração gráfica...", 0.15)

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--use-gl=angle",
                        "--use-angle=d3d11",
                        "--enable-webgl",
                        "--ignore-gpu-blocklist",
                        "--disable-gpu-vsync",
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                        f"--window-size={width},{height}",
                    ],
                )

                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )

                context.expose_function("pySendChunk", receive_chunk)

                page = context.new_page()
                page.goto(url, wait_until="networkidle")

                page.evaluate(f"""() => {{
                    const cv = document.getElementById('c');
                    if (cv) {{
                        cv.width = {width};
                        cv.height = {height};
                        if (typeof sc !== 'undefined' && typeof W !== 'undefined') {{
                            sc = cv.width / W;
                        }}
                    }}
                }}""")

                btn = page.locator("#s")
                if btn.count() > 0:
                    btn.click()
                else:
                    page.mouse.click(width // 2, height // 2)

                time.sleep(0.2)

                page.evaluate(f"""async () => {{
                    const cv = document.getElementById('c') || document.querySelector('canvas');
                    const stream = cv.captureStream({fps});

                    if (typeof rd !== 'undefined' && rd && rd.stream) {{
                        rd.stream.getAudioTracks().forEach(t => stream.addTrack(t));
                    }} else if (typeof A !== 'undefined' && A && A.createMediaStreamDestination) {{
                        const dest = A.createMediaStreamDestination();
                        if (typeof mg !== 'undefined' && mg) mg.connect(dest);
                        dest.stream.getAudioTracks().forEach(t => stream.addTrack(t));
                    }}

                    const mime = [
                        'video/webm;codecs=vp8,opus',
                        'video/webm;codecs=vp9,opus',
                        'video/webm'
                    ].find(m => MediaRecorder.isTypeSupported(m)) || 'video/webm';

                    const rec = new MediaRecorder(stream, {{
                        mimeType: mime,
                        videoBitsPerSecond: 16000000,
                        audioBitsPerSecond: 256000
                    }});

                    window.__mediaRec = rec;
                    rec.ondataavailable = async (ev) => {{
                        if (ev.data && ev.data.size > 0) {{
                            const buf = await ev.data.arrayBuffer();
                            const bytes = new Uint8Array(buf);
                            let bin = '';
                            const step = 0x8000;
                            for (let i = 0; i < bytes.length; i += step) {{
                                bin += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
                            }}
                            await window.pySendChunk(btoa(bin));
                        }}
                    }};
                    rec.start(500);
                }}""")

                steps = max(1, int(duration * 10))
                for i in range(steps):
                    time.sleep(0.1)
                    pct = 0.15 + (0.60 * ((i + 1) / steps))
                    self._update_status(f"Gravando animação + som ({fps} FPS): {(i+1)/10:.1f}s / {duration:.0f}s", pct)

                page.evaluate("""async () => {
                    if (window.__mediaRec && window.__mediaRec.state !== 'inactive') {
                        await new Promise(resolve => {
                            window.__mediaRec.onstop = resolve;
                            window.__mediaRec.stop();
                        });
                    }
                }""")
                time.sleep(0.5)

                context.close()
                browser.close()

            chunk_file.close()
            chunk_file = None
            httpd.shutdown()
            httpd.server_close()
            os.chdir(prev_cwd)

            self._update_status(f"Codificando MP4 ({target_encoder}) {width}x{height} @ {fps} FPS...", 0.85)

            v_args = build_ffmpeg_video_args(target_encoder)
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", str(raw_webm),
                "-vf", f"scale={width}:{height}:flags=lanczos,fps={fps}",
                *v_args,
                "-c:a", "aac",
                "-b:a", "256k",
                "-ar", "48000",
                str(out_file),
            ]

            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                if target_encoder != "libx264":
                    self._update_status(f"Falha em {target_encoder}, aplicando fallback CPU (libx264)...", 0.90)
                    fallback_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(raw_webm),
                        "-vf", f"scale={width}:{height}:flags=lanczos,fps={fps}",
                        *build_ffmpeg_video_args("libx264"),
                        "-c:a", "aac",
                        "-b:a", "256k",
                        "-ar", "48000",
                        str(out_file),
                    ]
                    subprocess.run(fallback_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    raise

            if raw_webm.exists():
                raw_webm.unlink(missing_ok=True)
            if temp_dir.exists():
                for item in temp_dir.iterdir():
                    item.unlink(missing_ok=True)
                temp_dir.rmdir()

            self._update_status(f"Concluído com {target_encoder} + som!", 1.0)
            messagebox.showinfo("Sucesso", f"Vídeo gerado com codec [{target_encoder}] em:\n{out_file}")

        except Exception as e:
            os.chdir(prev_cwd)
            if chunk_file:
                try:
                    chunk_file.close()
                except Exception:
                    pass
            self._update_status("Erro durante a renderização.", 0)
            messagebox.showerror("Falha na Renderização", f"Detalhes técnicos:\n{str(e)}")

        finally:
            self.is_rendering = False
            self.btn_render.configure(state="normal", text="RENDERIZAR VÍDEO MP4 (COM SOM)")


if __name__ == "__main__":
    app = HtmlToMp4App()
    app.mainloop()
