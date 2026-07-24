import { FaceLandmarker, FilesetResolver, type FaceLandmarkerResult } from '@mediapipe/tasks-vision'

/** Client-side, lightweight proctoring. Runs Google MediaPipe FaceLandmarker entirely in the
 * browser (WASM) on the local camera stream — no video ever leaves the candidate's machine and
 * there is zero server cost. It raises four kinds of integrity signals:
 *   - multiple_faces: more than one person visible
 *   - no_face:        candidate not visible / left the frame
 *   - looking_away:   gaze held off-screen (e.g. reading from elsewhere)
 *   - head_turned:    head turned away from the screen
 *
 * Everything degrades gracefully: if the model can't load (offline, blocked CDN, unsupported
 * device) the interview continues completely normally with detection simply disabled. The
 * "multiple voices" signal is NOT done here — it's a server-side concern (active only when the
 * STT provides speaker labels). This is purely informational and never affects interview scoring. */

export type CheatKind = 'multiple_faces' | 'no_face' | 'looking_away' | 'head_turned'

export interface CheatFlag {
  kind: CheatKind
  detail: string
}

// MediaPipe public assets (documented). For fully offline/production use, download these into
// the app's own /public and point the two URLs below at same-origin paths instead.
const WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm'
const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'

// Run detection ~5x/second — plenty for proctoring, and gentle on the main thread (the audio
// pipeline is the priority; see audioPlayback / useAudioAmplitude notes).
const DETECT_INTERVAL_MS = 200

// A condition must persist this long before it's reported once — filters out momentary glances,
// a hand passing the camera, brief occlusion, etc. Tuned generously to avoid nagging.
const SUSTAIN_MS: Record<CheatKind, number> = {
  multiple_faces: 1500,
  no_face: 2500,
  looking_away: 2000,
  head_turned: 2000,
}

const DETAIL: Record<CheatKind, string> = {
  multiple_faces: 'More than one person detected on camera',
  no_face: 'Candidate not visible on camera',
  looking_away: 'Looking away from the screen',
  head_turned: 'Head turned away from the screen',
}

// Gaze blendshape score (0-1) above which the eyes are considered clearly off-centre.
const GAZE_THRESHOLD = 0.55
// How far the nose can sit off the horizontal midpoint between the cheeks (0.5 = centred)
// before the head counts as turned.
const YAW_DEVIATION_THRESHOLD = 0.19

// MediaPipe FaceMesh landmark indices.
const NOSE_TIP = 1
const CHEEK_LEFT = 234
const CHEEK_RIGHT = 454

interface ConditionState {
  since: number | null
  emitted: boolean
}

export class CheatingDetector {
  private readonly video: HTMLVideoElement
  private readonly onFlag: (flag: CheatFlag) => void
  private landmarker: FaceLandmarker | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private stopped = false
  private paused = false
  private lastVideoTime = -1
  private readonly cond: Record<CheatKind, ConditionState> = {
    multiple_faces: { since: null, emitted: false },
    no_face: { since: null, emitted: false },
    looking_away: { since: null, emitted: false },
    head_turned: { since: null, emitted: false },
  }

  constructor(video: HTMLVideoElement, onFlag: (flag: CheatFlag) => void) {
    this.video = video
    this.onFlag = onFlag
  }

