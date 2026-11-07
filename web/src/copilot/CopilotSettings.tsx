// Settings → Copilot (03 §16): endpoint, model and API key of the built-in agent. The key is written once and
// never read back into the renderer: the shell only reports whether one is stored and its last four characters.
import * as React from 'react'
import type { CopilotApi, CopilotConfig } from '@/api'
import { notify } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { useCopilot } from './store'
import { useCPT, type CopilotKey } from './strings'

type RowComponent = React.ComponentType<{ label: string; hint?: React.ReactNode; children: React.ReactNode; wide?: boolean }>

const APIS: CopilotApi[] = ['openai-completions', 'anthropic-messages', 'openai-responses']

export function CopilotSettings({ Row }: { Row: RowComponent }) {
  const t = useCPT()
  const bridge = typeof window !== 'undefined' ? window.relay?.copilot : undefined
  const [cfg, setCfg] = React.useState<CopilotConfig | null>(null)
  const [api, setApi] = React.useState<CopilotApi>('openai-completions')
  const [baseUrl, setBaseUrl] = React.useState('')
  const [model, setModel] = React.useState('')
  const [key, setKey] = React.useState('')
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
      setKey('')
      notify(t('cps.saved'))
      return true
    } catch (e) {
      setResult(String(e instanceof Error ? e.message : e).replace(/^Error invoking remote method '[^']+': (Error: )?/, ''))
      return false
    } finally {
      setBusy(null)
    }
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
      {cfg && !cfg.secureStorage ? <p className="py-1 text-hud-sub text-[var(--g400)]">{t('cps.keyInsecure')}</p> : null}
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
