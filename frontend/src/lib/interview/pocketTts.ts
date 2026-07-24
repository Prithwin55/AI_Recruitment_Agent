/** Client-side text-to-speech using Pocket TTS (kyutai-labs) running as ONNX/WASM entirely in
 * the candidate's browser. This is what lets the SERVER do zero synthesis work: no matter how
 * many interviews run at once, each candidate's machine renders its own agent voice, so there
 * is no server-side TTS concurrency or lag.
 *
 * The heavy lifting lives in the vendored engine under /public/pockettts/ (a Web Worker that
 * runs the ONNX models + a PCM AudioWorklet player, taken verbatim from KevinAHM's ONNX port).
 * This module is a thin, framework-agnostic wrapper over that engine's message protocol:
 *   worker <- { type:'load' }                          -> 'voices_loaded', 'loaded'
 *   worker <- { type:'generate', data:{text, voice} }  -> 'audio_chunk'*, 'stream_ended'
 *   worker <- { type:'stop' }
 * Audio chunks (24kHz Float32 PCM) are fed to the PCM player; speak() resolves once the player
 * has fully drained that utterance, which is exactly when the interview turn-taking wants to
 * know "the agent finished saying this sentence."
 *
 * Everything is best-effort: init() throws if the model can't load, and the caller falls back
 * to the browser's built-in speechSynthesis so the interview still talks. */

const ENGINE_BASE = '/pockettts'
const SAMPLE_RATE = 24000

// Which Pocket TTS voice the interviewer uses. Available English voices:
//   female: alba, azelma, cosette, eponine, fantine   male: javert, jean, marius
// Change this to try a different one — they vary a lot in how natural they sound. If the value
// isn't in the loaded bundle we fall back to the bundle's own default.
const PREFERRED_VOICE = 'cosette'
// Start speaking once this much audio is buffered ahead, rather than waiting for the whole
// sentence. Small enough to feel responsive/conversational, large enough to ride out brief
// inference dips or main-thread stalls (the MediaPipe proctoring runs on the main thread).
const PREROLL_S = 0.4
// Tiny extra lead when scheduling the very first chunk, to absorb scheduling jitter.
const START_CUSHION_S = 0.08

export class PocketTts {
  private audioContext: AudioContext | null = null
  private analyserNode: AnalyserNode | null = null
  private worker: Worker | null = null
  private voice: string | null = null
  private ready = false

  // Playback is streamed but glitch-resistant: chunks are scheduled back-to-back on the audio
  // timeline (AudioBufferSourceNode), so once a chunk is scheduled it plays on the audio thread
  // and can't be starved by slow inference or a busy main thread. We only start after PREROLL_S
  // is buffered, which gives a cushion; since Pocket TTS is typically faster than real-time, the
  // lead then grows on its own. (The earlier streaming worklet broke because its delivery was
  // tied to a main-thread backpressure loop; this schedules directly.)
  private speaking = false
  private started = false
  private preroll: Float32Array[] = []
  private nextStartTime = 0
  private activeSources: AudioBufferSourceNode[] = []
  private synthEnded = false
  private speakResolve: (() => void) | null = null

