// Both providers (Deepgram Aura-2 for English, Azure Neural for Arabic-Omani) are configured
// server-side to emit raw linear16 PCM at this rate — see deepgram_provider.py / azure_provider.py.
const PLAYBACK_SAMPLE_RATE = 24000

// Small cushion applied only when starting a fresh utterance (the schedule was empty/caught
// up) — absorbs network jitter and any main-thread scheduling delay between chunks arriving
// and enqueue() actually running, without accumulating unbounded latency for chunks that
// arrive while already comfortably ahead of schedule.
const SCHEDULE_AHEAD_S = 0.08

/** Gapless playback queue for agent TTS audio, with an immediate `clear()` for barge-in — the
 * moment `agent_interrupted` arrives, every scheduled/playing chunk is stopped instantly,
 * regardless of how much more audio the server had already sent before the interruption was
 * confirmed (mirrors the server's own "just stop forwarding" approach to perceived silence). */
export class AgentAudioPlayer {
  readonly analyser: AnalyserNode
  private audioContext: AudioContext
  private nextStartTime = 0
  private activeSources: AudioBufferSourceNode[] = []

  constructor() {
    this.audioContext = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE })
    this.analyser = this.audioContext.createAnalyser()
    this.analyser.fftSize = 256
    this.analyser.connect(this.audioContext.destination)
  }

  enqueue(pcmBytes: ArrayBuffer): void {
    const int16 = new Int16Array(pcmBytes)
    if (int16.length === 0) return
    const float32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) {
      const sample = int16[i]
      float32[i] = sample / (sample < 0 ? 0x8000 : 0x7fff)
    }

    const buffer = this.audioContext.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE)
    buffer.copyToChannel(float32, 0)

    const source = this.audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyser)

    const now = this.audioContext.currentTime
    // Only add the cushion when starting fresh (queue was empty/caught up) — if we're
    // already scheduled ahead (nextStartTime > now), chunks are keeping pace and stacking
    // more lookahead on top of a healthy queue would just add creeping latency for nothing.
    const startTime = this.nextStartTime > now ? this.nextStartTime : now + SCHEDULE_AHEAD_S
    source.start(startTime)
    this.nextStartTime = startTime + buffer.duration

    this.activeSources.push(source)
    source.onended = () => {
      this.activeSources = this.activeSources.filter((s) => s !== source)
    }
  }

  clear(): void {
    for (const source of this.activeSources) {
      try {
        source.stop()
      } catch {
        // already stopped/ended — fine
      }
    }
    this.activeSources = []
    this.nextStartTime = this.audioContext.currentTime
  }

  close(): void {
    this.clear()
    void this.audioContext.close()
  }
}
