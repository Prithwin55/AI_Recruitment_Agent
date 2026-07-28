"""Pocket TTS (kyutai flow-matching) engine — a faithful Python port of the browser worker
frontend/public/pockettts/inference-worker.js. Runs the same int8 ONNX bundle
(KevinAHM/pocket-tts-web, onnx/english_2026-04) server-side and yields 24 kHz mono int16 LE PCM.

Only the PREDEFINED-voice path is ported (alba/azelma/cosette/... from voices.bin) — that's all the
interview needs; the custom-voice encoder path from the worker is intentionally omitted, so the
mimi_encoder model isn't loaded. Function names in comments map to their inference-worker.js origin.

Heavy + autoregressive: this is CPU-bound and can run near/below real-time, which is why routes.py
synthesizes a whole sentence before streaming its frames (no client-side underrun) and the semaphore
holds a slot for the full generation. Scale by replicas."""

import logging
import os
import re
import struct
import threading
from typing import Iterator

import numpy as np
import onnxruntime as ort
import sentencepiece as spm
from huggingface_hub import hf_hub_download

logger = logging.getLogger(__name__)

REPO = os.getenv("POCKET_REPO", "KevinAHM/pocket-tts-web")
BUNDLE = os.getenv("POCKET_BUNDLE", "english_2026-04")
BASE = f"onnx/{BUNDLE}"

# --- constants, all from inference-worker.js ---
# Flow-matching integration steps. Measured RTF on 8 CPU cores: 4→1.03x, 2→0.93x, 1→0.88x (below
# real-time regardless — flow_lm_main's per-frame AR step is the floor). 4 = smoothest prosody
# (worker default); drop to 2 for a bit less latency at slightly flatter timbre.
LSD_STEPS = int(os.getenv("POCKET_LSD_STEPS", "4"))
MAX_FRAMES = 500
TEMPERATURE = 0.7
EOS_THRESHOLD = -4.0
FIRST_CHUNK_FRAMES = 3        # decode granularity: small first block for low first-audio latency
NORMAL_CHUNK_FRAMES = 12
CHUNK_GAP_SEC = 0.25          # silence inserted between text chunks
RESET_FLOW_STATE_EACH_CHUNK = True
RESET_MIMI_STATE_EACH_CHUNK = True

MODEL_STEMS = {
    "text_conditioner": "text_conditioner_int8.onnx",
    "flow_lm_main": "flow_lm_main_int8.onnx",
    "flow_lm_flow": "flow_lm_flow_int8.onnx",
    "mimi_decoder": "mimi_decoder_int8.onnx",
}


def _np_bool(x):
    return np.asarray(x).astype(np.bool_)


# ---- makeFilledArray + state manifests (worker: makeFilledArray/initStateFromManifest) ----

def _filled(shape, dtype, fill):
    if dtype == "int64":
        return np.zeros(shape, np.int64)          # NB: worker ignores `fill` for int64/bool
    if dtype == "bool":
        return np.zeros(shape, np.bool_)
    a = np.zeros(shape, np.float32)
    if fill == "nan":
        a[...] = np.nan
    elif fill == "ones":
        a[...] = 1.0
    return a


def _init_state(manifest):
    return {e["input_name"]: _filled(e["shape"], e["dtype"], e.get("fill")) for e in manifest}


def _update_state(state, result, manifest):
    for e in manifest:
        state[e["input_name"]] = result[e["output_name"]]


# ---- voices.bin parser (worker: parseVoiceStatesBin, "PTVB1") ----

def _parse_voices(buf: bytes):
    if buf[0:5] != b"PTVB1":
        raise ValueError("Invalid voices.bin header")
    off = 5
    (count,) = struct.unpack_from("<I", buf, off); off += 4
    voices = {}
    for _ in range(count):
        (name_len,) = struct.unpack_from("<H", buf, off); off += 2
        name = buf[off:off + name_len].decode(); off += name_len
        (tcount,) = struct.unpack_from("<H", buf, off); off += 2
        tensors = {}
        for _ in range(tcount):
            (key_len,) = struct.unpack_from("<H", buf, off); off += 2
            key = buf[off:off + key_len].decode(); off += key_len
            dcode = buf[off]; off += 1
            rank = buf[off]; off += 1
            shape = list(struct.unpack_from(f"<{rank}I", buf, off)); off += 4 * rank
            (blen,) = struct.unpack_from("<I", buf, off); off += 4
            raw = bytes(buf[off:off + blen]); off += blen
            if dcode == 0:
                data, dt = np.frombuffer(raw, dtype="<f4").copy(), "float32"
            elif dcode == 1:
                data, dt = np.frombuffer(raw, dtype="<i8").copy(), "int64"
            elif dcode == 2:
                data, dt = np.frombuffer(raw, dtype=np.uint8).copy(), "bool"
            else:
                raise ValueError(f"Unsupported voices.bin dtype code: {dcode}")
            tensors[key] = {"data": data, "shape": shape, "dtype": dt}
        voices[name] = tensors
    return voices


