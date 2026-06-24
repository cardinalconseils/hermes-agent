import { useEffect, useRef } from 'react'

/**
 * Canvas star-shower layered over a freshly hatched pet — a burst of crisp
 * pixel sparkles that rain down, drift, and twinkle in the theme accent. Replaces
 * the old radial-glow flash; reads as a Pokémon-style hatch celebration.
 *
 * Sized to its container (absolute inset-0, pointer-events: none) and disabled
 * under `prefers-reduced-motion`.
 */

interface Star {
  x: number
  y: number
  vx: number
  vy: number
  size: number
  rot: number
  vrot: number
  phase: number // twinkle
  twinkle: number
  life: number
  ttl: number
}

const BURST = 16 // stars dropped at reveal
const MAX = 40
const GRAVITY = 26 // px/s² — a lazy drift, not a fall
const SPAWN_MS = 150 // ambient trickle after the burst

function readAccent(el: HTMLElement): string {
  const c = getComputedStyle(el).getPropertyValue('--ui-accent').trim()
  return c || '#9aa0ff'
}

function diamond(ctx: CanvasRenderingContext2D, rx: number, ry: number): void {
  ctx.beginPath()
  ctx.moveTo(0, -ry)
  ctx.lineTo(rx, 0)
  ctx.lineTo(0, ry)
  ctx.lineTo(-rx, 0)
  ctx.closePath()
  ctx.fill()
}

export function PetStarShower() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    const parent = canvas?.parentElement
    if (!canvas || !ctx || !parent) {
      return
    }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      return
    }

    const accent = readAccent(canvas)
    const dpr = Math.min(window.devicePixelRatio || 1, 3)
    let w = 0
    let h = 0
    const resize = () => {
      const r = parent.getBoundingClientRect()
      w = r.width
      h = r.height
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(parent)

    const stars: Star[] = []
    const spawn = (burst: boolean): void => {
      if (stars.length >= MAX) {
        return
      }
      const size = 3 + Math.random() * 5
      stars.push({
        x: Math.random() * w,
        y: burst ? Math.random() * h * 0.5 : -8,
        vx: (Math.random() - 0.5) * 16,
        vy: 10 + Math.random() * 26,
        size,
        rot: Math.random() * Math.PI,
        vrot: (Math.random() - 0.5) * 1.6,
        phase: Math.random() * Math.PI * 2,
        twinkle: 4 + Math.random() * 4,
        life: 0,
        ttl: 1.1 + Math.random() * 1.1
      })
    }
    for (let i = 0; i < BURST; i++) {
      spawn(true)
    }

    let raf = 0
    let last = performance.now()
    let acc = 0

    const tick = (now: number) => {
      raf = requestAnimationFrame(tick)
      const ms = now - last
      last = now
      const dt = Math.min(0.05, ms / 1000)
      acc += ms
      if (acc >= SPAWN_MS) {
        acc = 0
        spawn(false)
      }

      ctx.clearRect(0, 0, w, h)
      for (let i = stars.length - 1; i >= 0; i--) {
        const s = stars[i]
        s.life += dt
        s.vy += GRAVITY * dt
        s.x += s.vx * dt
        s.y += s.vy * dt
        s.rot += s.vrot * dt
        s.phase += s.twinkle * dt
        if (s.life >= s.ttl || s.y > h + 12) {
          stars.splice(i, 1)
          continue
        }
        // Twinkle, with a quick fade in/out across the star's life.
        const fade = Math.min(1, s.life * 5, (s.ttl - s.life) * 3)
        const alpha = fade * (0.45 + 0.55 * Math.abs(Math.sin(s.phase)))

        ctx.save()
        ctx.globalAlpha = alpha
        ctx.translate(Math.round(s.x), Math.round(s.y))
        ctx.rotate(s.rot)
        // 4-point sparkle: crossed accent spikes + a white pixel core.
        ctx.fillStyle = accent
        diamond(ctx, s.size, s.size * 0.3)
        diamond(ctx, s.size * 0.3, s.size)
        const core = Math.max(1, Math.round(s.size * 0.34))
        ctx.fillStyle = '#fff'
        ctx.fillRect(-core / 2, -core / 2, core, core)
        ctx.restore()
      }
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [])

  return <canvas className="pointer-events-none absolute inset-0 z-10" ref={canvasRef} />
}
