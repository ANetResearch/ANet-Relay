// Top-bar word mark (02 §4.2): the ANet mark reversed to a single colour (height 14) + 1 px g700 rule + "Relay"
// (13 / 560 / g100, tracking -0.01em). The mono mark is derived from the brand SVG at build time: the black badge
// body is dropped and every brand colour becomes currentColor, so the mark follows the text colour.
import logoSvg from '../../../public/brand/anet-logo.svg?raw'
import { cn } from '@/lib/utils'

export function monoLogo(svg: string, idPrefix: string): string {
  return svg
    .replace(/\s(width|height)="\d+"/g, '')
    .replace(/mask0_57_38/g, `${idPrefix}-mask`)
    .replace(/fill="black"/g, 'fill="none"')
    .replace(/stroke="black"/g, 'stroke="none"')
    .replace(/(fill|stroke)="(#[0-9A-Fa-f]{6}|white)"/g, '$1="currentColor"')
    .replace('<svg', '<svg aria-hidden="true" focusable="false" style="display:block;height:100%;width:auto"')
}

const MONO = monoLogo(logoSvg, 'wordmark')

export function AnetMark({ className }: { className?: string }) {
  return <span className={cn('inline-block h-3.5', className)} dangerouslySetInnerHTML={{ __html: MONO }} />
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex h-full items-center gap-2.5', className)} aria-label="ANet Relay">
      <AnetMark className="text-[var(--g50)]" />
      <span className="h-3.5 w-px bg-[var(--g700)]" aria-hidden="true" />
      <span className="text-hud-title font-[560] tracking-[-0.01em] text-[var(--g100)]">Relay</span>
    </span>
  )
}