# ---- voice record -> flow-LM state (worker: groupVoiceRecordByModule/deriveStep/adaptTypedArray/
#      stateFromVoiceRecord) ----

def _group_by_module(record):
    grouped = {}
    for k, v in record.items():
        i = k.find("/")
        if i == -1:
            continue
        grouped.setdefault(k[:i], {})[k[i + 1:]] = v
    return grouped


def _derive_step(module_state):
    if "step" in module_state:
        val = int(module_state["step"]["data"][0])
    elif "offset" in module_state and "end_offset" not in module_state:
        val = int(module_state["offset"]["data"][0])
    elif "current_end" in module_state:
        val = int(module_state["current_end"]["shape"][0])
    else:
        val = 0
    return {"data": np.array([val], np.int64), "shape": [1], "dtype": "int64"}


def _coerce(arr, dtype):
    if dtype == "int64":
        return arr.astype(np.int64)
    if dtype == "bool":
        return arr.astype(np.bool_)
    return arr.astype(np.float32)


def _adapt(source, entry):
    """Port of adaptTypedArray: fit a voice tensor to a manifest slot. Fast paths (exact shape / equal
    element count) cover the matched bundle; the strided partial-copy is a faithful safety fallback."""
    tshape = entry["shape"]
    tsize = int(np.prod(tshape)) if tshape else 1
    tdt = entry["dtype"]
    sdata = source["data"]
    sshape = list(source["shape"])

    if len(sshape) == len(tshape) and all(sshape[i] == tshape[i] for i in range(len(sshape))):
        return _coerce(sdata, tdt).reshape(tshape)
    if sdata.size == tsize:
        return _coerce(sdata, tdt).reshape(tshape)
    if len(sshape) != len(tshape):
        return _filled(tshape, tdt, entry.get("fill"))

    target = _filled(tshape, tdt, entry.get("fill")).reshape(-1)
    src = sdata.reshape(-1)
    src_strides = [0] * len(sshape)
    stride = 1
    for i in range(len(sshape) - 1, -1, -1):
        src_strides[i] = stride
        stride *= sshape[i]
    max_idx = [min(sshape[i], tshape[i]) for i in range(len(sshape))]

    def tgt_index(coords):
        idx, ts = 0, 1
        for i in range(len(tshape) - 1, -1, -1):
            idx += coords[i] * ts
            ts *= tshape[i]
        return idx

    indices = [0] * len(sshape)
    done = False
    while not done:
        s_idx = sum(indices[i] * src_strides[i] for i in range(len(indices)))
        target[tgt_index(indices)] = src[s_idx]
        for dim in range(len(indices) - 1, -1, -1):
            indices[dim] += 1
            if indices[dim] < max_idx[dim]:
                break
            indices[dim] = 0
            if dim == 0:
                done = True
    return _coerce(target, tdt).reshape(tshape)


