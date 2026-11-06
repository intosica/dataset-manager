"""AI model files: where they live, how to find them, and downloading them from Hugging Face.

Every model has a location setting (a folder or a file). Blank = <app data>/models/<key>. Downloads go to the
chosen folder, so pointing a setting at an existing folder (e.g. another tool's models/) reuses those files.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths

HF = "https://huggingface.co"
JOY_GGUF_REPO = "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf"
JOY_HF_REPO = "fancyfeast/llama-joycaption-beta-one-hf-llava"

MODELS = {
    "hydra": {"name": "JTP Hydra 3.5", "setting": "hydra_path", "size": "1.1 GB",
              "repo": "RedRocket/Hydra", "files": ["models/hydra-3.5.safetensors"]},
    "wd": {"name": "WD EVA02 Large Tagger v3", "setting": "wd_path", "size": "1.3 GB",
           "repo": "SmilingWolf/wd-eva02-large-tagger-v3",
           "files": ["model.safetensors", "selected_tags.csv", "config.json"]},
    "joycaption": {"name": "JoyCaption Beta One · GGUF Q4 (llama.cpp)", "setting": "joycaption_path", "size": "5.8 GB",
                   "repo": JOY_GGUF_REPO,
                   "files": ["Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf",
                             "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf"]},
    "joycaption_hf": {"name": "JoyCaption Beta One · Transformers (GPU)", "setting": "joycaption_path",
                      "size": "17 GB", "repo": JOY_HF_REPO, "subdir": "llama-joycaption-beta-one-hf-llava",
                      "files": ["config.json", "generation_config.json", "chat_template.json",
                                "preprocessor_config.json", "processor_config.json", "special_tokens_map.json",
                                "tokenizer.json", "tokenizer_config.json", "model.safetensors.index.json"]
                               + [f"model-0000{i}-of-00004.safetensors" for i in range(1, 5)]},
}


# ---- Florence 2 (ComfyUI-Florence2 layout) ----
# ComfyUI's Florence2 node downloads these repos into models/LLM/<repo basename>. The app reads that folder
# instead of keeping a second copy. Order here is the order shown in Preferences.
FLORENCE_REPOS = {
    "Florence-2-base": ("microsoft/Florence-2-base", "Florence-2 Base", "0.5 GB"),
    "Florence-2-base-ft": ("microsoft/Florence-2-base-ft", "Florence-2 Base FT", "0.5 GB"),
    "Florence-2-large": ("microsoft/Florence-2-large", "Florence-2 Large", "1.5 GB"),
    "Florence-2-large-ft": ("microsoft/Florence-2-large-ft", "Florence-2 Large FT", "1.5 GB"),
    "CogFlorence-2.1-Large": ("thwri/CogFlorence-2.1-Large", "CogFlorence 2.1 Large", "1.6 GB"),
    "CogFlorence-2.2-Large": ("thwri/CogFlorence-2.2-Large", "CogFlorence 2.2 Large", "1.6 GB"),
    "Florence-2-Flux-Large": ("gokaygokay/Florence-2-Flux-Large", "Florence-2 Flux Large", "3.1 GB"),
    "Florence-2-SD3-Captioner": ("gokaygokay/Florence-2-SD3-Captioner", "Florence-2 SD3 Captioner", "1.5 GB"),
    "Florence-2-base-PromptGen-v1.5": ("MiaoshouAI/Florence-2-base-PromptGen-v1.5", "Florence-2 Base PromptGen 1.5", "0.5 GB"),
    "Florence-2-large-PromptGen-v1.5": ("MiaoshouAI/Florence-2-large-PromptGen-v1.5", "Florence-2 Large PromptGen 1.5", "1.5 GB"),
    "Florence-2-base-PromptGen-v2.0": ("MiaoshouAI/Florence-2-base-PromptGen-v2.0", "Florence-2 Base PromptGen 2.0", "0.5 GB"),
    "Florence-2-large-PromptGen-v2.0": ("MiaoshouAI/Florence-2-large-PromptGen-v2.0", "Florence-2 Large PromptGen 2.0", "1.5 GB"),
    "Florence-2-DocVQA": ("HuggingFaceM4/Florence-2-DocVQA", "Florence-2 DocVQA", "1.5 GB"),
    "Florence-2-base-Castollux-v0.5": ("PJMixers-Images/Florence-2-base-Castollux-v0.5", "Florence-2 Base Castollux 0.5", "0.5 GB"),
}
FLORENCE_FILES = ["*.json", "*.py", "*.txt", "*.model", "*.tiktoken", "*.spm", "*.merges", "*.vocab", "*.jinja"]


@dataclass(frozen=True)
class FlorenceModel:
    name: str
    path: str
    repo: str = ""
    size: str = ""


def _florence_info(d: Path) -> tuple[str, str, str]:
    """(display name, repo, size) for a folder or a HF cache snapshot."""
    name = d.name
    repo = ""
    if d.parent.name == "snapshots" and d.parent.parent.name.startswith("models--"):
        # ~/.cache/huggingface/hub/models--microsoft--Florence-2-base/snapshots/<sha>
        repo = d.parent.parent.name.removeprefix("models--").replace("--", "/")
        name = repo.rsplit("/", 1)[-1]
    info = FLORENCE_REPOS.get(name)
    if info:
        return info[1], info[0], info[2]
    return name.replace("-", " "), repo, ""


def _is_florence(d: Path) -> bool:
    try:
        cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not (d / "preprocessor_config.json").is_file() or not (d / "modeling_florence2.py").is_file():
        return False
    weights = (list(d.glob("*.safetensors")) or list(d.glob("pytorch_model*.bin")))
    if not weights:
        return False
    return "florence" in str(cfg.get("architectures", "")).lower() or "florence" in str(cfg.get("model_type", "")).lower()         or "florence" in str(cfg.get("_name_or_path", "")).lower()


def _comfyui_llm_dirs(settings) -> list[Path]:
    """ComfyUI's models/LLM folders, from an explicit folder, the model folder list, or the usual install paths."""
    explicit = paths.resolve(getattr(settings, "florence_path", ""))
    if explicit:
        p = Path(explicit)
        if p.name.lower() == "models":
            return [p / "LLM"]
        if (p / "models" / "LLM").is_dir():
            return [p / "models" / "LLM"]
        return [p]
    out: list[Path] = []
    for d in getattr(settings, "model_dirs", []) or []:
        p = Path(paths.resolve(d))
        for parent in (p, *p.parents):
            if (parent / "models" / "LLM").is_dir() and parent / "models" / "LLM" not in out:
                out.append(parent / "models" / "LLM")
                break
    home = Path.home()
    for base in (home / "Documents", home, Path("/opt"), Path("/usr/local")):
        for p in (base / "ComfyUI", base / "comfyui"):
            llm = p / "models" / "LLM"
            if llm.is_dir() and llm not in out:
                out.append(llm)
        try:
            for p in base.iterdir():
                llm = p / "models" / "LLM"
                if p.is_dir() and p.name.lower().startswith("comfyui") and llm.is_dir() and llm not in out:
                    out.append(llm)
        except OSError:
            pass
    local = paths.data_dir() / "models" / "florence"
    if local.is_dir():
        out.append(local)
    return out


