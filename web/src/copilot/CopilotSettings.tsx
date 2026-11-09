// Settings → Copilot (03 §16): endpoint, model and API key of the built-in agent. The key is written once and
// never read back into the renderer: the shell only reports whether one is stored and its last four characters.
// Storage (06 §9): the app data folder by default, the system keychain only when the operator turns it on.
import * as React from 'react'
import type { CopilotApi, CopilotConfig, CopilotThinking, CopilotToolScope } from '@/api'
import { notify } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Slider } from '@/ui/components/ui/slider'
import { Switch } from '@/ui/components/ui/switch'
import { KeyMigrationNotice } from './KeyMigration'
import { useCopilot } from './store'
import { useCPT, type CopilotKey } from './strings'

type RowComponent = React.ComponentType<{ label: string; hint?: React.ReactNode; children: React.ReactNode; wide?: boolean }>

const APIS: CopilotApi[] = ['openai-completions', 'anthropic-messages', 'openai-responses']
const SCOPES: CopilotToolScope[] = ['flight', 'flight+device', 'all']
const OPACITY_RANGE = [60, 100] as const
const THINKING: CopilotThinking[] = ['auto', 'off', 'low', 'high']
const MAX_TOKENS_RANGE = [1024, 131072] as const

export function CopilotSettings({ Row }: { Row: RowComponent }) {
  const t = useCPT()
  const bridge = typeof window !== 'undefined' ? window.relay?.copilot : undefined
  const [cfg, setCfg] = React.useState<CopilotConfig | null>(null)
  const [api, setApi] = React.useState<CopilotApi>('openai-completions')
  const [baseUrl, setBaseUrl] = React.useState('')
  const [model, setModel] = React.useState('')
  const [key, setKey] = React.useState('')
  const [opacity, setOpacity] = React.useState(100)
  const [maxTokens, setMaxTokens] = React.useState('16384')
  const [busy, setBusy] = React.useState<'save' | 'test' | null>(null)
  const [result, setResult] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!bridge) return
    let alive = true
    bridge.getConfig().then((c) => {
      if (!alive) return
      setCfg(c)
      setApi(c.api)
      setBaseUrl(c.baseUrl)
      setModel(c.model)
      setOpacity(c.opacity)
      setMaxTokens(String(c.maxTokens))
    }, () => {})
    return () => {
      alive = false
    }
  }, [bridge])

  if (!bridge) return <p className="text-hud-sub text-[var(--g500)]">{t('cp.desktopOnly')}</p>

  const save = async (patch: Parameters<typeof bridge.saveConfig>[0]) => {
    setBusy('save')
    setResult(null)
    try {
      const next = await bridge.saveConfig(patch)
      setCfg(next)
      useCopilot.getState().setConfig(next)
      if (Object.hasOwn(patch, 'apiKey')) setKey('')
      setMaxTokens(String(next.maxTokens))
      notify(t('cps.saved'))
      return true
    } catch (e) {
      setResult(String(e instanceof Error ? e.message : e).replace(/^Error invoking remote method '[^']+': (Error: )?/, ''))
      return false
    } finally {
      setBusy(null)
    }
  }
  const commitMaxTokens = () => {
    const n = Math.round(Number(maxTokens))
    if (!Number.isFinite(n) || n === cfg?.maxTokens) return setMaxTokens(String(cfg?.maxTokens ?? 16384))
    void save({ maxTokens: Math.min(MAX_TOKENS_RANGE[1], Math.max(MAX_TOKENS_RANGE[0], n)) })
  }
  const test = async () => {
    if (!(await save({ api, baseUrl, model, ...(key ? { apiKey: key } : {}) }))) return
    setBusy('test')
    const r = await bridge.test()
    setBusy(null)
    setResult(r.ok ? t('cps.testOk', { ms: r.ms ?? 0 }) : t('cps.testFail', { error: r.error ?? '' }))
  }

  return (
    <>
      <Row label={t('cps.api')}>
        <NativeSelect value={api} onChange={(e) => setApi(e.target.value as CopilotApi)} className="w-[260px]" aria-label={t('cps.api')} data-copilot-api="">
          {APIS.map((a) => (
            <NativeSelectOption key={a} value={a}>
              {t(`cps.api.${a}` as CopilotKey)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Row>
      <Row label={t('cps.baseUrl')} hint={t('cps.baseUrlHint')} wide>
        <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" spellCheck={false} className="h-7 font-mono text-[12px]" aria-label={t('cps.baseUrl')} data-copilot-base="" />
      </Row>
      <Row label={t('cps.model')} hint={t('cps.modelHint')}>
        <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-5.5" spellCheck={false} className="h-7 w-[260px] font-mono text-[12px]" aria-label={t('cps.model')} data-copilot-model="" />
      </Row>
      <Row label={t('cps.thinking')} hint={t('cps.thinkingHint')} wide>
        <NativeSelect
          value={cfg?.thinking ?? 'auto'}
          onChange={(e) => void save({ thinking: e.target.value as CopilotThinking })}
          className="w-[260px]"
          aria-label={t('cps.thinking')}
          data-copilot-thinking=""
        >
          {THINKING.map((th) => (
            <NativeSelectOption key={th} value={th}>
              {t(`cps.thinking.${th}` as CopilotKey)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Row>
      <Row label={t('cps.maxTokens')} hint={t('cps.maxTokensHint')} wide>
        <Input
          type="number"
          inputMode="numeric"
          min={MAX_TOKENS_RANGE[0]}
          max={MAX_TOKENS_RANGE[1]}
          step={1024}
          value={maxTokens}
          onChange={(e) => setMaxTokens(e.target.value)}
          onBlur={commitMaxTokens}
          onKeyDown={(e) => e.key === 'Enter' && commitMaxTokens()}
          className="num h-7 w-[140px] text-[12px]"
          aria-label={t('cps.maxTokens')}
          data-copilot-max-tokens=""
        />
      </Row>
      <Row label={t('cps.scope')} hint={t('cps.scopeHint')} wide>
        <NativeSelect
          value={cfg?.toolScope ?? 'all'}
          onChange={(e) => void save({ toolScope: e.target.value as CopilotToolScope })}
          className="w-[260px]"
          aria-label={t('cps.scope')}
          data-copilot-scope=""
        >
          {SCOPES.map((sc) => (
            <NativeSelectOption key={sc} value={sc}>
              {t(`cps.scope.${sc}` as CopilotKey)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Row>
      <Row label={t('cps.autoApprove')} hint={t('cps.autoApproveHint')} wide>
        <Switch checked={cfg?.autoApprove === true} onCheckedChange={(c) => void save({ autoApprove: Boolean(c) })} aria-label={t('cps.autoApprove')} data-copilot-auto-approve="" />
      </Row>
      <Row label={t('cps.opacity')} hint={t('cps.opacityHint')}>
        <Slider
          min={OPACITY_RANGE[0]}
          max={OPACITY_RANGE[1]}
          step={5}
          value={opacity}
          onValueChange={(v) => setOpacity(Array.isArray(v) ? (v[0] as number) : (v as number))}
          onValueCommitted={(v) => void save({ opacity: Array.isArray(v) ? (v[0] as number) : (v as number) })}
          className="w-[200px]"
          aria-label={t('cps.opacity')}
          data-copilot-opacity=""
        />
        <span className="num w-10 flex-none text-right text-[11px] text-[var(--g200)]">{opacity} %</span>
      </Row>
      <Row
        label={t('cps.key')}
        hint={cfg?.hasKey ? (cfg.keyHint ? t('cps.keyStored', { hint: cfg.keyHint }) : t('cps.keyStoredShort')) : t('cps.keyNone')}
      >
        <Input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={cfg?.hasKey ? t('cps.keyPlaceholder') : 'sk-…'}
          autoComplete="off"
          spellCheck={false}
          className="h-7 w-[200px] font-mono text-[12px]"
          aria-label={t('cps.key')}
          data-copilot-key=""
        />
        {cfg?.hasKey ? (
          <Button variant="ghost" size="sm" disabled={busy !== null} onClick={() => void save({ apiKey: null })}>
            {t('cps.keyClear')}
          </Button>
        ) : null}
      </Row>
      {cfg?.keyMigration ? (
        <KeyMigrationNotice onEnter={() => document.querySelector<HTMLInputElement>('[data-copilot-key]')?.focus()} onKeep={() => void save({ keyStorage: 'keychain' })} busy={busy !== null} />
      ) : null}
      <Row label={t('cps.keyStorage')} hint={t('cps.keyStorageHint')} wide>
        <Switch
          checked={cfg?.keyStorage === 'keychain'}
          disabled={busy !== null || !cfg}
          onCheckedChange={(v) => void save({ keyStorage: v ? 'keychain' : 'file' })}
          aria-label={t('cps.keyStorage')}
          data-copilot-keychain=""
        />
      </Row>
      <p className="py-1 text-hud-sub text-[var(--g400)]" data-copilot-key-storage={cfg?.keyStorage ?? 'file'}>
        {t(cfg?.keyStorage === 'keychain' ? 'cps.keyStorage.keychain' : 'cps.keyStorage.file')} {t('cps.keyPassword')}
      </p>
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => void save({ api, baseUrl, model, ...(key ? { apiKey: key } : {}) })} data-copilot-save="">
          {t('cps.save')}
        </Button>
        <Button variant="outline" size="sm" disabled={busy !== null || !baseUrl || !model} onClick={() => void test()} data-copilot-test="">
          {busy === 'test' ? t('cps.testing') : t('cps.test')}
        </Button>
        {result ? <span className="text-hud-sub text-[var(--g300)]" data-copilot-result="">{result}</span> : null}
      </div>
      <p className="text-hud-sub text-[var(--g500)]">{t('cps.privacy')}</p>
    </>
  )
}