  /** Loads the model and starts the detection loop. Never throws — on failure it logs and
   * leaves detection disabled so the interview is unaffected. */
  async start(): Promise<void> {
    try {
      const fileset = await FilesetResolver.forVisionTasks(WASM_URL)
      this.landmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
        runningMode: 'VIDEO',
        numFaces: 2,
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: false,
      })
    } catch (err) {
      console.warn('[proctoring] face detection unavailable — continuing without it', err)
      this.landmarker = null
      return
    }
    if (this.stopped) {
      this.landmarker?.close()
      this.landmarker = null
      return
    }
    this.loop()
  }

  stop(): void {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.landmarker?.close()
    this.landmarker = null
  }

  /** Temporarily suspend the detection loop without tearing down the model. Used to stop
   * MediaPipe from competing for the main thread while the agent's in-browser TTS is actively
   * scheduling audio — running both at once (heaviest with 2+ faces tracked) starves the audio
   * scheduler and makes the agent voice choppy. Proctoring resumes for the candidate's turn,
   * which is when it actually matters. Resetting the in-progress condition windows means the
   * paused gap never counts toward a sustained detection. */
  setPaused(paused: boolean): void {
    if (this.paused === paused) return
    this.paused = paused
    if (paused) {
      for (const kind of Object.keys(this.cond) as CheatKind[]) {
        this.cond[kind].since = null
        this.cond[kind].emitted = false
      }
    }
  }

  private loop = (): void => {
    if (this.stopped || !this.landmarker) return
    if (!this.paused) {
      try {
        this.detectOnce()
      } catch (err) {
        // A single bad frame must never kill the loop or the interview.
        console.warn('[proctoring] frame skipped', err)
      }
    }
    this.timer = setTimeout(this.loop, DETECT_INTERVAL_MS)
  }

  private detectOnce(): void {
    const video = this.video
    if (!this.landmarker || video.readyState < 2 || video.videoWidth === 0) return
    // FaceLandmarker in VIDEO mode requires strictly increasing timestamps; skip duplicate frames.
    if (video.currentTime === this.lastVideoTime) return
    this.lastVideoTime = video.currentTime

    const result = this.landmarker.detectForVideo(video, performance.now())
    const faceCount = result.faceLandmarks?.length ?? 0

    const now = performance.now()
    if (faceCount === 0) {
      this.evaluate('no_face', true, now)
      this.evaluate('multiple_faces', false, now)
      this.evaluate('looking_away', false, now)
      this.evaluate('head_turned', false, now)
      return
    }

    this.evaluate('no_face', false, now)
    this.evaluate('multiple_faces', faceCount > 1, now)

    // Gaze / head-pose only make sense for a single tracked face.
    if (faceCount === 1) {
      this.evaluate('looking_away', this.isLookingAway(result), now)
      this.evaluate('head_turned', this.isHeadTurned(result), now)
    } else {
      this.evaluate('looking_away', false, now)
      this.evaluate('head_turned', false, now)
    }
  }

  private isLookingAway(result: FaceLandmarkerResult): boolean {
    const shapes = result.faceBlendshapes?.[0]?.categories
    if (!shapes) return false
    const score = (name: string) => shapes.find((c) => c.categoryName === name)?.score ?? 0
    // Combine the two eyes into a single direction magnitude in each axis.
    const right = (score('eyeLookOutRight') + score('eyeLookInLeft')) / 2
    const left = (score('eyeLookOutLeft') + score('eyeLookInRight')) / 2
    const up = (score('eyeLookUpLeft') + score('eyeLookUpRight')) / 2
    const down = (score('eyeLookDownLeft') + score('eyeLookDownRight')) / 2
    return Math.max(right, left, up, down) > GAZE_THRESHOLD
  }

  private isHeadTurned(result: FaceLandmarkerResult): boolean {
    const lm = result.faceLandmarks?.[0]
    if (!lm) return false
    const nose = lm[NOSE_TIP]
    const cheekL = lm[CHEEK_LEFT]
    const cheekR = lm[CHEEK_RIGHT]
    if (!nose || !cheekL || !cheekR) return false
    const dLeft = Math.abs(nose.x - cheekL.x)
    const dRight = Math.abs(cheekR.x - nose.x)
    const span = dLeft + dRight
    if (span <= 0) return false
    // 0.5 = nose centred between the cheeks (facing forward); deviation = head yawed left/right.
    const ratio = dLeft / span
    return Math.abs(ratio - 0.5) > YAW_DEVIATION_THRESHOLD
  }

  /** Per-condition debounce: emit once when a condition has held continuously for its sustain
   * window; re-arm only after it clears, so a later recurrence is logged as a fresh event. */
  private evaluate(kind: CheatKind, active: boolean, now: number): void {
    const state = this.cond[kind]
    if (!active) {
      state.since = null
      state.emitted = false
      return
    }
    if (state.since === null) state.since = now
    if (!state.emitted && now - state.since >= SUSTAIN_MS[kind]) {
      state.emitted = true
      this.onFlag({ kind, detail: DETAIL[kind] })
    }
  }
}
