// Runs on the audio rendering thread. Resamples the mic's native sample rate down to the
// target rate (16kHz, what Deepgram/Azure STT expect) via linear interpolation, converts to
// 16-bit PCM, and posts ~100ms chunks back to the main thread. No client-side VAD/gating here
// by design — every chunk goes out continuously while the node is connected; muting is handled
// by the main thread choosing not to forward chunks over the WebSocket, not by silencing here.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const targetSampleRate = (options.processorOptions && options.processorOptions.targetSampleRate) || 16000
    this.ratio = sampleRate / targetSampleRate
    this.srcPos = 0
    this.inputBuffer = []
    this.outSamples = []
    this.outChunkTarget = Math.round(targetSampleRate * 0.1) // ~100ms per chunk
  }

  process(inputs) {
    const channelData = inputs[0] && inputs[0][0]
    if (channelData) {
      for (let i = 0; i < channelData.length; i++) this.inputBuffer.push(channelData[i])
    }

    while (Math.floor(this.srcPos) + 1 < this.inputBuffer.length) {
      const idx0 = Math.floor(this.srcPos)
      const frac = this.srcPos - idx0
      const s0 = this.inputBuffer[idx0]
      const s1 = this.inputBuffer[idx0 + 1]
      this.outSamples.push(s0 + (s1 - s0) * frac)
      this.srcPos += this.ratio

      if (this.outSamples.length >= this.outChunkTarget) {
        this._emit()
      }
    }

    const consumed = Math.floor(this.srcPos)
    if (consumed > 0) {
      this.inputBuffer.splice(0, consumed)
      this.srcPos -= consumed
    }

    return true
  }

  _emit() {
    const n = this.outSamples.length
    const int16 = new Int16Array(n)
    for (let i = 0; i < n; i++) {
      let s = this.outSamples[i]
      s = Math.max(-1, Math.min(1, s))
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
    }
    this.port.postMessage(int16.buffer, [int16.buffer])
    this.outSamples = []
  }
}

registerProcessor('pcm-worklet-processor', PCMWorkletProcessor)
