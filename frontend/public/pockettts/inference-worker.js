// Kitten TTS ONNX/WASM Web Worker.
//
// Drop-in replacement for the previous Pocket TTS worker: it speaks the SAME message
// protocol the wrapper (src/lib/interview/pocketTts.ts) expects, so nothing downstream
// changes — playback, turn-taking and the sentence-done ack all stay identical:
//   <- { type:'load' }                          -> 'voices_loaded', 'loaded'
//   <- { type:'generate', data:{ text, voice } } -> 'audio_chunk'*, 'stream_ended'
//   <- { type:'stop' }
// Audio is 24kHz mono Float32 PCM, delivered as ONE chunk per sentence (gapless within
// a sentence: the whole sentence is synthesized in a single forward pass before it's sent).
//
// Pipeline (KittenTTS nano): text -> clean -> sentence chunks -> eSpeak-NG IPA phonemes
// (vendored ./phonemizer.min.js, self-contained WASM) -> token ids -> ONNX
// (input_ids / style / speed) -> waveform. The model + voice-style bank are the int8
// `kitten-tts-nano-0.8-int8` bundle (~27MB), fetched from Hugging Face at runtime and
// browser-cached after first load — exactly how the old Pocket bundle was served.
//
// KittenTTS is a small non-autoregressive model (one pass per sentence, ~0.5 RTF), so it
// does not have the below-real-time "breaks up within words" problem the flow-LM had.

console.log("Kitten TTS Worker starting...");
self.postMessage({ type: "status", status: "Worker Thread Started", state: "idle" });

