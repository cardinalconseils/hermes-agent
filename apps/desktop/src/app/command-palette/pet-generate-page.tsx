/**
 * Cmd-K → Pets → "Generate" page — describe a pet, pick a draft, hatch it.
 *
 * A thin view over the `pet-generate` store. The palette search box doubles as
 * the concept prompt; this page renders the variant grid, the selection, the
 * retry/hatch actions, and the loading states. The store owns the two-step
 * `pet.generate` → `pet.hatch` flow.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { DiffusionCanvas } from '@/components/chat/image-generation-placeholder'
import { PetEggHatch, PetHatchSparkles } from '@/components/pet/pet-egg-hatch'
import { PetSprite } from '@/components/pet/pet-sprite'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Check, Egg, Loader2, PawPrint, RefreshCw, Sparkles } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { closeCommandPalette } from '@/store/command-palette'
import { type PetInfo } from '@/store/pet'
import {
  $petGenDrafts,
  $petGenError,
  $petGenPreview,
  $petGenSelected,
  $petGenStatus,
  adoptHatched,
  discardHatched,
  generateDrafts,
  hatchSelected
} from '@/store/pet-generate'

const VARIANT_COUNT = 4

// Fixed render scale for the preview so it's a predictable size regardless of
// the user's configured `display.pet.scale`.
const PREVIEW_SCALE = 0.7

// Fallback row order if a backend doesn't return `stateRows`.
const PREVIEW_ROWS = ['idle', 'waving', 'running-right', 'running-left', 'running', 'review', 'jumping', 'failed']
const PREVIEW_STATE_MS = 1500

const ROW_TO_FRAME_KEY: Record<string, string> = {
  idle: 'idle',
  wave: 'wave',
  waving: 'wave',
  jump: 'jump',
  jumping: 'jump',
  run: 'run',
  running: 'run',
  'running-right': 'run',
  'running-left': 'run',
  failed: 'failed',
  review: 'review',
  waiting: 'waiting'
}

function frameCountForRow(pet: PetInfo, row: string): number {
  const byState = pet.framesByState
  const mapped = ROW_TO_FRAME_KEY[row]
  return byState?.[row] ?? (mapped ? byState?.[mapped] : undefined) ?? pet.framesPerState ?? 0
}

interface PetGeneratePageProps {
  search: string
}

export function PetGeneratePage({ search }: PetGeneratePageProps) {
  const { t } = useI18n()
  const copy = t.commandCenter.generatePet
  const { requestGateway } = useGatewayRequest()

  const status = useStore($petGenStatus)
  const error = useStore($petGenError)
  const drafts = useStore($petGenDrafts)
  const selected = useStore($petGenSelected)
  const preview = useStore($petGenPreview)
  const [name, setName] = useState('')

  const prompt = search.trim()
  const busy = status === 'generating' || status === 'hatching'

  const generate = () => {
    if (prompt) {
      void generateDrafts(requestGateway, { prompt })
    }
  }

  const hatch = () => {
    void hatchSelected(requestGateway, { name: name.trim() || prompt, prompt })
  }

  const adopt = () => {
    void adoptHatched(requestGateway).then(out => {
      if (out.ok) {
        triggerHaptic('crisp')
        closeCommandPalette()
      }
    })
  }

  if (status === 'stale') {
    return <Status text={copy.staleBackend} tone="error" />
  }

  // Hatching is slow (several grounded image generations) — own the whole pane
  // with the egg-incubation beat instead of a bare spinner.
  if (status === 'hatching') {
    return <PetEggHatch subtitle={copy.hatchingSub} title={copy.hatching} />
  }

  // Preview: play every animation row before the user commits.
  if ((status === 'preview' || status === 'adopting') && preview) {
    return (
      <HatchPreview
        adopting={status === 'adopting'}
        error={error}
        onAdopt={adopt}
        onDiscard={() => void discardHatched(requestGateway)}
        pet={preview}
      />
    )
  }

  const hasDrafts = drafts.length > 0
  const generating = status === 'generating'
  // While generating, render a fixed grid of slots that fill in as drafts stream
  // back (pet.generate.progress); empty slots animate the diffusion placeholder.
  const slots = generating
    ? Array.from({ length: VARIANT_COUNT }, (_, i) => drafts.find(draft => draft.index === i) ?? null)
    : drafts

  return (
    <div className="flex flex-col gap-2 p-2">
      {error && <p className="px-1 text-[0.6875rem] text-(--ui-red)">{error}</p>}

      {!hasDrafts && !generating && (
        <p className="px-1 py-1 text-xs text-muted-foreground">{prompt ? copy.readyHint : copy.promptHint}</p>
      )}

      {generating && (
        <div className="flex items-center justify-between px-1 text-[0.6875rem] text-muted-foreground">
          <span className="shimmer">{copy.generating}</span>
          <span className="tabular-nums">
            {drafts.length}/{VARIANT_COUNT}
          </span>
        </div>
      )}

      {(hasDrafts || generating) && (
        <div className="grid grid-cols-2 gap-2">
          {slots.map((draft, i) => {
            const isSelected = !generating && draft != null && selected === draft.index

            return (
              <button
                className={cn(
                  'relative flex aspect-square items-center justify-center overflow-hidden rounded-lg border bg-(--ui-bg-quinary) transition-colors',
                  isSelected
                    ? 'border-(--ui-accent) ring-2 ring-(--ui-accent)/40'
                    : draft != null
                      ? 'border-(--ui-stroke-tertiary) hover:border-foreground/40'
                      : 'border-(--ui-stroke-tertiary)'
                )}
                disabled={generating || busy || draft == null}
                key={draft ? `draft-${draft.index}` : `slot-${i}`}
                onClick={() => draft != null && $petGenSelected.set(draft.index)}
                onMouseDown={event => event.preventDefault()}
                type="button"
              >
                {draft != null ? (
                  <img alt="" className="size-full object-contain" draggable={false} src={draft.dataUri} />
                ) : (
                  <DiffusionCanvas />
                )}
                {isSelected && (
                  <span className="absolute right-1 top-1 rounded-full bg-(--ui-accent) p-0.5 text-(--ui-base)">
                    <Check className="size-3" />
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {hasDrafts ? (
        <div className="flex flex-col gap-2">
          <input
            className="w-full rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1.5 text-xs outline-none placeholder:text-muted-foreground focus:border-foreground/40"
            onChange={event => setName(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                hatch()
              }
            }}
            placeholder={copy.namePlaceholder}
            value={name}
          />
          <div className="flex gap-2">
            <button
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-xs font-medium transition-colors hover:bg-(--chrome-action-hover) disabled:opacity-50"
              disabled={busy || !prompt}
              onClick={generate}
              onMouseDown={event => event.preventDefault()}
              type="button"
            >
              <RefreshCw className="size-3.5" />
              {copy.retry}
            </button>
            <button
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-primary px-2 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              disabled={busy || selected === null}
              onClick={hatch}
              onMouseDown={event => event.preventDefault()}
              type="button"
            >
              <PawPrint className="size-3.5" />
              {copy.hatch}
            </button>
          </div>
        </div>
      ) : (
        <button
          className="flex items-center justify-center gap-1.5 rounded-md bg-primary px-2 py-2 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          disabled={busy || !prompt}
          onClick={generate}
          onMouseDown={event => event.preventDefault()}
          type="button"
        >
          {generating ? <Loader2 className="size-3.5 animate-spin" /> : <Egg className="size-3.5" />}
          {generating ? copy.generating : copy.generate}
        </button>
      )}
    </div>
  )
}

interface HatchPreviewProps {
  pet: PetInfo
  adopting: boolean
  error: string | null
  onAdopt: () => void
  onDiscard: () => void
}

function HatchPreview({ pet, adopting, error, onAdopt, onDiscard }: HatchPreviewProps) {
  const { t } = useI18n()
  const copy = t.commandCenter.generatePet
  const [stateIndex, setStateIndex] = useState(0)
  const previewRows = (pet.stateRows?.length ? pet.stateRows : PREVIEW_ROWS).filter(row => frameCountForRow(pet, row) > 0)
  const rows = previewRows.length > 0 ? previewRows : ['idle']
  const activeRow = rows[stateIndex % rows.length] ?? 'idle'

  // Cycle through the animation rows so the preview showcases all frames.
  useEffect(() => {
    const id = setInterval(() => {
      setStateIndex(i => (i + 1) % rows.length)
    }, PREVIEW_STATE_MS)

    return () => clearInterval(id)
  }, [rows.length])

  useEffect(() => {
    setStateIndex(0)
  }, [pet.slug])

  // Celebrate the reveal — fires once per hatched pet.
  useEffect(() => {
    triggerHaptic('crisp')
  }, [pet.slug])

  const previewInfo: PetInfo = { ...pet, scale: PREVIEW_SCALE }

  return (
    <div className="flex flex-col items-center gap-2 p-2">
      <div className="relative flex min-h-[9rem] w-full items-center justify-center overflow-hidden rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) py-2">
        <PetHatchSparkles />
        <div className="pet-reveal">
          <PetSprite info={previewInfo} rowOverride={activeRow} />
        </div>
      </div>

      <p className="flex items-center gap-1 text-xs font-semibold text-(--ui-accent)">
        <Sparkles className="size-3" />
        {copy.hatched}
      </p>

      {pet.displayName && <p className="text-xs font-medium text-foreground">{pet.displayName}</p>}

      {error && <p className="px-1 text-[0.6875rem] text-(--ui-red)">{error}</p>}

      <div className="flex w-full gap-2">
        <button
          className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-xs font-medium transition-colors hover:bg-(--chrome-action-hover) disabled:opacity-50"
          disabled={adopting}
          onClick={onDiscard}
          onMouseDown={event => event.preventDefault()}
          type="button"
        >
          <RefreshCw className="size-3.5" />
          {copy.startOver}
        </button>
        <button
          className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-primary px-2 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          disabled={adopting}
          onClick={onAdopt}
          onMouseDown={event => event.preventDefault()}
          type="button"
        >
          {adopting ? <Loader2 className="size-3.5 animate-spin" /> : <PawPrint className="size-3.5" />}
          {copy.adopt}
        </button>
      </div>
    </div>
  )
}

function Status({ icon, text, tone }: { icon?: React.ReactNode; text: string; tone?: 'error' }) {
  return (
    <div
      className={cn(
        'flex items-center justify-center gap-2 px-2 py-6 text-xs',
        tone === 'error' ? 'text-(--ui-red)' : 'text-muted-foreground'
      )}
    >
      {icon}
      {text}
    </div>
  )
}