def find_florence_models(settings) -> list[FlorenceModel]:
    """Florence-2 folders in ComfyUI's models/LLM (and the HF cache); first copy of each name wins."""
    roots = _comfyui_llm_dirs(settings)
    for hub in _hf_cache_roots():
        try:
            roots += [p for p in hub.iterdir() if p.is_dir() and "florence" in p.name.lower()]
        except OSError:
            pass
    found: dict[str, FlorenceModel] = {}
    for root in roots:
        candidates = [root]
        if root.name.startswith("models--"):
            candidates = list((root / "snapshots").glob("*")) if (root / "snapshots").is_dir() else []
        else:
            try:
                candidates += [p for p in root.iterdir() if p.is_dir()]
            except OSError:
                pass
        for d in candidates:
            if d.name.startswith(".") or not _is_florence(d):
                continue
            name, repo, size = _florence_info(d)
            found.setdefault(name.lower(), FlorenceModel(name, str(d), repo, size))
    order = {v[1].lower(): i for i, v in enumerate(FLORENCE_REPOS.values())}
    return sorted(found.values(), key=lambda m: (order.get(m.name.lower(), 999), m.name.lower()))


def florence_download_root(settings) -> Path:
    p = paths.resolve(getattr(settings, "florence_path", ""))
    if p:
        root = Path(p)
        if _is_florence(root):
            return root.parent
        dirs = _comfyui_llm_dirs(settings)
        return dirs[0] if dirs else root
    dirs = _comfyui_llm_dirs(settings)
    if dirs:
        return dirs[0]
    return paths.data_dir() / "models" / "florence"