// ---- config ----------------------------------------------------------------
const ORT_VERSION = "1.22.0";
const ORT_CDN = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`;
const MODEL_REPO = "KittenML/kitten-tts-nano-0.8-int8";
const HF_BASE = "https://huggingface.co";
const PHONEMIZER_URL = "./phonemizer.min.js";
const PHONEME_LANGUAGE = "en-us";
const SAMPLE_RATE = 24000;
// --- Low-latency streaming (BALANCED / clause-only) --------------------------
// KittenTTS renders a whole chunk in one forward pass, so time-to-first-audio == the
// synth time of that chunk. To start speaking sooner we split each sentence into pieces,
// synthesize them in order, and stream each out immediately; the wrapper schedules them
// back-to-back on the audio timeline so the rest is synthesized while the first piece is
// already playing. Synthesis is ~2x faster than real-time, so playback stays ahead.
//
// TRADE-OFF: each piece is rendered as its OWN utterance, so it has no shared intonation with
// its neighbours — the more (and the smaller) the pieces, the more clipped/robotic it sounds;
// but the bigger the first piece, the longer before any sound comes out ("laggy"). The balance:
// keep the FIRST piece small (FIRST_MAX_WORDS) so speech starts quickly, then use FULL clauses
// (up to the big STREAM_MAX_WORDS cap) for everything after — so only the opener may land on a
// mid-phrase seam, and only when the first clause is long. Levers: lower FIRST_MAX_WORDS for an
// even snappier start; raise STREAM_MIN_WORDS above any real sentence length to synthesize each
// sentence in one pass (most natural, slowest start); lower STREAM_MAX_WORDS to split later
// clauses too (snappier mid-sentence, slightly more robotic).
//
// Each isolated piece carries leading + trailing silence; naively concatenating them stacks
// those into ~800ms gaps (the old "voice breaks" failure). So trimToSpeechCore() trims each
// piece to its actual speech and buildChunk() re-inserts a fixed PIECE_PAUSE clause pause.
// The STREAM_MIN_WORDS floor keeps pieces long enough that edge-trimming never clips a word.
const STREAM_MIN_WORDS = 4;    // never emit a piece shorter than this (tail would clip)
const FIRST_MAX_WORDS = 6;     // cap the first piece so the interviewer starts talking fast
const STREAM_MAX_WORDS = 40;   // later pieces are clause-only; only splits absurdly long runs
// Playback speed passed to the model (higher = faster). The model config suggests ~0.8 per
// voice, but that renders the default voice a touch slow/laggy, so we nudge it up. Raise toward
// 1.1 for snappier delivery, lower toward 0.8 for a more deliberate pace.
const SPEECH_SPEED = 1.1;
const EDGE_THRESHOLD = 0.015;  // |sample| above this counts as speech (below ~= silence)
const LEAD_KEEP_SAMPLES = Math.round(0.03 * SAMPLE_RATE);  // keep 30ms before first speech
const TAIL_KEEP_SAMPLES = Math.round(0.04 * SAMPLE_RATE);  // keep 40ms after last speech
const PIECE_PAUSE_SAMPLES = Math.round(0.16 * SAMPLE_RATE); // clause pause inserted between pieces
const HEAD_LEADIN_SAMPLES = Math.round(0.04 * SAMPLE_RATE); // tiny lead-in before the first piece
// Which KittenTTS voice the interviewer uses. The eight voices are expr-voice-{2..5}-{f,m};
// swap DEFAULT_VOICE to try another — '-f' are female, '-m' are male.
const DEFAULT_VOICE = "expr-voice-2-f";
// Advertised to the wrapper female-first, so its fallback default is a female voice.
const VOICE_KEYS = [
  "expr-voice-2-f", "expr-voice-3-f", "expr-voice-4-f", "expr-voice-5-f",
  "expr-voice-5-m", "expr-voice-4-m", "expr-voice-3-m", "expr-voice-2-m",
];

// Exact KittenTTS symbol table (verified byte-for-byte against KittenML/KittenTTS
// onnx_model.py TextCleaner). Order defines the token ids, so it must not change.
const SYMBOLS = ["$",";",":",",",".","!","?","¡","¿","—","…","\"","«","»","\"","\""," ","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z","ɑ","ɐ","ɒ","æ","ɓ","ʙ","β","ɔ","ɕ","ç","ɗ","ɖ","ð","ʤ","ə","ɘ","ɚ","ɛ","ɜ","ɝ","ɞ","ɟ","ʄ","ɡ","ɠ","ɢ","ʛ","ɦ","ɧ","ħ","ɥ","ʜ","ɨ","ɪ","ʝ","ɭ","ɬ","ɫ","ɮ","ʟ","ɱ","ɯ","ɰ","ŋ","ɳ","ɲ","ɴ","ø","ɵ","ɸ","θ","œ","ɶ","ʘ","ɹ","ɺ","ɾ","ɻ","ʀ","ʁ","ɽ","ʂ","ʃ","ʈ","ʧ","ʉ","ʊ","ʋ","ⱱ","ʌ","ɣ","ɤ","ʍ","χ","ʎ","ʏ","ʑ","ʐ","ʒ","ʔ","ʡ","ʕ","ʢ","ǀ","ǁ","ǂ","ǃ","ˈ","ˌ","ː","ˑ","ʼ","ʴ","ʰ","ʱ","ʲ","ʷ","ˠ","ˤ","˞","↓","↑","→","↗","↘","'","̩","'","ᵻ"];
const SYMBOL_TO_ID = new Map(SYMBOLS.map((symbol, index) => [symbol, index]));
// The boundary end token (10) is the ellipsis '…'; assert the table wasn't corrupted.
if (SYMBOLS.length !== 178 || SYMBOL_TO_ID.get("…") !== 10) {
  console.error("Kitten TTS: symbol table looks wrong", SYMBOLS.length, SYMBOL_TO_ID.get("…"));
}

// ---- state -----------------------------------------------------------------
let ort = null;
let session = null;
let phonemize = null;
let voices = {};
let voiceAliases = {};
let isReady = false;
let isGenerating = false;

// ---- text preprocessing (ported from KittenTTS-JS preprocess.ts) ------------
const SENTENCE_BREAK_REGEX = /[.!?]+/g;
const URL_REGEX = /https?:\/\/\S+|www\.\S+/gi;
const EMAIL_REGEX = /\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b/gi;
const HTML_REGEX = /<[^>]+>/g;
const WHITESPACE_REGEX = /\s+/g;
const CONTRACTIONS = [
  [/\bcan't\b/gi, "cannot"],
  [/\bwon't\b/gi, "will not"],
  [/\bshan't\b/gi, "shall not"],
  [/\bain't\b/gi, "is not"],
  [/\blet's\b/gi, "let us"],
  [/\b(\w+)n't\b/gi, "$1 not"],
  [/\b(\w+)'re\b/gi, "$1 are"],
  [/\b(\w+)'ve\b/gi, "$1 have"],
  [/\b(\w+)'ll\b/gi, "$1 will"],
  [/\b(\w+)'d\b/gi, "$1 would"],
  [/\b(\w+)'m\b/gi, "$1 am"],
  [/\bit's\b/gi, "it is"],
];

function expandContractions(text) {
  let out = text;
  for (const [pattern, replacement] of CONTRACTIONS) out = out.replace(pattern, replacement);
  return out;
}

function normalizeLeadingDecimals(text) {
  const signed = text.replace(/(?<!\d)(-)\.([\d])/g, (_m, sign, digit) => `${sign}0.${digit}`);
  return signed.replace(/(?<!\d)\.([\d])/g, (_m, digit) => `0.${digit}`);
}

// KittenTTS runs its preprocessor with removePunctuation:false (punctuation carries
// prosody), so we keep punctuation and only do the safe normalizations.
function preprocess(text) {
  let out = text.normalize("NFC");
  out = out.replace(HTML_REGEX, " ");
  out = out.replace(URL_REGEX, " ");
  out = out.replace(EMAIL_REGEX, " ");
  out = expandContractions(out);
  out = normalizeLeadingDecimals(out);
  out = out.toLowerCase();
  out = out.replace(WHITESPACE_REGEX, " ").trim();
  return out;
}

function ensurePunctuation(text) {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  return /[.!?,;:]$/.test(trimmed) ? trimmed : `${trimmed},`;
}

function isClauseEnd(word) {
  return /[,;:—–]$/.test(word);
}

// Pick the end index (exclusive) of a piece starting at `from`: stop at a clause boundary once
// we have >= STREAM_MIN_WORDS words, otherwise at `maxWords`; never leave a < MIN remainder.
function pieceEnd(words, from, maxWords) {
  let end = -1;
  for (let j = from + 1; j <= words.length; j++) {
    const taken = j - from;
    if (taken >= STREAM_MIN_WORDS && isClauseEnd(words[j - 1])) { end = j; break; }
    if (taken >= maxWords) { end = j; break; }
  }
  if (end < 0) end = words.length;
  const remainder = words.length - end;
  if (remainder > 0 && remainder < STREAM_MIN_WORDS) end = words.length;
  return end;
}

// One sentence -> ordered speakable pieces. The FIRST piece is kept small (FIRST_MAX_WORDS) so
// the interviewer starts talking quickly; every following piece is a full clause (up to the big
// STREAM_MAX_WORDS cap), so past the opener the speech is natural. Only the opener may fall on a
// mid-phrase boundary, and only when the first clause is long — i.e. at most one such seam.
function splitSentenceIntoPieces(sentence) {
  const words = sentence.match(/\S+/g) || [];
  if (words.length <= STREAM_MIN_WORDS) return [sentence.trim()];
  const pieces = [];
  let i = 0;
  let first = true;
  while (i < words.length) {
    const end = pieceEnd(words, i, first ? FIRST_MAX_WORDS : STREAM_MAX_WORDS);
    pieces.push(words.slice(i, end).join(" "));
    i = end;
    first = false;
  }
  return pieces;
}

// Whole (already-cleaned) utterance -> flat ordered list of pieces to stream.
function splitIntoStreamPieces(cleanText) {
  const pieces = [];
  for (const sentence of cleanText.split(SENTENCE_BREAK_REGEX)) {
    const value = sentence.trim();
    if (!value) continue;
    for (const piece of splitSentenceIntoPieces(value)) {
      const p = ensurePunctuation(piece);
      if (p) pieces.push(p);
    }
  }
  return pieces;
}

// ---- tokenization -----------------------------------------------------------
function basicEnglishTokenize(text) {
  return text.match(/[\p{L}\p{M}\p{N}_]+|[^\p{L}\p{M}\p{N}_\s]/gu) ?? [];
}

// phonemes (IPA string) -> token ids, wrapped with KittenTTS boundary tokens [0, …, 10, 0].
function phonemesToTokens(phonemeString) {
  const normalized = basicEnglishTokenize(phonemeString).join(" ");
  const ids = [];
  for (const char of normalized) {
    const id = SYMBOL_TO_ID.get(char);
    if (id !== undefined) ids.push(id);
  }
  return [0, ...ids, 10, 0];
}

// ---- voices.npz (ZIP64, STORED entries) -> style matrices -------------------
function parseVoicesNpz(buffer) {
  const bytes = new Uint8Array(buffer);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const table = {};
  for (let i = 0; i + 4 <= bytes.length; ) {
    if (dv.getUint32(i, true) !== 0x04034b50) break; // reached central directory
    const method = dv.getUint16(i + 8, true);
    let compSize = dv.getUint32(i + 18, true);
    let uncompSize = dv.getUint32(i + 22, true);
    const nameLen = dv.getUint16(i + 26, true);
    const extraLen = dv.getUint16(i + 28, true);
    const nameStart = i + 30;
    const name = new TextDecoder().decode(bytes.subarray(nameStart, nameStart + nameLen));
    const extraStart = nameStart + nameLen;
    // numpy writes ZIP64 entries: the 32-bit size fields read 0xFFFFFFFF and the real
    // 64-bit sizes live in the 0x0001 extra field (uncompressed first, then compressed).
    for (let e = extraStart; e + 4 <= extraStart + extraLen; ) {
      const tag = dv.getUint16(e, true);
      const size = dv.getUint16(e + 2, true);
      if (tag === 0x0001) {
        let p = e + 4;
        if (uncompSize === 0xffffffff) { uncompSize = Number(dv.getBigUint64(p, true)); p += 8; }
        if (compSize === 0xffffffff) { compSize = Number(dv.getBigUint64(p, true)); p += 8; }
      }
      e += 4 + size;
    }
    const dataStart = extraStart + extraLen;
    if (method !== 0) throw new Error(`voices.npz entry ${name} is compressed (method ${method}); expected STORED`);
    if (name.endsWith(".npy")) {
      table[name.replace(/^.*\//, "").replace(/\.npy$/, "")] = parseNpy(bytes.subarray(dataStart, dataStart + compSize));
    }
    i = dataStart + compSize;
  }
  if (!Object.keys(table).length) throw new Error("No .npy voice arrays found in voices.npz");
  return table;
}

function parseNpy(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (String.fromCharCode(...bytes.subarray(1, 6)) !== "NUMPY") throw new Error("Invalid NPY header");
  const major = bytes[6];
  const headerLen = major === 1 ? dv.getUint16(8, true) : dv.getUint32(8, true);
  const headerOff = major === 1 ? 10 : 12;
  const header = new TextDecoder("ascii").decode(bytes.subarray(headerOff, headerOff + headerLen));
  const descr = /'descr'\s*:\s*'([^']+)'/.exec(header)[1];
  if (!descr.endsWith("f4")) throw new Error(`Unsupported NPY dtype ${descr}`);
  const shape = /'shape'\s*:\s*\(([^)]*)\)/.exec(header)[1]
    .split(",").map((s) => s.trim()).filter(Boolean).map((s) => parseInt(s, 10));
  const [rows, cols] = shape.length === 2 ? shape : [1, shape[0]];
  const count = rows * cols;
  const dataOff = headerOff + headerLen;
  // copy so the Float32Array view is guaranteed 4-byte aligned
  const slice = bytes.slice(dataOff, dataOff + count * 4);
  return { rows, cols, values: new Float32Array(slice.buffer, slice.byteOffset, count) };
}

// KittenTTS picks a style row by text length (ref_id = min(len(text), rows-1)).
function pickStyle(voice, textLength) {
  const row = Math.min(textLength, voice.rows - 1);
  const start = row * voice.cols;
  return voice.values.slice(start, start + voice.cols);
}

function resolveVoiceKey(requested) {
  if (requested && voices[requested]) return requested;
  const aliased = requested && voiceAliases[requested];
  if (aliased && voices[aliased]) return aliased;
  return voices[DEFAULT_VOICE] ? DEFAULT_VOICE : Object.keys(voices)[0];
}

// ---- loading ----------------------------------------------------------------
async function loadOrt() {
  if (ort) return;
  const module = await import(`${ORT_CDN}ort.min.mjs`);
  ort = module.default || module;
  ort.env.wasm.wasmPaths = ORT_CDN;
  ort.env.wasm.simd = true;
  // Multi-threaded when the page is cross-origin isolated (see vite.config headers),
  // single-threaded otherwise. KittenTTS is fast enough either way for short sentences.
  ort.env.wasm.numThreads = self.crossOriginIsolated
    ? Math.min(navigator.hardwareConcurrency || 4, 8)
    : 1;
}

function hfUrl(file) {
  return `${HF_BASE}/${MODEL_REPO}/resolve/main/${file}`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url} (${res.status})`);
  return res.json();
}