  /** Loads the WASM engine + models. Rejects if anything fails so the caller can fall back.
   * The first call downloads the model bundle (~180MB, then browser-cached), so it can take a
   * while on a cold cache — surface a "preparing voice" state to the candidate if you show one. */
  async init(): Promise<void> {
    // Cross-origin isolation is what lets the ONNX worker run multi-threaded (see
    // inference-worker.js: numThreads = crossOriginIsolated ? nCores : 1). Without it synthesis
    // runs single-threaded and is slower than real-time, so the agent voice breaks up / sounds
    // robotic on playback. This is an operational/serving problem, not a code one — surface it
    // loudly rather than degrading in silence. Requires BOTH a secure context (HTTPS/localhost)
    // AND COOP:same-origin + COEP:credentialless headers (see vite.config.ts / nginx.conf / Caddyfile).
    if (!self.crossOriginIsolated) {
      console.warn(
        '[tts] self.crossOriginIsolated === false — Pocket TTS will run SINGLE-THREADED and the ' +
          'agent voice will break up / sound robotic. Serve this page over HTTPS with ' +
          'Cross-Origin-Opener-Policy: same-origin and Cross-Origin-Embedder-Policy: credentialless.',
      )
    }
    this.audioContext = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: 'interactive' })
    this.analyserNode = this.audioContext.createAnalyser()
    this.analyserNode.fftSize = 256
    this.analyserNode.connect(this.audioContext.destination)

    this.worker = new Worker(`${ENGINE_BASE}/inference-worker.js`, { type: 'module' })

    await new Promise<void>((resolve, reject) => {
      const onMessage = (e: MessageEvent) => {
        const { type } = e.data
        if (type === 'voices_loaded') {
          const voices: string[] = e.data.voices ?? []
          // Log the options so a voice can be chosen by ear (see PREFERRED_VOICE).
          console.info('[tts] available voices:', voices, '| default:', e.data.defaultVoice)
          this.voice = voices.includes(PREFERRED_VOICE)
            ? PREFERRED_VOICE
            : (e.data.defaultVoice ?? voices[0] ?? null)
        } else if (type === 'loaded') {
          this.ready = true
          resolve()
        } else if (type === 'audio_chunk') {
          if (this.speaking && e.data.data) this.onChunk(e.data.data as Float32Array)
        } else if (type === 'stream_ended') {
          this.onStreamEnded()
        } else if (type === 'error') {
          if (!this.ready) reject(new Error(e.data.error || 'Pocket TTS worker error'))
          else this.finishSpeak() // an error mid-utterance: unblock the pending speak()
        }
      }
      this.worker!.addEventListener('message', onMessage)
      this.worker!.addEventListener('error', (err) => {
        if (!this.ready) reject(err.error || new Error('Pocket TTS worker failed to start'))
      })
      this.worker!.postMessage({ type: 'load' })
    })
  }

  /** AnalyserNode on the agent's output — drives the meeting-room orb amplitude. */
  get analyser(): AnalyserNode | null {
    return this.analyserNode
  }

  get isReady(): boolean {
    return this.ready
  }

  /** Synthesize + stream one sentence, resolving once it has fully finished playing (or once
   * stop() is called). Serialized — the caller awaits each before sending the next. */
  async speak(text: string): Promise<void> {
    if (!this.ready || !this.worker || !this.audioContext || !text.trim()) return
    if (this.audioContext.state === 'suspended') {
      try {
        await this.audioContext.resume()
      } catch {
        /* ignore — sources just won't be audible until running */
      }
    }
    if (this.speaking) this.finishSpeak() // end any straggler first

    this.speaking = true
    this.started = false
    this.synthEnded = false
    this.preroll = []
    this.activeSources = []
    this.nextStartTime = 0

    return new Promise<void>((resolve) => {
      this.speakResolve = resolve
      this.worker!.postMessage({ type: 'generate', data: { text, voice: this.voice } })
    })
  }

  /** Barge-in: stop synthesizing and playback immediately, resolving any pending speak(). */
  stop(): void {
    if (!this.speaking) return
    try {
      this.worker?.postMessage({ type: 'stop' })
    } catch {
      /* worker may be gone */
    }
    this.stopAllSources()
    this.finishSpeak()
  }

  close(): void {
    this.stop()
    try {
      this.worker?.terminate()
    } catch {
      /* ignore */
    }
    this.worker = null
    void this.audioContext?.close()
    this.audioContext = null
    this.analyserNode = null
    this.ready = false
  }

  private onChunk(chunk: Float32Array): void {
    if (!this.started) {
      this.preroll.push(chunk)
      const buffered = this.preroll.reduce((n, c) => n + c.length, 0) / SAMPLE_RATE
      if (buffered >= PREROLL_S) this.beginPlayback()
    } else {
      this.scheduleChunk(chunk)
    }
  }

  private onStreamEnded(): void {
    if (!this.speaking) return
    this.synthEnded = true
    if (!this.started) this.beginPlayback() // sentence shorter than the preroll — play what we have
    if (this.activeSources.length === 0) this.finishSpeak()
  }

  private beginPlayback(): void {
    if (!this.audioContext) return
    this.started = true
    this.nextStartTime = this.audioContext.currentTime + START_CUSHION_S
    const pending = this.preroll
    this.preroll = []
    for (const chunk of pending) this.scheduleChunk(chunk)
  }

  private scheduleChunk(chunk: Float32Array): void {
    if (!this.audioContext || !this.analyserNode || chunk.length === 0) return
    const buffer = this.audioContext.createBuffer(1, chunk.length, SAMPLE_RATE)
    buffer.getChannelData(0).set(chunk)
    const source = this.audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyserNode)

    const now = this.audioContext.currentTime
    // If synthesis fell behind real playback the lead is spent; start now (a small seam) rather
    // than scheduling in the past. With Pocket TTS usually faster than real-time this is rare.
    const startAt = this.nextStartTime > now ? this.nextStartTime : now
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration

    this.activeSources.push(source)
    source.onended = () => {
      this.activeSources = this.activeSources.filter((s) => s !== source)
      if (this.speaking && this.synthEnded && this.activeSources.length === 0) this.finishSpeak()
    }
  }

  private stopAllSources(): void {
    for (const source of this.activeSources) {
      try {
        source.onended = null
        source.stop()
      } catch {
        /* already stopped */
      }
    }
    this.activeSources = []
    this.preroll = []
  }

  private finishSpeak(): void {
    this.speaking = false
    this.started = false
    this.stopAllSources()
    const resolve = this.speakResolve
    this.speakResolve = null
    if (resolve) resolve()
  }
}

/** Minimal fallback so the interview still speaks if the WASM engine can't initialize (offline,
 * blocked CDN, unsupported device). Uses the browser's built-in speech synthesis. */
export class BrowserTts {
  private analyserNode: AnalyserNode | null = null

  get analyser(): AnalyserNode | null {
    return this.analyserNode
  }

  get isReady(): boolean {
    return typeof window !== 'undefined' && 'speechSynthesis' in window
  }

  async init(): Promise<void> {
    if (!this.isReady) throw new Error('speechSynthesis not available')
  }

  speak(text: string): Promise<void> {
    return new Promise<void>((resolve) => {
      if (!text.trim() || !this.isReady) {
        resolve()
        return
      }
      const utter = new SpeechSynthesisUtterance(text)
      utter.onend = () => resolve()
      utter.onerror = () => resolve()
      window.speechSynthesis.speak(utter)
    })
  }

  stop(): void {
    if (this.isReady) window.speechSynthesis.cancel()
  }

  close(): void {
    this.stop()
  }
}

export type AgentVoice = PocketTts | BrowserTts