def download_florence(repo: str, settings, ctx) -> bool:
    """Fetch one Florence-2 repo into models/LLM/<repo name>, the same layout ComfyUI uses."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("Florence downloads need huggingface-hub; install 'transformers' in Preferences → "
                           "System first.") from exc
    dest = florence_download_root(settings) / repo.rsplit("/", 1)[-1]
    dest.mkdir(parents=True, exist_ok=True)
    ctx.log(f"Downloading {repo} to {dest}… (Hugging Face shows progress in its own terminal)")
    # Prefer safetensors; fall back to .bin for the few repos that only ship that.
    for extra in (["*.safetensors"], ["*.bin"]):
        snapshot_download(repo_id=repo, local_dir=str(dest), allow_patterns=FLORENCE_FILES + extra)
        if list(dest.glob("*.safetensors")) or list(dest.glob("pytorch_model*.bin")):
            break
    ctx.log(f"{repo} is ready.")
    return True


# ---- locations ----
def location(key: str, settings) -> Path:
    """The folder (or file) the user chose for this model, else the app's own models folder."""
    p = paths.resolve(getattr(settings, MODELS[key]["setting"], ""))
    return Path(p) if p else paths.data_dir() / "models" / ("joycaption" if key.startswith("joycaption") else key)


def download_dir(key: str, settings) -> Path:
    loc = location(key, settings)
    if loc.is_file():
        loc = loc.parent
    return loc / MODELS[key]["subdir"] if MODELS[key].get("subdir") else loc


def _walk(root: Path, depth: int = 4):
    """Folders under root, shallow first, at most `depth` levels (HF cache snapshots sit 3 deep)."""
    level = [root]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            yield d
            try:
                nxt += sorted(e.path for e in os.scandir(d) if e.is_dir() and not e.name.startswith("."))
            except OSError:
                pass
        level = [Path(p) for p in nxt]


def _hf_cache_roots() -> list[Path]:
    roots = [os.environ.get("HF_HUB_CACHE"), os.environ.get("HF_HOME") and os.path.join(os.environ["HF_HOME"], "hub"),
             os.path.join(os.environ.get("XDG_CACHE_HOME") or "~/.cache", "huggingface", "hub"),
             "~/.cache/huggingface/hub"]  # in the flatpak, ~ is the real home, so the host's HF cache is found too
    out = []
    for r in roots:
        if r:
            p = Path(os.path.expanduser(r))
            if p not in out:
                out.append(p)
    return out


def find_hydra(settings) -> str | None:
    loc = location("hydra", settings)
    if loc.is_file():
        return str(loc)
    for d in _walk(loc, 2):
        exact = d / "hydra-3.5.safetensors"
        if exact.is_file():
            return str(exact)
        hits = [f for f in glob.glob(str(d / "*.safetensors")) if "hydra" in f.lower() and "3.5" in f]
        if hits:
            return sorted(hits)[0]
    return None


def find_wd(settings) -> str | None:
    """Folder holding model.safetensors + selected_tags.csv (a plain folder or a HF cache snapshot)."""
    loc = location("wd", settings)
    if loc.is_file():
        loc = loc.parent
    candidates = [loc] + [r / "models--SmilingWolf--wd-eva02-large-tagger-v3" for r in _hf_cache_roots()]
    for root in candidates:
        for d in _walk(root, 4):
            if (d / "selected_tags.csv").is_file() and (d / "model.safetensors").is_file():
                return str(d)
    return None


def find_joycaption(settings) -> dict | None:
    """{'kind': 'gguf', 'model': ..., 'mmproj': ...} or {'kind': 'hf', 'path': folder}; GGUF wins if both exist."""
    loc = location("joycaption", settings)
    picked = str(loc) if loc.is_file() and loc.suffix == ".gguf" else ""
    if loc.is_file():
        loc = loc.parent
    hf = None
    for d in _walk(loc, 3):
        ggufs = glob.glob(str(d / "*.gguf"))
        mm = [f for f in ggufs if "mmproj" in os.path.basename(f).lower()]
        main = [f for f in ggufs if "mmproj" not in os.path.basename(f).lower()]
        if picked and d == loc:
            main = [picked]
        if main and mm:
            # prefer Beta One over Alpha Two, and Q4 over bigger quants when several are present
            main.sort(key=lambda f: ("beta" not in f.lower(), "q4" not in f.lower(), f))
            return {"kind": "gguf", "model": main[0], "mmproj": mm[0]}
        if hf is None and (d / "config.json").is_file() and glob.glob(str(d / "*.safetensors")) \
                and (d / "preprocessor_config.json").is_file():
            hf = {"kind": "hf", "path": str(d)}
    return hf


def status(key: str, settings) -> str | None:
    """Path of the installed model, or None."""
    if key.startswith("florence:"):
        path = key.split(":", 1)[1]
        return path if os.path.isdir(path) else None
    if key == "hydra":
        return find_hydra(settings)
    if key == "wd":
        return find_wd(settings)
    j = find_joycaption(settings)
    if j and (key == "joycaption") == (j["kind"] == "gguf"):
        return j.get("model") or j.get("path")
    return None


