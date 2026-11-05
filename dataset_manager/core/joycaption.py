"""JoyCaption Beta One / Alpha Two captioning: GGUF through llama.cpp, or Hugging Face weights through transformers.

The prompt tables below are JoyCaption's own (copied from joycaption-batch-webui/app.py).
"""
from __future__ import annotations

import base64
import io
import threading

from . import models

CAPTION_TYPE_MAP = {
    "Descriptive": [
        "Write a detailed description for this image.",
        "Write a detailed description for this image in {word_count} words or less.",
        "Write a {length} detailed description for this image.",
    ],
    "Descriptive (Casual)": [
        "Write a descriptive caption for this image in a casual tone.",
        "Write a descriptive caption for this image in a casual tone within {word_count} words.",
        "Write a {length} descriptive caption for this image in a casual tone.",
    ],
    "Straightforward": [
        "Write a straightforward caption for this image. Begin with the main subject and medium. Mention pivotal elements—people, objects, scenery—using confident, definite language. Focus on concrete details like color, shape, texture, and spatial relationships. Show how elements interact. Omit mood and speculative wording. If text is present, quote it exactly. Note any watermarks, signatures, or compression artifacts. Never mention what's absent, resolution, or unobservable details. Vary your sentence structure and keep the description concise, without starting with \"This image is…\" or similar phrasing.",
        "Write a straightforward caption for this image within {word_count} words. Begin with the main subject and medium. Mention pivotal elements—people, objects, scenery—using confident, definite language. Focus on concrete details like color, shape, texture, and spatial relationships. Show how elements interact. Omit mood and speculative wording. If text is present, quote it exactly. Note any watermarks, signatures, or compression artifacts. Never mention what's absent, resolution, or unobservable details. Vary your sentence structure and keep the description concise, without starting with \"This image is…\" or similar phrasing.",
        "Write a {length} straightforward caption for this image. Begin with the main subject and medium. Mention pivotal elements—people, objects, scenery—using confident, definite language. Focus on concrete details like color, shape, texture, and spatial relationships. Show how elements interact. Omit mood and speculative wording. If text is present, quote it exactly. Note any watermarks, signatures, or compression artifacts. Never mention what's absent, resolution, or unobservable details. Vary your sentence structure and keep the description concise, without starting with \"This image is…\" or similar phrasing.",
    ],
    "Stable Diffusion Prompt": [
        "Output a stable diffusion prompt that is indistinguishable from a real stable diffusion prompt.",
        "Output a stable diffusion prompt that is indistinguishable from a real stable diffusion prompt. {word_count} words or less.",
        "Output a {length} stable diffusion prompt that is indistinguishable from a real stable diffusion prompt.",
    ],
    "MidJourney": [
        "Write a MidJourney prompt for this image.",
        "Write a MidJourney prompt for this image within {word_count} words.",
        "Write a {length} MidJourney prompt for this image.",
    ],
    "Danbooru tag list": [
        "Generate only comma-separated Danbooru tags (lowercase_underscores). Strict order: `artist:`, `copyright:`, `character:`, `meta:`, then general tags. Include counts (1girl), appearance, clothing, accessories, pose, expression, actions, background. Use precise Danbooru syntax. No extra text.",
        "Generate only comma-separated Danbooru tags (lowercase_underscores). Strict order: `artist:`, `copyright:`, `character:`, `meta:`, then general tags. Include counts (1girl), appearance, clothing, accessories, pose, expression, actions, background. Use precise Danbooru syntax. No extra text. {word_count} words or less.",
        "Generate only comma-separated Danbooru tags (lowercase_underscores). Strict order: `artist:`, `copyright:`, `character:`, `meta:`, then general tags. Include counts (1girl), appearance, clothing, accessories, pose, expression, actions, background. Use precise Danbooru syntax. No extra text. {length} length.",
    ],
    "e621 tag list": [
        "Write a comma-separated list of e621 tags in alphabetical order for this image. Start with the artist, copyright, character, species, meta, and lore tags (if any), prefixed by 'artist:', 'copyright:', 'character:', 'species:', 'meta:', and 'lore:'. Then all the general tags.",
        "Write a comma-separated list of e621 tags in alphabetical order for this image. Start with the artist, copyright, character, species, meta, and lore tags (if any), prefixed by 'artist:', 'copyright:', 'character:', 'species:', 'meta:', and 'lore:'. Then all the general tags. Keep it under {word_count} words.",
        "Write a {length} comma-separated list of e621 tags in alphabetical order for this image. Start with the artist, copyright, character, species, meta, and lore tags (if any), prefixed by 'artist:', 'copyright:', 'character:', 'species:', 'meta:', and 'lore:'. Then all the general tags.",
    ],
    "Rule34 tag list": [
        "Write a comma-separated list of rule34 tags in alphabetical order for this image. Start with the artist, copyright, character, and meta tags (if any), prefixed by 'artist:', 'copyright:', 'character:', and 'meta:'. Then all the general tags.",
        "Write a comma-separated list of rule34 tags in alphabetical order for this image. Start with the artist, copyright, character, and meta tags (if any), prefixed by 'artist:', 'copyright:', 'character:', and 'meta:'. Then all the general tags. Keep it under {word_count} words.",
        "Write a {length} comma-separated list of rule34 tags in alphabetical order for this image. Start with the artist, copyright, character, and meta tags (if any), prefixed by 'artist:', 'copyright:', 'character:', and 'meta:'. Then all the general tags.",
    ],
    "Booru-like tag list": [
        "Write a list of Booru-like tags for this image.",
        "Write a list of Booru-like tags for this image within {word_count} words.",
        "Write a {length} list of Booru-like tags for this image.",
    ],
    "Art Critic": [
        "Analyze this image like an art critic would with information about its composition, style, symbolism, the use of color, light, any artistic movement it might belong to, etc.",
        "Analyze this image like an art critic would with information about its composition, style, symbolism, the use of color, light, any artistic movement it might belong to, etc. Keep it within {word_count} words.",
        "Analyze this image like an art critic would with information about its composition, style, symbolism, the use of color, light, any artistic movement it might belong to, etc. Keep it {length}.",
    ],
    "Product Listing": [
        "Write a caption for this image as though it were a product listing.",
        "Write a caption for this image as though it were a product listing. Keep it under {word_count} words.",
        "Write a {length} caption for this image as though it were a product listing.",
    ],
    "Social Media Post": [
        "Write a caption for this image as if it were being used for a social media post.",
        "Write a caption for this image as if it were being used for a social media post. Limit the caption to {word_count} words.",
        "Write a {length} caption for this image as if it were being used for a social media post.",
    ],
}

