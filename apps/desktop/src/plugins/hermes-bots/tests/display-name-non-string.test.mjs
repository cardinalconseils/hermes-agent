import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// A bot row whose `name` arrives as a non-string (numeric id from a remote
// roster, or a null-ish object from a half-populated Cloud row) crashed the
// whole roster render: `.trim()` and `.replace()` are String methods, so the
// first one displayName reached threw TypeError and took the pane with it.
//
// 06300c6b3e hardened the two `name === 'default'` comparisons but left the
// fallthrough `raw` assignment reading the raw value — and THAT is the line
// every non-default bot goes through. Contract pinned here: every read of
// bot.name/bot.title in displayName coerces before calling a String method.

const pluginSource = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')

const displayNameSource = pluginSource.slice(
  pluginSource.indexOf('function displayName'),
  pluginSource.indexOf('function filterBots')
)

// executable check: evaluate displayName against a stub alias resolver
const displayName = new Function(
  'aliasIdentityFor',
  `${displayNameSource}; return displayName`
)(() => null)

test('a non-string bot name renders instead of throwing', () => {
  assert.equal(displayName({ name: 12345, title: null }, null), '12345')
  assert.equal(displayName({ name: 42, title: 0 }, null), '42')
})

test('a non-string title falls through the same coercion', () => {
  assert.equal(displayName({ name: 'ops', title: 7 }, null), '7')
})

test('the ordinary string paths are unchanged', () => {
  assert.equal(displayName({ name: 'rank-and-rent' }, null), 'Rank And Rent')
  assert.equal(displayName({ name: 'default' }, null), 'Hermes')
  assert.equal(displayName({ name: 'ops' }, { title: '  Trader  ' }), 'Trader')
  assert.equal(displayName({ name: 'ops', display_name: 'Ops Bot' }, null), 'Ops Bot')
})

test('no read of name or title reaches a String method uncoerced', () => {
  // guards the regression at its root: the whole function, not one line
  // lookbehind excludes the coerced `String(bot?.name || '')` form
  const uncoerced = displayNameSource.match(/(?<!String)\(bot\??\.(name|title)[^)]*\)\s*\.\s*(trim|replace|toLowerCase)/g)
  assert.equal(uncoerced, null, `uncoerced String-method call on bot.name/title: ${uncoerced}`)
})
