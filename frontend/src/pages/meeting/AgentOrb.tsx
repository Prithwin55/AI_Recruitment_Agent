import { motion, useTransform } from 'framer-motion'
import { useAudioAmplitude } from '@/lib/interview/useAudioAmplitude'
import { cn } from '@/lib/utils'

export type OrbState = 'idle' | 'listening' | 'speaking' | 'thinking' | 'interrupted'

interface AgentOrbProps {
  state: OrbState
  micAnalyser: AnalyserNode | null
  agentAnalyser: AnalyserNode | null
}

const STATE_GLOW_COLOR: Record<OrbState, string> = {
  idle: 'oklch(0.47 0.17 264 / 0.4)',
  listening: 'oklch(0.75 0.15 220 / 0.55)',
  speaking: 'oklch(0.47 0.17 264 / 0.65)',
  thinking: 'oklch(0.65 0.18 300 / 0.55)',
  interrupted: 'oklch(0.75 0.15 80 / 0.65)',
}

const STATE_CORE_GRADIENT: Record<OrbState, string> = {
  idle: 'from-primary/80 to-primary/40',
  listening: 'from-sky-400 to-sky-500/60',
  speaking: 'from-primary to-primary/60',
  thinking: 'from-violet-400 to-violet-500/60',
  interrupted: 'from-warning to-warning/60',
}

export function AgentOrb({ state, micAnalyser, agentAnalyser }: AgentOrbProps) {
  const reactive = state === 'listening' || state === 'speaking'
  const activeAnalyser = state === 'listening' ? micAnalyser : state === 'speaking' ? agentAnalyser : null
  // A MotionValue, not React state: updates at 60fps without ever triggering a re-render of
  // this component (or anything above it) — see useAudioAmplitude for why that matters here.
  const amplitude = useAudioAmplitude(activeAnalyser, reactive)

  const baseScale = state === 'idle' ? 0.85 : state === 'thinking' ? 0.95 : 1
  // Derived MotionValue: Framer Motion recomputes this on amplitude changes internally,
  // still without going through React. Re-created each render (cheap — state/baseScale only
  // change a few times per turn, not 60x/sec), so it always reflects the current baseScale.
  const reactiveScale = useTransform(amplitude, (a) => baseScale + a * 0.22)

  return (
    <div className="relative flex h-56 w-56 items-center justify-center sm:h-64 sm:w-64">
      <motion.div
        className="absolute inset-0 rounded-full blur-2xl"
        style={reactive ? { scale: reactiveScale, opacity: 0.7 } : undefined}
        animate={
          reactive
            ? { opacity: 0.7 }
            : { scale: state === 'idle' ? [0.8, 0.88, 0.8] : [0.9, 1.02, 0.9], opacity: state === 'interrupted' ? 0.9 : 0.7 }
        }
        transition={
          reactive
            ? { duration: 0.15 }
            : { duration: state === 'idle' ? 3.2 : 1.1, repeat: Infinity, ease: 'easeInOut' }
        }
      >
        <div
          className="h-full w-full rounded-full"
          style={{ background: `radial-gradient(circle, ${STATE_GLOW_COLOR[state]}, transparent 70%)` }}
        />
      </motion.div>

      <motion.div
        className={cn(
          'relative h-32 w-32 rounded-full bg-gradient-to-br shadow-2xl sm:h-36 sm:w-36',
          STATE_CORE_GRADIENT[state],
        )}
        // While listening/speaking, scale is driven entirely by the `style`-bound MotionValue
        // above (no `animate` target for it — mixing the two for the same property is what
        // forced Framer Motion to compute a brand-new transition on every amplitude tick,
        // which is the main-thread cost this component used to add on top of WS audio
        // handling). Idle/thinking/interrupted use the ordinary declarative `animate` prop,
        // which only changes a few times per turn.
        style={reactive ? { scale: reactiveScale } : undefined}
        animate={
          reactive
            ? undefined
            : state === 'idle'
              ? { scale: [1, 1.05, 1], rotate: 0 }
              : state === 'thinking'
                ? { scale: [0.96, 1.06, 0.96], rotate: [0, 360] }
                : { scale: baseScale, rotate: 0 }
        }
        transition={
          state === 'idle'
            ? { duration: 3.2, repeat: Infinity, ease: 'easeInOut' }
            : state === 'thinking'
              ? { duration: 2.4, repeat: Infinity, ease: 'linear' }
              : { duration: 0.2, ease: 'easeOut' }
        }
      />

      {state === 'interrupted' && (
        <motion.div
          className="absolute inset-0 rounded-full border-2 border-warning"
          initial={{ scale: 0.9, opacity: 0.9 }}
          animate={{ scale: 1.4, opacity: 0 }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
        />
      )}
    </div>
  )
}
