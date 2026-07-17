export interface MicCapture {
  analyser: AnalyserNode
  stop: () => void
}

/** Starts continuous 16kHz PCM capture from `stream` via the AudioWorklet, invoking `onChunk`
 * for every ~100ms of resampled audio. Also exposes an AnalyserNode (on the ORIGINAL stream, at
 * native sample rate) purely for local UI amplitude visualization — entirely separate from the
 * server-side turn-taking VAD, which is what the spec means by "don't use browser VAD" for
 * actual speech/silence decisions. */
export async function startMicCapture(stream: MediaStream, onChunk: (chunk: ArrayBuffer) => void): Promise<MicCapture> {
  const audioContext = new AudioContext()
  await audioContext.audioWorklet.addModule('/audio/pcm-worklet-processor.js')

  const source = audioContext.createMediaStreamSource(stream)

  const analyser = audioContext.createAnalyser()
  analyser.fftSize = 256
  source.connect(analyser)

  const workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet-processor', {
    processorOptions: { targetSampleRate: 16000 },
  })
  workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    onChunk(event.data)
  }
  source.connect(workletNode)
  // Deliberately not connected to audioContext.destination — we never want to hear our own mic.

  return {
    analyser,
    stop: () => {
      workletNode.port.onmessage = null
      source.disconnect()
      workletNode.disconnect()
      analyser.disconnect()
      void audioContext.close()
    },
  }
}
