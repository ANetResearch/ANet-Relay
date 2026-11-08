import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { useLangStore } from '@/i18n'
import { KeyMigrationNotice } from './KeyMigration'
import { COPILOT_DICTS } from './strings'

describe('key migration notice (06 §9)', () => {
  it('shows the contract copy and both choices', () => {
    useLangStore.setState({ lang: 'en' })
    const html = renderToStaticMarkup(<KeyMigrationNotice onEnter={() => {}} onKeep={() => {}} />)
    expect(html).toContain('Your saved key is protected by the system keychain. Enter it again to store it in the app data folder, or keep using the keychain.')
    expect(html).toContain('Enter key again')
    expect(html).toContain('Keep using keychain')
    expect(html).toContain('data-copilot-key-reenter')
    expect(html).toContain('data-copilot-key-keep')
  })

  it('states the storage plainly in both languages', () => {
    expect(COPILOT_DICTS.en['cps.keyStorage']).toBe('Store keys in the system keychain')
    expect(COPILOT_DICTS.en['cps.keyStorage.file']).toBe('Stored in the app data folder and readable only by your user account.')
    expect(COPILOT_DICTS.zh['cps.keyStorage.file']).toBe('保存在应用数据目录，仅当前系统用户可读。')
    expect(COPILOT_DICTS.zh['cps.keyPassword']).toContain('不需要系统登录密码')
  })
})