# ---- downloading ----
def _fetch(url: str, dest: Path, ctx, label: str) -> bool:
    """Stream url to dest, resuming a previous .part; honours ctx cancel. False if cancelled."""
    import requests

    if dest.is_file():
        return True
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Accept-Encoding": "identity"}       # byte counts must match Content-Length
    if have:
        headers["Range"] = f"bytes={have}-"
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True) as r:
        if r.status_code == 416:            # Range past the end: complete only if the size matches exactly
            size = (r.headers.get("content-range") or "").rpartition("/")[2]
            if size.isdigit() and int(size) == have:
                part.replace(dest)
                return True
            part.unlink(missing_ok=True)
            ctx.log(f"  {label}: partial download didn't match the remote file, starting over")
            return _fetch(url, dest, ctx, label)
        r.raise_for_status()
        if r.status_code != 206:
            have = 0
        length = int(r.headers.get("content-length") or 0)
        total = have + length
        mb_total = max(1, total >> 20)
        with open(part, "ab" if have else "wb") as f:
            done, last = have, -1
            for chunk in r.iter_content(1 << 20):
                if ctx.cancelled:
                    ctx.log(f"Paused {label}; downloading again resumes where it stopped.")
                    return False
                f.write(chunk)
                done += len(chunk)
                if done >> 20 != last:
                    last = done >> 20
                    ctx.progress(last, mb_total, f"{label}: {last} / {mb_total} MB")
    if length and done != total:
        raise OSError(f"{label}: connection closed early ({done >> 20} of {mb_total} MB); download again to resume")
    part.replace(dest)
    return True


def download(key: str, settings, ctx) -> bool:
    m = MODELS[key]
    dest = download_dir(key, settings)
    dest.mkdir(parents=True, exist_ok=True)
    ctx.log(f"Downloading {m['name']} (~{m['size']}) to {dest}")
    for rel in m["files"]:
        out = dest / os.path.basename(rel)
        if out.is_file():
            ctx.log(f"  {out.name}: already there")
            continue
        ctx.log(f"  {out.name}…")
        if not _fetch(f"{HF}/{m['repo']}/resolve/main/{rel}", out, ctx, out.name):
            return False
    ctx.log(f"{m['name']} is ready.")
    return True


# ---- smaller models that the libraries fetch themselves (into the Hugging Face / torch cache) ----
PREFETCH = {  # key -> (label, modules needed)
    "imgbeddings": ("Similarity · imgbeddings (CLIP ViT-B/32 ONNX, 88 MB)", ["onnxruntime", "transformers"]),
    "tinyclip": ("Similarity · TinyCLIP ViT-8M (40 MB)", ["torch", "transformers"]),
    "openclip": ("Similarity · OpenCLIP ViT-B/32 (600 MB)", ["torch", "open_clip"]),
    "mobilenet": ("Similarity · MobileNetV3 (10 MB)", ["torch", "torchvision"]),
    "imageprism:semantic": ("Similarity · imageprism models", ["imageprism"]),
    "cafe": ("Low-quality detector · cafe_aesthetic (350 MB)", ["torch", "transformers"]),
    "aesthetic": ("Aesthetic score v2.5 · SigLIP (1.6 GB)", ["aesthetic_predictor_v2_5"]),
}


def prefetch(key: str, ctx) -> bool:
    """Load the model once so it is downloaded and cached; the next real use starts offline and instantly."""
    ctx.log(f"Fetching {PREFETCH[key][0]}… (progress shows in the log once the download starts)")
    if key == "cafe":
        from .aesthetics import QualityDetector
        ok = QualityDetector()._load()
    elif key == "aesthetic":
        from .aesthetic_score import AestheticFilter, AestheticSpec
        ok = AestheticFilter(AestheticSpec(), ctx)._load()
    else:
        from .similarity import _embedder
        _embedder(key)
        ok = True
    ctx.log("Ready." if ok else "Could not load it; see the messages above.")
    return ok


# ---- caches used by transformers / open_clip / torchvision ----
def apply_cache_env(settings) -> None:
    """Point the Hugging Face and torch caches at the chosen folder. Must run before those libraries import;
    a change made later takes effect on the next start."""
    d = paths.resolve(getattr(settings, "hf_cache_dir", ""))
    if d:
        os.environ["HF_HOME"] = d
        os.environ["HF_HUB_CACHE"] = os.path.join(d, "hub")
        os.environ["TORCH_HOME"] = os.path.join(d, "torch")
