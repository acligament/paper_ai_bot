import os
import re
import requests
import feedparser
import fitz  # PyMuPDF
from datetime import datetime
from urllib.parse import quote_plus

# ================================
# Gemini API（RESTモード強制）
# ================================
import google.generativeai as genai

os.environ["GOOGLE_API_USE_REST"] = "true"     # ★ gRPC 無効化（503防止）
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-pro")

# ================================
# Movie 作成用
# ================================
from PIL import Image, ImageDraw
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

SAVE_DIR = "outputs"
os.makedirs(SAVE_DIR, exist_ok=True)

# ================================
# ファイル名を安全にする
# ================================
def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\r\n]', "_", name)
    return re.sub(r"_+", "_", name).strip("_")

# ================================
# ① arXiv 最新AI論文取得
# ================================
def fetch_arxiv_papers():
    raw_query = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:stat.ML"
    encoded = quote_plus(raw_query)

    url = (
        "http://export.arxiv.org/api/query?"
        f"search_query={encoded}&start=0&max_results=3&sortBy=submittedDate&sortOrder=descending"
    )

    feed = feedparser.parse(url)
    return feed.entries

# ================================
# ② PDF ダウンロード
# ================================
def download_pdf(pdf_url, filename):
    try:
        res = requests.get(pdf_url, timeout=20)
        res.raise_for_status()
    except Exception as e:
        print(f"PDF download failed: {e}")
        return None

    path = os.path.join(SAVE_DIR, filename)
    with open(path, "wb") as f:
        f.write(res.content)
    return path

# ================================
# ③ PDF → テキスト
# ================================
def extract_text(pdf_path):
    if not pdf_path:
        return ""

    try:
        doc = fitz.open(pdf_path)
        text = "".join([p.get_text() for p in doc])
        return text
    except:
        return ""

# ================================
# ④ Gemini による日本語要約
# ================================
def summarize_text_ja(text):
    MAX_CHARS = 5000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    prompt = f"""
以下の英語論文の内容を、日本語で簡潔に3点に要約してください。

{text}
"""
    response = model.generate_content(prompt)
    return response.text.strip()

# ================================
# ⑤ VOICEVOX（四国めたん）
# ================================
def get_speaker_id(name="四国めたん", style="ノーマル"):
    data = requests.get("http://localhost:50021/speakers").json()
    for sp in data:
        if sp["name"] == name:
            for st in sp["styles"]:
                if st["name"] == style:
                    return st["id"]
    return None

def voicevox_tts(text, filename, speed=1.1):
    speaker = get_speaker_id()
    if speaker is None:
        raise RuntimeError("四国めたんが見つかりません")

    cleaned = text.replace("**", "")

    query = requests.post(
        "http://localhost:50021/audio_query",
        params={"text": cleaned, "speaker": speaker}
    ).json()

    query["speedScale"] = speed

    audio = requests.post(
        "http://localhost:50021/synthesis",
        params={"speaker": speaker},
        json=query
    )

    path = os.path.join(SAVE_DIR, filename)
    with open(path, "wb") as f:
        f.write(audio.content)

    return path

# ================================
# ⑥ スライド構成
# ================================
def build_slide_structure(title, summary):
    prompt = f"""
次の論文について、動画用5スライドに整理してください：

1. TITLE
2. PURPOSE
3. METHOD
4. RESULT
5. CONCLUSION

タイトル:
{title}

要約:
{summary}
"""

    res = model.generate_content(prompt).text
    slides = {}
    for line in res.split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            slides[key.strip()] = val.strip()
    return slides

# ================================
# ⑦ 画像生成（Pillow）
# ================================
def create_slide_image(text, filename):
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    draw.multiline_text((100, 100), text, fill="black", spacing=20)

    img.save(filename)
    return filename

# ================================
# ⑧ MoviePy で動画作成
# ================================
def generate_video(slide_paths, audio_path, output_path):
    clips = [ImageClip(p).set_duration(4) for p in slide_paths]
    video = concatenate_videoclips(clips)
    audio = AudioFileClip(audio_path)

    final = video.set_audio(audio)
    final.write_videofile(output_path, fps=24)
    return output_path

# ================================
# MAIN
# ================================
def main():

    print("📥 Fetching AI papers...")
    papers = fetch_arxiv_papers()

    if not papers:
        print("No papers found.")
        return

    # 1本だけ動画化する
    entry = papers[0]
    raw_title = entry.title
    print(f"\n▶ Processing: {raw_title}")

    filename = safe_filename(raw_title)
    pdf_url = entry.id.replace("abs", "pdf") + ".pdf"
    pdf_path = download_pdf(pdf_url, f"{filename}.pdf")

    text = extract_text(pdf_path)
    summary = summarize_text_ja(text)

    # スライド構成
    structure = build_slide_structure(raw_title, summary)

    slide_files = []
    for key in ["TITLE", "PURPOSE", "METHOD", "RESULT", "CONCLUSION"]:
        msg = f"{key}\n\n{structure.get(key, '')}"
        path = os.path.join(SAVE_DIR, f"{key}.png")
        create_slide_image(msg, path)
        slide_files.append(path)

    # ナレーション
    today = datetime.utcnow().strftime("%Y%m%d")
    audio_path = voicevox_tts(summary, f"narration_{today}.wav")

    # 動画生成
    video_path = os.path.join(SAVE_DIR, f"paper_video_{today}.mp4")
    generate_video(slide_files, audio_path, video_path)

    print(f"\n🎉 完成！ → {video_path}")

if __name__ == "__main__":
    main()