async function fetchBuffer(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url} (${res.status})`);
  return res.arrayBuffer();
}

async function loadEngine() {
  postMessage({ type: "status", status: "Loading ONNX Runtime...", state: "loading" });
  await loadOrt();

  postMessage({ type: "status", status: "Loading phonemizer...", state: "loading" });
  const phonemizerModule = await import(PHONEMIZER_URL);
  phonemize = phonemizerModule.phonemize;
  if (typeof phonemize !== "function") throw new Error("phonemizer module does not export phonemize()");

  postMessage({ type: "status", status: "Loading voice model...", state: "loading" });
  const config = await fetchJson(hfUrl("config.json"));
  voiceAliases = config.voice_aliases || {};

  const [modelBuffer, voicesBuffer] = await Promise.all([
    fetchBuffer(hfUrl(config.model_file || "kitten_tts_nano_v0_8.onnx")),
    fetchBuffer(hfUrl(config.voices || "voices.npz")),
  ]);
  voices = parseVoicesNpz(voicesBuffer);
  session = await ort.InferenceSession.create(new Uint8Array(modelBuffer), {
    executionProviders: ["wasm"],
    graphOptimizationLevel: "all",
  });

  // Warm the eSpeak-NG WASM so the first real utterance isn't delayed by its init.
  try {
    await phonemize("ok", PHONEME_LANGUAGE);
  } catch (err) {
    console.warn("Kitten TTS: phonemizer warm-up failed", err);
  }

  isReady = true;
  const available = VOICE_KEYS.filter((key) => voices[key]);
  const defaultVoice = voices[DEFAULT_VOICE] ? DEFAULT_VOICE : available[0] || Object.keys(voices)[0];
  postMessage({ type: "voices_loaded", voices: available, defaultVoice, language: "en" });
  postMessage({ type: "status", status: "Ready", state: "idle" });
  postMessage({ type: "model_status", status: "ready", text: "Ready (kitten)" });
  postMessage({ type: "loaded" });
}

// ---- generation -------------------------------------------------------------
// Trim a rendered piece down to its actual speech (drop the utterance-final artifact and
// the utterance-initial silence), keeping small margins so onsets/releases aren't clipped.
function trimToSpeechCore(audio) {
  let start = -1;
  for (let i = 0; i < audio.length; i++) {
    if (Math.abs(audio[i]) > EDGE_THRESHOLD) { start = i; break; }
  }
  if (start < 0) return new Float32Array(0); // all silence
  let end = audio.length - 1;
  for (let i = audio.length - 1; i >= 0; i--) {
    if (Math.abs(audio[i]) > EDGE_THRESHOLD) { end = i; break; }
  }
  const from = Math.max(0, start - LEAD_KEEP_SAMPLES);
  const to = Math.min(audio.length, end + TAIL_KEEP_SAMPLES + 1);
  return audio.slice(from, to); // fresh, transferable buffer
}

// Synthesize one piece and return just its speech core (no surrounding silence).
async function synthesizePieceCore(piece, voice, speed) {
  const segments = await phonemize(piece, PHONEME_LANGUAGE);
  // eSpeak returns one string per clause (split on punctuation, which it drops). Rejoin
  // with ", " so the tokenizer sees the clause pauses — matching the reference Python's
  // preserve_punctuation output. (Taking only segments[0] would drop text after a comma.)
  const phonemeString = Array.isArray(segments) ? segments.join(", ") : String(segments || "");
  const tokens = phonemesToTokens(phonemeString);
  if (tokens.length <= 3) return null; // nothing but boundary tokens

  // Style row is picked by the piece's own text length (the reference KittenTTS behavior);
  // with clause-only splitting each piece is a whole clause, so this gives natural pacing.
  const style = pickStyle(voice, piece.length);
  const feeds = {
    input_ids: new ort.Tensor("int64", BigInt64Array.from(tokens, (t) => BigInt(t)), [1, tokens.length]),
    style: new ort.Tensor("float32", style, [1, style.length]),
    speed: new ort.Tensor("float32", Float32Array.from([speed]), [1]),
  };
  const outputs = await session.run(feeds);
  const waveform = outputs.waveform || outputs[session.outputNames[0]];
  let audio = waveform.data;
  if (!(audio instanceof Float32Array)) audio = Float32Array.from(audio);
  return trimToSpeechCore(audio);
}

// Concatenate the speech core with a lead-in / clause pause so pieces butt together
// smoothly on the wrapper's timeline. Returns a fresh transferable Float32Array.
function buildChunk(core, isFirst, isLast) {
  const lead = isFirst ? HEAD_LEADIN_SAMPLES : 0;
  const pause = isLast ? 0 : PIECE_PAUSE_SAMPLES;
  if (!lead && !pause) return core;
  const out = new Float32Array(lead + core.length + pause);
  out.set(core, lead);
  return out;
}

async function startGeneration(text, voiceKey) {
  isGenerating = true;
  postMessage({ type: "status", status: "Generating...", state: "running" });
  postMessage({ type: "generation_started", data: { time: performance.now() } });

  try {
    const voice = voices[voiceKey];
    if (!voice) throw new Error(`Voice '${voiceKey}' is not available`);
    const pieces = splitIntoStreamPieces(preprocess(text));

    for (let i = 0; i < pieces.length; i++) {
      if (!isGenerating) break;
      const core = await synthesizePieceCore(pieces[i], voice, SPEECH_SPEED);
      if (!isGenerating || !core || core.length === 0) continue;

      const chunk = buildChunk(core, i === 0, i === pieces.length - 1);
      postMessage(
        {
          type: "audio_chunk",
          data: chunk,
          metrics: {
            chunkDuration: chunk.length / SAMPLE_RATE,
            isFirst: i === 0,
            isLast: i === pieces.length - 1,
          },
        },
        [chunk.buffer]
      );
      // Yield so a 'stop' (barge-in) posted mid-utterance is handled between pieces.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  } catch (err) {
    console.error("Kitten TTS generation error:", err);
    postMessage({ type: "error", error: String(err) });
  } finally {
    if (isGenerating) postMessage({ type: "stream_ended" });
    isGenerating = false;
    postMessage({ type: "status", status: "Ready", state: "idle" });
  }
}

// ---- message protocol (unchanged from the wrapper's perspective) ------------
self.onmessage = async (e) => {
  const { type, data } = e.data;
  try {
    if (type === "load") {
      await loadEngine();
      return;
    }
    if (type === "stop") {
      isGenerating = false;
      postMessage({ type: "status", status: "Stopped", state: "idle" });
      return;
    }
    if (!isReady) {
      postMessage({ type: "error", error: "Models are not loaded yet." });
      return;
    }
    // set_voice / set_language / encode_voice: the wrapper doesn't depend on these, but
    // accept a voice switch harmlessly so the protocol stays a superset of the old one.
    if (type === "set_voice") {
      const key = resolveVoiceKey(data && data.voiceName);
      postMessage({ type: "voice_set", voiceName: key });
      postMessage({ type: "status", status: "Ready", state: "idle" });
      return;
    }
    if (type === "generate") {
      if (isGenerating) return;
      await startGeneration(data.text, resolveVoiceKey(data.voice));
      return;
    }
  } catch (err) {
    console.error("Worker error:", err);
    postMessage({ type: "error", error: String(err) });
  }
};
