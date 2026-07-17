import { useEffect, useRef } from 'react'
import { useMotionValue, type MotionValue } from 'framer-motion'

/** Smoothed 0-1 RMS amplitude from an AnalyserNode, exposed as a Framer Motion MotionValue
 * rather than React state. This is deliberate, not a style choice: updating via `.set()`
 * notifies Framer Motion's own subscribers directly and never triggers a React re-render —
 * critical here specifically, since this hook runs at 60fps for the entire time the agent is
 * speaking, which is exactly when the main thread must stay free to receive WebSocket audio
 * chunks and schedule them for gapless playback. An earlier version used useState (even
 * throttled to ~20fps) and still competed for the same thread doing that scheduling — audible
 * as choppy/laggy agent audio. Purely a client-side visual cue; has no bearing on turn-taking
 * decisions, which are made server-side from the actual STT/TTS event streams. */
export function useAudioAmplitude(analyser: AnalyserNode | null, active: boolean): MotionValue<number> {
  const amplitude = useMotionValue(0)
  const smoothedRef = useRef(0)

  useEffect(() => {
    if (!analyser || !active) {
      amplitude.set(0)
      smoothedRef.current = 0
      return
    }

    let rafId: number
    const data = new Uint8Array(analyser.fftSize)

    const tick = () => {
      analyser.getByteTimeDomainData(data)
      let sumSquares = 0
      for (let i = 0; i < data.length; i++) {
        const centered = (data[i] - 128) / 128
        sumSquares += centered * centered
      }
      const rms = Math.sqrt(sumSquares / data.length)
      smoothedRef.current = smoothedRef.current * 0.7 + rms * 0.3
      amplitude.set(Math.min(1, smoothedRef.current * 3.5))
      rafId = requestAnimationFrame(tick)
    }

    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [analyser, active, amplitude])

  return amplitude
}