class PocketEngine:
    name = "pocket"

    def __init__(self) -> None:
        self._sess = {}
        self._out_names = {}
        self._meta = None
        self._sp = None
        self._voice_state = {}      # voice -> flow-LM base state (dict input_name -> np array)
        self._default_voice = None
        self._st = []               # precomputed (s, t) flow buffers
        self._ready = False

    # -------------------------------------------------------------------------- load ----

    def load(self) -> None:
        import json

        threads = int(os.getenv("TTS_ORT_THREADS", "0"))  # 0 => onnxruntime picks (all cores)
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads > 0:
            so.intra_op_num_threads = threads

        with open(hf_hub_download(REPO, f"{BASE}/bundle.json", repo_type="space")) as f:
            self._meta = json.load(f)

        for key, stem in MODEL_STEMS.items():
            path = hf_hub_download(REPO, f"{BASE}/{stem}", repo_type="space")
            sess = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
            self._sess[key] = sess
            self._out_names[key] = [o.name for o in sess.get_outputs()]

        tok_path = hf_hub_download(REPO, f"{BASE}/{self._meta['tokenizer_file']}", repo_type="space")
        self._sp = spm.SentencePieceProcessor(model_file=tok_path)

        voices_path = hf_hub_download(REPO, f"{BASE}/voices.bin", repo_type="space")
        with open(voices_path, "rb") as f:
            records = _parse_voices(f.read())

        flow_manifest = self._meta["flow_lm_state_manifest"]
        for name, record in records.items():
            self._voice_state[name] = self._state_from_voice(record, flow_manifest)

        predefined = self._meta.get("predefined_voices") or list(records.keys())
        self._default_voice = "cosette" if "cosette" in self._voice_state else (
            predefined[0] if predefined else None
        )

        dt = 1.0 / LSD_STEPS
        for step in range(LSD_STEPS):
            s = step / LSD_STEPS
            self._st.append((
                np.array([[s]], np.float32),
                np.array([[s + dt]], np.float32),
            ))

        self._ready = True
        logger.info("Pocket TTS loaded: %d voices, default=%s", len(self._voice_state), self._default_voice)

    @property
    def ready(self) -> bool:
        return self._ready

    def _state_from_voice(self, record, flow_manifest):
        grouped = _group_by_module(record)
        state = _init_state(flow_manifest)
        for e in flow_manifest:
            ms = grouped.get(e["module"], {})
            src = ms.get(e["key"])
            if src is None and e["key"] == "step":
                src = _derive_step(ms)
            if src is None:
                continue
            state[e["input_name"]] = _adapt(src, e)
        return state

    # ----------------------------------------------------------------------- helpers ----

    def _run(self, key, feed):
        outs = self._sess[key].run(self._out_names[key], feed)
        return dict(zip(self._out_names[key], outs))

    def _resolve_voice(self, voice):
        if voice and voice in self._voice_state:
            return voice
        return self._default_voice

    # ------------------------------------------------------------- text prep (worker) ----

    def _prepare_prompt(self, text):
        meta = self._meta
        p = text.strip()
        if not p:
            return "", 1
        p = re.sub(r"[\r\n]", " ", p)
        p = re.sub(r"\s+", " ", p)
        if meta.get("remove_semicolons"):
            p = p.replace(";", ",")
        wc = len([w for w in p.split(" ") if w])
        fae = 3 if wc <= 4 else 1
        if meta.get("model_recommended_frames_after_eos") is not None:
            fae = int(meta["model_recommended_frames_after_eos"])
        if p and not re.match(r"[A-ZÀ-Þ]", p[0]):
            p = p[0].upper() + p[1:]
        if p and re.search(r"[0-9A-Za-zÀ-ÿ]", p[-1]):
            p = p + "."
        if meta.get("pad_with_spaces_for_short_inputs") and wc < 5:
            p = "        " + p
        return p, fae

    def _split_sentences(self, text):
        return [s.strip() for s in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text) if s.strip()]

    def _encode(self, text):
        return list(self._sp.encode(text, out_type=int))

    def _decode(self, ids):
        return self._sp.decode(ids)

    def _split_into_chunks(self, text):
        prepared, fae = self._prepare_prompt(text)
        if not prepared:
            return [], fae
        max_tok = int(self._meta.get("max_token_per_chunk") or 50)
        sentences = self._split_sentences(prepared)
        if not sentences:
            return [prepared], fae

        chunks, current = [], ""
        for sent in sentences:
            ids = self._encode(sent)
            if len(ids) > max_tok:
                if current:
                    chunks.append(current.strip()); current = ""
                for i in range(0, len(ids), max_tok):
                    piece = self._decode(ids[i:i + max_tok]).strip()
                    if piece:
                        chunks.append(piece)
                continue
            if not current:
                current = sent
                continue
            combined = f"{current} {sent}"
            if len(self._encode(combined)) > max_tok:
                chunks.append(current.strip()); current = sent
            else:
                current = combined
        if current:
            chunks.append(current.strip())
        return chunks, fae

    # ------------------------------------------------------- generation (worker port) ----

    def stream(self, text: str, voice: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]:
        """Yield 24 kHz mono int16 LE PCM for `text`, chunk by chunk as decoded. `cancel` (a
        threading.Event) stops generation promptly on barge-in."""
        if not self._ready:
            raise RuntimeError("Pocket engine not loaded")
        vname = self._resolve_voice(voice)
        if vname is None:
            raise RuntimeError("No Pocket voice available")

        chunks, fae = self._split_into_chunks(text)
        if not chunks:
            return

        meta = self._meta
        flow_manifest = meta["flow_lm_state_manifest"]
        mimi_manifest = meta["mimi_state_manifest"]
        latent_dim = int(meta["latent_dim"])
        cond_dim = int(meta["conditioning_dim"])
        sample_rate = int(meta["sample_rate"])
        base_flow = self._voice_state[vname]

        empty_seq = np.zeros((1, 0, latent_dim), np.float32)
        empty_text = np.zeros((1, 0, cond_dim), np.float32)
        dt = 1.0 / LSD_STEPS
        std = float(np.sqrt(TEMPERATURE))

        flow_state = dict(base_flow)
        mimi_state = _init_state(mimi_manifest)
        first_audio = True

        for chunk_idx, chunk_text in enumerate(chunks):
            if cancel is not None and cancel.is_set():
                return
            if RESET_FLOW_STATE_EACH_CHUNK and chunk_idx > 0:
                flow_state = dict(base_flow)
            if RESET_MIMI_STATE_EACH_CHUNK and chunk_idx > 0:
                mimi_state = _init_state(mimi_manifest)

            token_ids = self._encode(chunk_text)
            text_input = np.array([token_ids], np.int64)
            text_emb = self._run("text_conditioner", {"token_ids": text_input})["embeddings"]
            if text_emb.ndim == 2:
                text_emb = text_emb.reshape(1, text_emb.shape[0], text_emb.shape[1])

            cond = self._run("flow_lm_main", {"sequence": empty_seq, "text_embeddings": text_emb, **flow_state})
            _update_state(flow_state, cond, flow_manifest)

            chunk_latents = []
            decoded = 0
            current_latent = np.full((1, 1, latent_dim), np.nan, np.float32)
            eos_step = None

            for step in range(MAX_FRAMES):
                if cancel is not None and cancel.is_set():
                    return
                ar = self._run("flow_lm_main",
                               {"sequence": current_latent, "text_embeddings": empty_text, **flow_state})
                conditioning = ar["conditioning"]
                eos_logit = float(ar["eos_logit"].reshape(-1)[0])
                if eos_logit > EOS_THRESHOLD and eos_step is None:
                    eos_step = step
                should_stop = eos_step is not None and step >= eos_step + fae

                latent = (np.random.randn(latent_dim).astype(np.float32)) * std
                x = latent.reshape(1, latent_dim)
                for li in range(LSD_STEPS):
                    s_t, t_t = self._st[li]
                    flow_dir = self._run("flow_lm_flow", {"c": conditioning, "s": s_t, "t": t_t, "x": x})["flow_dir"]
                    x = x + flow_dir.reshape(1, latent_dim) * dt
                latent = x.reshape(latent_dim)

                chunk_latents.append(latent.copy())
                current_latent = latent.reshape(1, 1, latent_dim)
                _update_state(flow_state, ar, flow_manifest)

                pending = len(chunk_latents) - decoded
                if should_stop:
                    decode_size = pending
                elif first_audio and pending >= FIRST_CHUNK_FRAMES:
                    decode_size = FIRST_CHUNK_FRAMES
                elif pending >= NORMAL_CHUNK_FRAMES:
                    decode_size = NORMAL_CHUNK_FRAMES
                else:
                    decode_size = 0

                if decode_size > 0:
                    block = np.stack(chunk_latents[decoded:decoded + decode_size]).reshape(1, decode_size, latent_dim)
                    dec = self._run("mimi_decoder", {"latent": block.astype(np.float32), **mimi_state})
                    _update_state(mimi_state, dec, mimi_manifest)
                    decoded += decode_size
                    audio = np.asarray(dec[self._out_names["mimi_decoder"][0]], np.float32).reshape(-1)
                    first_audio = False
                    yield _pcm16(audio)

                if should_stop:
                    break

            if chunk_idx < len(chunks) - 1:
                gap = np.zeros(max(1, int(CHUNK_GAP_SEC * sample_rate)), np.float32)
                yield _pcm16(gap)


def _pcm16(audio: np.ndarray) -> bytes:
    a = np.clip(audio, -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()