NAME_OPTION = "If there is a person/character in the image you must refer to them as {name}."

EXTRA_OPTIONS_LIST = [
    "Do NOT include information about people/characters that cannot be changed (like ethnicity, gender, etc), but do still include changeable attributes (like hair style).",
    "Include information about lighting.",
    "Include information about camera angle.",
    "Include information about whether there is a watermark or not.",
    "Include information about whether there are JPEG artifacts or not.",
    "If it is a photo you MUST include information about what camera was likely used and details such as aperture, shutter speed, ISO, etc.",
    "Do NOT include anything sexual; keep it PG.",
    "Do NOT mention the image's resolution.",
    "You MUST include information about the subjective aesthetic quality of the image from low to very high.",
    "Include information on the image's composition style, such as leading lines, rule of thirds, or symmetry.",
    "Do NOT mention any text that is in the image.",
    "Specify the depth of field and whether the background is in focus or blurred.",
    "If applicable, mention the likely use of artificial or natural lighting sources.",
    "Do NOT use any ambiguous language.",
    "Include whether the image is sfw, suggestive, or nsfw.",
    "ONLY describe the most important elements of the image.",
]

LENGTHS = ["any", "very short", "short", "medium-length", "long", "very long"] + [str(i) for i in range(20, 261, 10)]
SYSTEM = "You are a helpful image captioner."


def build_prompt(caption_type: str, length: str, extras: list[str], name: str = "") -> str:
    templates = CAPTION_TYPE_MAP.get(caption_type) or CAPTION_TYPE_MAP["Descriptive"]
    idx = 0 if length == "any" else 1 if str(length).isdigit() else 2
    prompt = templates[idx]
    extras = ([NAME_OPTION] if name.strip() else []) + [e for e in extras if e != NAME_OPTION]
    if extras:
        prompt += " " + " ".join(extras)
    return prompt.format(name=name.strip(), length=length, word_count=length)


