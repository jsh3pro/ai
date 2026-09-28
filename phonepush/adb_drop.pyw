import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from tkinterdnd2 import TkinterDnD, DND_FILES

# ===== 설정 =====
ADB = r"C:\Programs\scrcpy-win64-v4.1\adb.exe"   # adb.exe 위치
DEST = "/sdcard/Download/"                       # 폰에 저장할 폴더
ADB_OPTS = ["-d"]   # USB로 보내기. 무선만 쓰면 ["-e"], 기기가 하나뿐이면 []
# ================

NO_WINDOW = 0x08000000
jobs = queue.Queue()    # 복사할 파일 대기열
ui = queue.Queue()      # 화면 갱신 메시지
state = {"busy": False, "ok": 0, "fail": 0}


def adb(args, timeout=None):
    return subprocess.run(
        [ADB] + ADB_OPTS + args, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW, timeout=timeout)


def remote_size(path):
    """폰에 있는 파일 크기(바이트). 없으면 -1"""
    try:
        r = adb(["shell", "stat", "-c", "%s", path], timeout=5)
        return int(r.stdout.strip())
    except Exception:
        return -1


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def send(*msg):
    ui.put(msg)


def push_one(path):
    name = os.path.basename(path)
    size = os.path.getsize(path)
    ext = os.path.splitext(name)[1]
    tmp = DEST + f"_uploading_{int(time.time() * 1000)}{ext}"

    send("file", name, jobs.qsize())
    send("log", f"▶ 복사 시작: {name} ({size / 1024 / 1024:.0f}MB)")
    start = time.time()

    proc = subprocess.Popen(
        [ADB] + ADB_OPTS + ["push", path, tmp],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

    # 진행 상황: 폰에 쌓이는 파일 크기를 주기적으로 확인
    while proc.poll() is None:
        time.sleep(0.5)
        done = remote_size(tmp)
        if done > 0:
            send("progress", done, size, time.time() - start)

    out, err = proc.communicate()
    if proc.returncode != 0:
        adb(["shell", "rm", "-f", tmp])
        send("log", f"✖ 실패: {name}\n   {(err or out).strip()}")
        return False

    # 검증: 크기가 원본과 같은지
    got = remote_size(tmp)
    if got != size:
        adb(["shell", "rm", "-f", tmp])
        send("log", f"✖ 크기 불일치로 실패: {name} (원본 {size}, 폰 {got})")
        return False

    sec = time.time() - start
    send("progress", size, size, sec)

    # 원래 이름(한글)으로 바꾸기
    final = DEST + name
    r = adb(["shell", "mv", "-f", shell_quote(tmp), shell_quote(final)])
    if r.returncode == 0:
        send("log", f"✔ 완료: {final}  ({sec:.0f}초, {size / 1024 / 1024 / max(sec, 0.1):.1f}MB/s)")
    else:
        send("log", f"✔ 완료(이름 변경 실패): {tmp}\n   폰의 '내 파일' 앱에서 이름을 바꿔 주세요.")
    return True


def worker():
    while True:
        path = jobs.get()
        send("busy")
        try:
            ok = push_one(path)
        except Exception as e:
            send("log", f"✖ 오류: {e}")
            ok = False
        send("count", ok)

        if jobs.empty():   # 대기열이 비었으면 마무리
            send("syncing")
            try:
                adb(["shell", "sync"], timeout=120)
            except Exception:
                pass
            if jobs.empty():
                send("safe")


# ---------- 화면 ----------
def set_status(text, bg):
    status.config(text=text, bg=bg)


def poll_ui():
    while not ui.empty():
        kind, *a = ui.get()
        if kind == "log":
            box.insert(tk.END, a[0] + "\n")
            box.see(tk.END)
        elif kind == "busy":
            state["busy"] = True
            set_status("⛔ 복사 중입니다 — USB를 빼지 마세요", "#ff6b6b")
        elif kind == "file":
            wait = f"   (대기 {a[1]}개)" if a[1] else ""
            file_lbl.config(text=f"현재 파일: {a[0]}{wait}")
            bar["value"] = 0
            detail.config(text="준비 중...")
        elif kind == "progress":
            done, size, sec = a
            pct = done / size * 100 if size else 100
            speed = done / max(sec, 0.1) / 1024 / 1024
            remain = (size - done) / 1024 / 1024 / speed if speed > 0 else 0
            bar["value"] = pct
            detail.config(text=f"{pct:5.1f}%   {done / 1024 / 1024:,.0f} / {size / 1024 / 1024:,.0f} MB"
                               f"   {speed:.1f} MB/s   남은 시간 약 {remain:.0f}초")
        elif kind == "count":
            state["ok" if a[0] else "fail"] += 1
        elif kind == "syncing":
            set_status("⏳ 저장 마무리 중 — 아직 빼지 마세요", "#ffa94d")
        elif kind == "safe":
            state["busy"] = False
            ok, fail = state["ok"], state["fail"]
            state["ok"] = state["fail"] = 0
            if fail:
                set_status(f"⚠ 성공 {ok}개, 실패 {fail}개 — USB는 빼도 됩니다 (기록 확인)", "#ffe066")
            else:
                set_status(f"✅ 완료! ({ok}개) 이제 USB를 빼도 됩니다", "#69db7c")
            file_lbl.config(text="파일을 더 끌어다 놓으면 이어서 복사합니다")
            box.insert(tk.END, "----- 모든 작업 끝: 안전하게 분리 가능 -----\n")
            box.see(tk.END)
            root.bell()
    root.after(100, poll_ui)


def on_drop(event):
    for p in root.tk.splitlist(event.data):
        if os.path.isfile(p):
            jobs.put(p)
            if state["busy"]:
                box.insert(tk.END, f"＋ 대기열에 추가: {os.path.basename(p)}\n")
        else:
            box.insert(tk.END, f"건너뜀(폴더는 지원 안 함): {p}\n")
    box.see(tk.END)


def on_close():
    if state["busy"]:
        set_status("⛔ 복사 중에는 창을 닫을 수 없습니다 — 끝날 때까지 기다려 주세요", "#ff6b6b")
    else:
        root.destroy()


root = TkinterDnD.Tk()
root.title("폰으로 파일 보내기 (adb)")
root.geometry("680x460")

status = tk.Label(root, text="여기에 파일을 끌어다 놓으세요",
                  font=("맑은 고딕", 14, "bold"), bg="#dfefff", height=2)
status.pack(fill=tk.X)
file_lbl = tk.Label(root, text="", font=("맑은 고딕", 10), anchor="w")
file_lbl.pack(fill=tk.X, padx=10, pady=(8, 0))
bar = ttk.Progressbar(root, maximum=100)
bar.pack(fill=tk.X, padx=10, pady=4)
detail = tk.Label(root, text="", font=("Consolas", 10), anchor="w")
detail.pack(fill=tk.X, padx=10)
box = scrolledtext.ScrolledText(root, font=("맑은 고딕", 10), height=12)
box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

root.drop_target_register(DND_FILES)
root.dnd_bind("<<Drop>>", on_drop)
root.protocol("WM_DELETE_WINDOW", on_close)

r = adb(["get-state"])
box.insert(tk.END, "폰 연결됨 ✔\n" if r.returncode == 0
           else "폰이 연결되지 않았습니다. 연결을 확인하세요.\n")

threading.Thread(target=worker, daemon=True).start()
poll_ui()
root.mainloop()