def _thumb(path: str, side: int = 768):
    """RGB image, downscaled: JoyCaption sees 384 px, so bigger images only cost time."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.draft("RGB", (side, side))
        im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((side, side))
    return im


class GGUFCaptioner:
    def __init__(self, model: str, mmproj: str):
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Llava15ChatHandler

        class JoyCaptionChatHandler(Llava15ChatHandler):
            DEFAULT_SYSTEM_MESSAGE = None
            CHAT_FORMAT = (
                "{% for message in messages %}"
                "<|start_header_id|>{{ message.role }}<|end_header_id|>\n\n"
                "{% if message.content is string %}{{ message.content }}{% else %}"
                "{% for content in message.content %}"
                "{% if content.type == 'image_url' and content.image_url is string %}{{ content.image_url }}{% endif %}"
                "{% if content.type == 'image_url' and content.image_url is mapping %}{{ content.image_url.url }}{% endif %}"
                "{% endfor %}"
                "{% for content in message.content %}"
                "{% if content.type == 'text' %}{{ content.text }}{% endif %}"
                "{% endfor %}"
                "{% endif %}"
                "<|eot_id|>"
                "{% endfor %}"
                "{% if add_generation_prompt %}<|start_header_id|>assistant<|end_header_id|>\n\n{% endif %}"
            )

        import os
        self.llm = Llama(model_path=model, chat_handler=JoyCaptionChatHandler(clip_model_path=mmproj, verbose=False),
                         n_ctx=4096, n_gpu_layers=-1, n_threads=max(1, (os.cpu_count() or 2) - 1), verbose=False)

    def caption(self, path: str, prompt: str, temperature: float, max_tokens: int, cancelled=lambda: False) -> str:
        buf = io.BytesIO()
        _thumb(path).save(buf, "PNG")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}},
                                                 {"type": "text", "text": prompt}]}]
        out = ""
        for chunk in self.llm.create_chat_completion(
                messages=messages, stream=True, max_tokens=int(max_tokens), temperature=float(temperature),
                top_p=0.9 if temperature > 0 else 1.0, top_k=0, repeat_penalty=1.0, stop=["<|eot_id|>"]):
            out += chunk["choices"][0]["delta"].get("content") or ""
            if cancelled():
                break
        return out.strip()

    def close(self):
        try:
            self.llm.close()
        except Exception:
            pass


class HFCaptioner:
    def __init__(self, folder: str, log=print):
        import torch
        from transformers import AutoProcessor, LlavaForConditionalGeneration
        from . import capabilities
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(folder)
        kw = dict(torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)
        if torch.cuda.is_available():
            kw["device_map"] = "auto"
            free = torch.cuda.mem_get_info()[0] / 2**30
            if capabilities.has("bitsandbytes") and free < 20:
                from transformers import BitsAndBytesConfig
                log(f"{free:.0f} GB of VRAM free: loading in 4-bit.")
                kw["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                    llm_int8_skip_modules=["vision_tower", "multi_modal_projector"])
            elif free < 20:
                log(f"Only {free:.0f} GB of VRAM free; part of the model runs on the CPU (slow). "
                    "Install bitsandbytes in Preferences → System for 4-bit mode.")
        self.model = LlavaForConditionalGeneration.from_pretrained(folder, **kw).eval()

    def caption(self, path: str, prompt: str, temperature: float, max_tokens: int, cancelled=lambda: False) -> str:
        torch = self.torch
        convo = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        text = self.processor.apply_chat_template(convo, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text], images=[_thumb(path)], return_tensors="pt").to(self.model.device)
        inputs["pixel_values"] = inputs["pixel_values"].to(torch.bfloat16)
        with torch.inference_mode():
            ids = self.model.generate(**inputs, max_new_tokens=int(max_tokens), do_sample=temperature > 0,
                                      temperature=float(temperature) if temperature > 0 else None,
                                      top_p=0.9 if temperature > 0 else None, top_k=None, use_cache=True)[0]
        ids = ids[inputs["input_ids"].shape[1]:]
        return self.processor.tokenizer.decode(ids, skip_special_tokens=True,
                                               clean_up_tokenization_spaces=False).strip()

    def close(self):
        self.model = None


_lock = threading.Lock()
_loaded: dict = {}


def get(settings, log=print):
    found = models.find_joycaption(settings)
    if not found:
        raise FileNotFoundError("JoyCaption isn't downloaded. Set its folder or download it in Preferences → Models.")
    key = found.get("model") or found["path"]
    with _lock:
        if _loaded.get("key") == key:
            return _loaded["cap"]
        unload()
        from . import capabilities, taggers
        taggers.unload()                         # share the GPU: one big model at a time
        if found["kind"] == "gguf":
            if not capabilities.has("llama_cpp"):
                raise RuntimeError("GGUF JoyCaption needs llama-cpp-python: install it in Preferences → System.")
            log(f"Loading JoyCaption {key}…")
            cap = GGUFCaptioner(found["model"], found["mmproj"])
        else:
            if not capabilities.has("accelerate"):
                raise RuntimeError("JoyCaption needs accelerate + transformers: install them in Preferences → System.")
            log(f"Loading JoyCaption from {key} (first load takes a while)…")
            cap = HFCaptioner(found["path"], log)
        _loaded.update(key=key, cap=cap)
        return cap


def unload() -> None:
    cap = _loaded.pop("cap", None)
    _loaded.clear()
    if cap is not None:
        cap.close()
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def prompt_for(settings) -> str:
    return build_prompt(settings.joy_caption_type, settings.joy_caption_length, settings.joy_extra, settings.joy_name)


def caption_files(settings, files: list[str], ctx, on_caption=None) -> dict[str, str]:
    """{path: caption}; on_caption(path, text) is called as each one finishes."""
    cap = get(settings, ctx.log)
    prompt = prompt_for(settings)
    out = {}
    for i, f in enumerate(files):
        if ctx.cancelled:
            break
        ctx.progress(i, len(files), f"Captioning {i + 1} / {len(files)}")
        try:
            text = cap.caption(f, prompt, settings.joy_temperature, settings.joy_max_tokens, lambda: ctx.cancelled)
        except Exception as exc:
            ctx.log(f"  {f}: {exc}")
            continue
        if ctx.cancelled:              # half-finished caption: never write it
            break
        if not text.strip():
            ctx.log(f"  {f}: empty caption, skipped")
            continue
        out[f] = text
        if on_caption:
            on_caption(f, out[f])
    ctx.progress(len(files), len(files), "")
    return out
